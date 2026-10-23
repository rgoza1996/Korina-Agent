"""STT HTTP endpoints and streaming event formatting."""

from __future__ import annotations

import shutil
import tempfile
import time
import threading
from collections.abc import Callable
from functools import partial
from contextlib import closing
from pathlib import Path
from typing import Optional

from anyio import CancelScope
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from korina.config import load_config
from korina.runtime.json_http import RequestCancelledError
from korina.runtime.request_owner import RequestOwner, check_current
from korina.services.multimodal_stt import lmstudio_transcribe_wav
from korina.services.stt_resolution import resolve_multimodal_transcription
from korina.services.whisper_service import (
    AudioDurationLimitError,
    WhisperBusyError,
    audio_metadata,
    convert_to_16k_wav,
    owned_whisper_segments,
    sse_event,
    transcribe_upload_file,
    transcribe_wav_segments,
    whisper_payload,
)
from korina.util.paths import LMSTUDIO_MODEL, PARTIAL_MIN_SECONDS

router = APIRouter()


class _SerializedIterator:
    """Keep close behind an in-flight next, including raw ASGI task cancellation."""

    def __init__(self, content):
        self._content = content
        self._lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            return next(self._content)

    def close(self):
        with self._lock:
            self._content.close()


class SttStreamResponse(StreamingResponse):
    """Close lazy model usage and temporary audio on disconnect or send failure."""

    def __init__(self, content, *, owner: RequestOwner | None = None, **kwargs):
        self._owner = owner or RequestOwner()
        self._content = _SerializedIterator(content)
        super().__init__(self._content, **kwargs)

    async def __call__(self, scope, receive, send):
        receive = self._owner.receive(receive)
        async with self._owner.watch(Request(scope, receive)):
            try:
                await super().__call__(scope, receive, send)
            finally:
                # Retire provider HTTP before waiting for serialized cleanup.
                self._owner.retire()
                with CancelScope(shield=True):
                    await run_in_threadpool(self._content.close)


def _whisper_fallback_payload(wav: Path, *, device, model_id, started, metadata, is_current=None) -> dict:
    # Keep VAD off for fallback: filtering can erase quiet or clipped speech.
    parts, info = transcribe_wav_segments(
        wav, vad_filter=False, device=device, model_id=model_id,
        is_current=is_current,
    )
    return whisper_payload(
        parts, info, metadata=metadata, started=started, device=device,
        model_id=model_id or load_config().get('stt_model'),
        backend='whisper-fallback',
        is_current=is_current,
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


def _emit_whisper_sse(parts, info, *, metadata, started, device, model, is_current=None):
    """Emit each lazy segment as it arrives and share the final payload builder."""
    collected = []
    with closing(owned_whisper_segments(parts, is_current=is_current)) as segments:
        for index, segment in enumerate(segments, 1):
            collected.append(segment)
            yield sse_event('segment', {
                'start': segment.start, 'end': segment.end,
                'text': segment.text.strip(), 'index': index,
            })
    payload = whisper_payload(
        collected, info, metadata=metadata, started=started,
        device=device, model_id=model, is_current=is_current,
    )
    yield sse_event('done', {**payload, 'segments': len(collected)})


def _multimodal_payload(wav: Path, *, device, model, llm_model, started, metadata, is_current=None) -> dict:
    """Resolve cached capability failures, then attempt multimodal STT once."""
    whisper_fallback = partial(
        _whisper_fallback_payload, wav, device=device, model_id=model,
        started=started, metadata=metadata,
        is_current=is_current,
    )
    result = resolve_multimodal_transcription(
        wav, model=llm_model, fallback=whisper_fallback,
        transcribe=lmstudio_transcribe_wav,
        is_current=is_current,
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


def _stream_events(audio_file, *, device, model, backend, llm_model,
                   is_current: Callable[[], bool] | None = None):
    """Run blocking decoding/inference in StreamingResponse's worker iterator."""
    with tempfile.TemporaryDirectory(prefix='korina-stt-stream-') as directory:
        src = Path(directory) / 'input.audio'
        wav = Path(directory) / 'input-16k.wav'
        started = time.time()
        try:
            check_current(is_current)
            with src.open('wb') as target:
                shutil.copyfileobj(audio_file, target)
            yield sse_event('status', {'message': 'converting', 'backend': 'faster-whisper'})
            check_current(is_current)
            convert_to_16k_wav(src, wav)
            check_current(is_current)
            metadata = audio_metadata(wav)
            yield sse_event('status', {
                'message': 'transcribing', 'backend': backend,
                'samples': metadata['samples'], 'sample_rate': metadata['sample_rate'],
            })
            check_current(is_current)
            if backend == 'llm':
                payload = _multimodal_payload(
                    wav, device=device, model=model, llm_model=llm_model,
                    started=started, metadata=metadata,
                    is_current=is_current,
                )
            else:
                parts, info = transcribe_wav_segments(
                    wav, vad_filter=True, device=device, model_id=model,
                    is_current=is_current,
                )
                yield from _emit_whisper_sse(
                    parts, info, metadata=metadata, started=started,
                    device=device, model=model,
                    is_current=is_current,
                )
                return
            yield from _emit_payload_sse(payload)
        except RequestCancelledError:
            return
        except Exception as exc:
            yield sse_event('error', {'detail': str(exc)})


@router.post('/api/transcribe')
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    # Keep decoding/inference in the existing pool while observing disconnect.
    try:
        return JSONResponse(await _owned_transcription(request,
            audio.file, vad_filter=True,
            device=device, model_id=model, backend=backend, llm_model=llm_model,
        ))
    except AudioDurationLimitError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except WhisperBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={'Retry-After': '1'}) from exc
    except RequestCancelledError as exc:
        raise HTTPException(status_code=499, detail='Transcription request was cancelled') from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post('/api/transcribe/partial')
async def transcribe_partial(
    request: Request,
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    """Transcribe a rolling snapshot of the current utterance off the event loop."""
    try:
        result = await _owned_transcription(request,
            audio.file, vad_filter=False,
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
    except RequestCancelledError as exc:
        raise HTTPException(status_code=499, detail='Transcription request was cancelled') from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _owned_transcription(request: Request, *args, **kwargs) -> dict:
    owner = RequestOwner()
    async with owner.watch(request):
        result = await run_in_threadpool(transcribe_upload_file, *args, **kwargs, is_current=owner.is_current)
        check_current(owner.is_current)
        return result


@router.post('/api/transcribe/stream')
async def transcribe_stream(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    owner = RequestOwner()
    return SttStreamResponse(
        _stream_events(audio.file, device=device, model=model,
                       backend=backend, llm_model=llm_model, is_current=owner.is_current),
        owner=owner,
        media_type='text/event-stream',
    )
