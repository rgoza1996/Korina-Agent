"""STT HTTP endpoints and streaming event formatting."""

from __future__ import annotations

import shutil
import tempfile
import time
from functools import partial
from pathlib import Path
from typing import Optional

from anyio import CancelScope
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from korina.config import load_config
from korina.services.multimodal_stt import lmstudio_transcribe_wav
from korina.services.stt_resolution import resolve_multimodal_transcription
from korina.services.whisper_service import (
    AudioDurationLimitError,
    WhisperBusyError,
    audio_metadata,
    convert_to_16k_wav,
    sse_event,
    transcribe_upload_file,
    transcribe_wav_segments,
    whisper_payload,
)
from korina.util.paths import LMSTUDIO_MODEL, PARTIAL_MIN_SECONDS

router = APIRouter()


class SttStreamResponse(StreamingResponse):
    """Close lazy model usage and temporary audio on disconnect or send failure."""

    def __init__(self, content, **kwargs):
        self._content = content
        super().__init__(content, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self._content.close)


def _whisper_fallback_payload(wav: Path, *, device, model_id, started, metadata) -> dict:
    # Keep VAD off for fallback: filtering can erase quiet or clipped speech.
    parts, info = transcribe_wav_segments(
        wav, vad_filter=False, device=device, model_id=model_id,
    )
    return whisper_payload(
        parts, info, metadata=metadata, started=started, device=device,
        model_id=model_id or load_config().get('stt_model'),
        backend='whisper-fallback',
    )


def _emit_payload_sse(payload: dict):
    """Emit one result as segment events followed by a compact done event."""
    fallback = payload.get('backend') == 'whisper-fallback'
    segments = payload.get('segments') or []
    if not segments and str(payload.get('text') or '').strip():
        segments = [{'start': None, 'end': None, 'text': payload['text']}]
    emitted = 0
    for index, segment in enumerate(segments, 1):
        text = str(segment.get('text') or '').strip()
        if fallback and not text:
            continue
        emitted += 1
        event = {**segment, 'text': text, 'index': index}
        if fallback:
            event['backend'] = payload['backend']
        yield sse_event('segment', event)
    yield sse_event('done', {**payload, 'segments': emitted})


def _emit_whisper_sse(parts, info, *, metadata, started, device, model):
    """Emit each lazy segment as it arrives and share the final payload builder."""
    collected = []
    try:
        for index, segment in enumerate(parts, 1):
            collected.append(segment)
            yield sse_event('segment', {
                'start': segment.start, 'end': segment.end,
                'text': segment.text.strip(), 'index': index,
            })
    finally:
        close = getattr(parts, 'close', None)
        if close is not None:
            close()
    payload = whisper_payload(
        collected, info, metadata=metadata, started=started,
        device=device, model_id=model,
    )
    yield sse_event('done', {**payload, 'segments': len(collected)})


def _multimodal_payload(wav: Path, *, device, model, llm_model, started, metadata) -> dict:
    """Resolve cached capability failures, then attempt multimodal STT once."""
    whisper_fallback = partial(
        _whisper_fallback_payload, wav, device=device, model_id=model,
        started=started, metadata=metadata,
    )
    result = resolve_multimodal_transcription(
        wav, model=llm_model, fallback=whisper_fallback,
        transcribe=lmstudio_transcribe_wav,
    )
    if result.get('backend') == 'whisper-fallback':
        return result
    text = result['text']
    return {
        **result, **metadata, 'text': text,
        'seconds': time.time() - started,
        'model': llm_model or result.get('model') or LMSTUDIO_MODEL,
        'backend': 'multimodal-stt', 'finish_reason': str(result.get('finish_reason') or ''),
        'segments': [{'start': None, 'end': None, 'text': text}] if text else [],
    }


def _stream_events(audio_file, suffix: str, *, device, model, backend, llm_model):
    """Run blocking decoding/inference in StreamingResponse's worker iterator."""
    with tempfile.TemporaryDirectory(prefix='korina-stt-stream-') as directory:
        src = Path(directory) / f'input{suffix}'
        wav = Path(directory) / 'input-16k.wav'
        started = time.time()
        try:
            with src.open('wb') as target:
                shutil.copyfileobj(audio_file, target)
            yield sse_event('status', {'message': 'converting', 'backend': 'faster-whisper'})
            convert_to_16k_wav(src, wav)
            metadata = audio_metadata(wav)
            yield sse_event('status', {
                'message': 'transcribing', 'backend': backend,
                'samples': metadata['samples'], 'sample_rate': metadata['sample_rate'],
            })
            if backend == 'llm':
                payload = _multimodal_payload(
                    wav, device=device, model=model, llm_model=llm_model,
                    started=started, metadata=metadata,
                )
            else:
                parts, info = transcribe_wav_segments(
                    wav, vad_filter=True, device=device, model_id=model,
                )
                yield from _emit_whisper_sse(
                    parts, info, metadata=metadata, started=started,
                    device=device, model=model,
                )
                return
            yield from _emit_payload_sse(payload)
        except Exception as exc:
            yield sse_event('error', {'detail': str(exc)})


@router.post('/api/transcribe')
def transcribe(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    # A sync handler puts FFmpeg, urllib and Whisper work in FastAPI's pool.
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'
    try:
        return JSONResponse(transcribe_upload_file(
            audio.file, suffix, vad_filter=True,
            device=device, model_id=model, backend=backend, llm_model=llm_model,
        ))
    except AudioDurationLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WhisperBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'Retry-After': '1'}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/api/transcribe/partial')
def transcribe_partial(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    """Transcribe a rolling snapshot of the current utterance off the event loop."""
    suffix = Path(audio.filename or 'partial.webm').suffix or '.webm'
    try:
        result = transcribe_upload_file(
            audio.file, suffix, vad_filter=False,
            device=device, model_id=model, backend=backend, llm_model=llm_model,
        )
        result.update(partial=True, stable=False)
        if result['duration'] < PARTIAL_MIN_SECONDS:
            result.update(text='', segments=[])
        return JSONResponse(result)
    except AudioDurationLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WhisperBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'Retry-After': '1'}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/api/transcribe/stream')
def transcribe_stream(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'
    return SttStreamResponse(
        _stream_events(audio.file, suffix, device=device, model=model,
                       backend=backend, llm_model=llm_model),
        media_type='text/event-stream',
    )
