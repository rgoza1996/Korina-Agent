"""STT HTTP endpoints and streaming event formatting."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from functools import partial
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from korina.config import (
    config_stt_llm_api_env,
    config_stt_llm_base_url,
    config_stt_llm_chat_url,
    load_config,
)
from korina.services.audio_probe import maybe_fallback_to_whisper
from korina.services.multimodal_stt import (
    lmstudio_transcribe_wav,
    transient_whisper_fallback_reason,
)
from korina.services.whisper_service import (
    audio_metadata,
    convert_to_16k_wav,
    sse_event,
    transcribe_upload_file,
    transcribe_wav_segments,
    whisper_payload,
)
from korina.util.paths import LMSTUDIO_MODEL, PARTIAL_MIN_SECONDS

router = APIRouter()


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


def _multimodal_payload(wav: Path, *, device, model, llm_model, started, metadata) -> dict:
    """Resolve cached capability failures, then attempt multimodal STT once."""
    config = load_config()
    probe_base = config_stt_llm_base_url(config)
    stt_chat_url = config_stt_llm_chat_url(config)
    whisper_fallback = partial(
        _whisper_fallback_payload, wav, device=device, model_id=model,
        started=started, metadata=metadata,
    )
    probe = partial(
        maybe_fallback_to_whisper,
        provider=config.get('stt_llm_provider') or config.get('llm_provider') or 'llama.cpp',
        base_url=probe_base, model=llm_model or '',
        whisper_fallback_fn=whisper_fallback,
    )
    # A known incompatible model must not incur another multimodal round trip.
    used_fallback, payload = probe(stt_status_code=0, stt_response_body='')
    if used_fallback:
        return payload
    try:
        result = lmstudio_transcribe_wav(
            wav, model=llm_model, base_url=stt_chat_url,
            api_env=config_stt_llm_api_env(config),
        )
    except Exception as exc:
        return {
            **whisper_fallback(), 'warning': str(exc),
            'fallback_reason': 'multimodal_call_failed',
        }

    text = str(result.get('text') or '').strip()
    finish_reason = str(result.get('finish_reason') or '')
    if not text:
        # Preserve structural-empty detection and its persistent probe cache.
        body = json.dumps({'choices': [{
            'finish_reason': finish_reason, 'message': {'content': text},
        }]})
        used_fallback, payload = probe(
            stt_status_code=200, stt_response_body=body,
            finish_reason=finish_reason,
        )
        if used_fallback:
            return payload
        detail = result.get('error') or 'empty_text'
        reason = transient_whisper_fallback_reason(detail)
        if reason:
            return {**whisper_fallback(), 'warning': detail, 'fallback_reason': reason}
    return {
        **result, **metadata, 'text': text,
        'seconds': time.time() - started,
        'model': llm_model or result.get('model') or LMSTUDIO_MODEL,
        'backend': 'multimodal-stt', 'finish_reason': finish_reason,
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
                payload = whisper_payload(
                    parts, info, metadata=metadata, started=started,
                    device=device, model_id=model,
                )
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
    return StreamingResponse(
        _stream_events(audio.file, suffix, device=device, model=model,
                       backend=backend, llm_model=llm_model),
        media_type='text/event-stream',
    )
