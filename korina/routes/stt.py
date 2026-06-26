"""STT endpoints — transcribe, partial, stream.

Phase 1.9: inlined from the monolith's _route_transcribe* helpers.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from korina.config import config_stt_llm_api_env, config_stt_llm_base_url, config_stt_llm_chat_url, load_config
from korina.runtime import state
from korina.services.multimodal_stt import lmstudio_transcribe_wav
from korina.services.whisper_service import (
    convert_to_16k_wav,
    normalize_device,
    sse_event,
    transcribe_upload_file,
    transcribe_wav_segments,
)
from korina.util.paths import LMSTUDIO_MODEL, PARTIAL_MIN_SECONDS, WHISPER_DEVICE, WHISPER_MODEL_ID

router = APIRouter()


def _whisper_fallback_payload(
    wav: Path,
    *,
    device: Optional[str],
    model_id: Optional[str],
    started: float,
    data_len: int,
    sr: int,
) -> dict:
    """Transcribe ``wav`` with faster-whisper for multimodal-STT fallback.

    ``/api/transcribe`` already has this fallback inline in
    ``transcribe_upload_file``. The live conversation path uses
    ``/api/transcribe/stream`` instead, so the streaming route needs an
    equivalent payload helper rather than crashing when multimodal audio is
    unsupported.
    """
    parts, info = transcribe_wav_segments(
        wav, vad_filter=True, device=device, model_id=model_id,
    )
    elapsed = time.time() - started
    text = ''.join(seg.text for seg in parts).strip()
    config = load_config()
    return {
        'text': text,
        'seconds': elapsed,
        'samples': int(data_len),
        'sample_rate': int(sr),
        'model': model_id or config.get('stt_model') or WHISPER_MODEL_ID,
        'backend': 'whisper-fallback',
        'device': state.asr.device,
        'requested_device': normalize_device(device, default_env=WHISPER_DEVICE),
        'compute_type': state.asr.compute_type,
        'language': getattr(info, 'language', None),
        'language_probability': getattr(info, 'language_probability', None),
        'segments': [
            {'start': seg.start, 'end': seg.end, 'text': seg.text.strip()}
            for seg in parts
        ],
    }


def _emit_fallback_sse(payload: dict, *, started: float, model_id: str):
    """Emit a Whisper fallback payload through the normal streaming SSE shape."""
    segments = payload.get('segments') or []
    emitted = 0
    if segments:
        for idx, seg in enumerate(segments, 1):
            part = str(seg.get('text') or '').strip()
            if not part:
                continue
            emitted += 1
            yield sse_event('segment', {
                'start': seg.get('start'),
                'end': seg.get('end'),
                'text': part,
                'index': idx,
                'backend': payload.get('backend', 'whisper-fallback'),
            })
    elif str(payload.get('text') or '').strip():
        emitted = 1
        yield sse_event('segment', {
            'start': None,
            'end': None,
            'text': str(payload.get('text')).strip(),
            'index': 1,
            'backend': payload.get('backend', 'whisper-fallback'),
        })

    done = {
        'text': str(payload.get('text') or '').strip(),
        'seconds': payload.get('seconds', time.time() - started),
        'samples': int(payload.get('samples') or 0),
        'sample_rate': int(payload.get('sample_rate') or 0),
        'model': payload.get('model') or model_id,
        'backend': payload.get('backend', 'whisper-fallback'),
        'device': payload.get('device'),
        'requested_device': payload.get('requested_device'),
        'compute_type': payload.get('compute_type'),
        'language': payload.get('language'),
        'language_probability': payload.get('language_probability'),
        'segments': emitted,
    }
    for key in ('warning', 'fallback_reason', 'audio_unsupported_since', 'triple'):
        if key in payload:
            done[key] = payload[key]
    yield sse_event('done', done)



@router.post('/api/transcribe')
async def transcribe(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'
    try:
        return JSONResponse(transcribe_upload_file(
            audio.file, suffix, vad_filter=True,
            device=device, model_id=model, backend=backend, llm_model=llm_model,
        ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/api/transcribe/partial')
async def transcribe_partial(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    """Low-latency rolling partial transcript for live mode.

    Browser sends the growing current utterance every ~1.8s while the user is
    still speaking. faster-whisper itself is not a streaming decoder, so this
    endpoint transcribes snapshots of the in-progress utterance and returns the
    latest best partial.
    """
    suffix = Path(audio.filename or 'partial.webm').suffix or '.webm'
    try:
        result = transcribe_upload_file(
            audio.file, suffix, vad_filter=False,
            device=device, model_id=model, backend=backend, llm_model=llm_model,
        )
        result['partial'] = True
        result['stable'] = False
        if result['duration'] < PARTIAL_MIN_SECONDS:
            result['text'] = ''
            result['segments'] = []
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/api/transcribe/stream')
async def transcribe_stream(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'

    def events():
        with tempfile.TemporaryDirectory(prefix='korina-stt-stream-') as td:
            td_path = Path(td)
            src = td_path / f'input{suffix}'
            wav = td_path / 'input-16k.wav'
            with src.open('wb') as f:
                shutil.copyfileobj(audio.file, f)
            started = time.time()
            try:
                yield sse_event('status', {'message': 'converting', 'backend': 'faster-whisper'})
                convert_to_16k_wav(src, wav)
                data, sr = sf.read(wav, dtype='float32')
                if data.ndim > 1:
                    data = np.mean(data, axis=1)
                yield sse_event('status', {'message': 'transcribing',
                                           'samples': int(len(data)),
                                           'sample_rate': int(sr),
                                           'backend': backend})
                if backend == 'llm':
                    # Phase 4.5.3: probe fallback. On
                    # audio-not-supported failure (cached or just
                    # observed), fall back to whisper and emit the
                    # result through the same SSE stream so the
                    # frontend doesn't see a hard error.
                    from korina.services.audio_probe import maybe_fallback_to_whisper
                    config = load_config()
                    stt_provider = (
                        config.get('stt_llm_provider')
                        or config.get('llm_provider')
                        or 'llama.cpp'
                    )
                    probe_base = config_stt_llm_base_url(config)
                    stt_chat_url = config_stt_llm_chat_url(config)
                    whisper_fallback = lambda: _whisper_fallback_payload(
                        wav, device=device, model_id=model, started=started,
                        data_len=int(len(data)), sr=int(sr),
                    )
                    # Cached audio-unsupported triples must skip the known-failing
                    # multimodal request entirely. The first call only checks cache;
                    # status/body do not classify a new failure.
                    used_fallback, payload = maybe_fallback_to_whisper(
                        provider=stt_provider, base_url=probe_base, model=llm_model or '',
                        stt_status_code=0, stt_response_body='',
                        whisper_fallback_fn=whisper_fallback,
                    )
                    if used_fallback:
                        for ev in _emit_fallback_sse(
                            payload, started=started,
                            model_id=llm_model or LMSTUDIO_MODEL,
                        ):
                            yield ev
                        return
                    try:
                        result = lmstudio_transcribe_wav(
                            wav, model=llm_model,
                            base_url=stt_chat_url,
                            api_env=config_stt_llm_api_env(config),
                        )
                    except Exception as e:
                        # Treat any exception as a failed multimodal
                        # call. We don't have a real HTTP status from
                        # urllib/requests error chains; infer a 400
                        # since audio-related issues come back as 400
                        # from llama.cpp. classify_failure will only
                        # return True if the message body itself
                        # matches an audio-not-supported pattern.
                        used_fallback, payload = maybe_fallback_to_whisper(
                            provider=stt_provider, base_url=probe_base, model=llm_model or '',
                            stt_status_code=400, stt_response_body=str(e),
                            whisper_fallback_fn=whisper_fallback,
                        )
                        if used_fallback:
                            for ev in _emit_fallback_sse(
                                payload, started=started,
                                model_id=llm_model or LMSTUDIO_MODEL,
                            ):
                                yield ev
                            return
                        # Otherwise: re-raise the original error to keep
                        # the existing 500 path.
                        raise

                    # Probe the result: empty text on a successful HTTP
                    # call often means audio-not-supported for a
                    # known-multimodal-but-broken triple.
                    text = result.get('text', '') if isinstance(result, dict) else ''
                    if not text:
                        err_body = (
                            result.get('error', '') if isinstance(result, dict) else ''
                        ) or 'empty_text'
                        used_fallback, payload = maybe_fallback_to_whisper(
                            provider=stt_provider, base_url=probe_base, model=llm_model or '',
                            stt_status_code=200, stt_response_body=err_body,
                            whisper_fallback_fn=whisper_fallback,
                        )
                        if used_fallback:
                            for ev in _emit_fallback_sse(
                                payload, started=started,
                                model_id=llm_model or LMSTUDIO_MODEL,
                            ):
                                yield ev
                            return
                    if text:
                        yield sse_event('segment', {'start': None, 'end': None,
                                                    'text': text, 'index': 1})
                    elapsed = time.time() - started
                    yield sse_event('done', {
                        'text': text,
                        'seconds': elapsed,
                        'samples': int(len(data)),
                        'sample_rate': int(sr),
                        'model': llm_model or LMSTUDIO_MODEL,
                        'backend': 'multimodal-stt',
                        'segments': 1 if text else 0,
                    })
                    return
                parts, info = transcribe_wav_segments(wav, vad_filter=True, device=device, model_id=model)
                text_parts = []
                count = 0
                for seg in parts:
                    count += 1
                    part = seg.text.strip()
                    if part:
                        text_parts.append(seg.text)
                    yield sse_event('segment', {
                        'start': seg.start,
                        'end': seg.end,
                        'text': part,
                        'index': count,
                    })
                elapsed = time.time() - started
                yield sse_event('done', {
                    'text': ''.join(text_parts).strip(),
                    'seconds': elapsed,
                    'samples': int(len(data)),
                    'sample_rate': int(sr),
                    'model': model or WHISPER_MODEL_ID,
                    'backend': 'faster-whisper',
                    'device': state.asr.device,
                    'compute_type': state.asr.compute_type,
                    'language': info.language,
                    'language_probability': info.language_probability,
                    'segments': count,
                })
            except Exception as e:
                yield sse_event('error', {'detail': str(e)})

    return StreamingResponse(events(), media_type='text/event-stream')