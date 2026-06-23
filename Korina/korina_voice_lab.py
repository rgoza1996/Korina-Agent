from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Paths and env defaults moved to korina/util/paths.py (Phase 1.2)
from korina.util.paths import (
    APP_DIR, INDEX_PATH, ACK_DIR, ACK_PHRASES_PATH, CONFIG_PATH,
    KOKORO_URL, ACK_DEFAULT_VOICE,
    WHISPER_MODEL_ID, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
    WHISPER_CPU_THREADS, WHISPER_BEAM_SIZE,
    PARTIAL_MIN_SECONDS, LMSTUDIO_URL, LMSTUDIO_MODEL,
    WHISPER_MODEL_CHOICES, LOCAL_MODEL_ROOTS, LMSTUDIO_HUB_ROOT,
    LLAMA_SERVER_BIN, LMSTUDIO_BIN, LLAMA_SERVER_MEDIA_PATH,
    LLAMA_SERVER_USER_UNIT,
)

app = FastAPI(title='Korina Voice Lab: Built-in Whisper, multimodal STT, llama.cpp, and Kokoro')
# Backwards-compat alias used by the in-progress EventBus state-report endpoints
korina_app = app
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
ACK_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/Ack', StaticFiles(directory=str(ACK_DIR)), name='ack')




# Runtime state moved to korina/runtime/state.py (Phase 1.3)
from korina.runtime import state

# Config bootstrapping and helpers moved to korina/config.py (Phase 1.2)
from korina.config import (
    DEFAULT_CONFIG, CONFIG_KEYS,
    LEGACY_AGENT_MODE_KEY, LEGACY_DELIVERY_VALUE, LEGACY_INJECTION_MODE_VALUE,
    _normalize_config_value, _config_example_path,
    load_config, save_config,
    synchronize_llm_dependents,
    config_tts_base_url, config_llm_base_url, config_llm_chat_url, config_llm_models_url,
    config_stt_llm_provider, config_stt_llm_base_url, config_stt_llm_chat_url, config_stt_llm_models_url,
    config_stt_llm_api_env, config_stt_llm_model,
    config_min_speech_ms, config_partial_window_ms,
    config_llm_reasoning, config_stt_llm_reasoning,
    agent_provider, agent_base_url, agent_models_url, agent_chat_url, agent_auth_headers,
)
from korina.util.presets import provider_preset_base_url, is_local_provider_base_url
from korina.util.labels import safe_slug, display_model_label
from korina.runtime.http import auth_headers_from_env, api_key_from_config
from korina.services.agent_service import agent_model_choices, parse_model_ids
from korina.services.model_catalog import llm_models_for

from korina.routes import register_routes
from korina.schemas import (
    AgentPermissionAnswer, AgentStateRequest, AgentTranscriptRequest,
    ChatRequest, ProviderActivateRequest,
)
from korina.util.labels import safe_slug

# Service modules moved to korina/services/ (Phase 1.4)
from korina.services.ack_service import (
    load_ack_manifest,
    ack_filename,
    ack_path,
    ack_files_for,
    missing_ack_phrases,
    enqueue_missing_acks,
    synthesize_ack_wav,
    ack_generation_worker,
    clear_ack_wavs,
    ack_status,
)
from korina.services.agent_service import (
    generate_agent_state_report,
    sanitize_agent_report,
    classify_agent_priority,
    push_agent_event,
    agent_snapshot,
    run_agent_transcript_job,
    submit_agent_transcript,
)
from korina.services.model_catalog import (
    local_model_roots,
    discover_local_gguf_models,
    discover_lmstudio_catalog_models,
    find_mmproj_for_model,
)
from korina.services.multimodal_stt import (
    lmstudio_models,
    lmstudio_transcribe_wav,
)
from korina.services.provider_manager import (
    gui_env,
    stop_lmstudio,
    start_lmstudio,
    stop_ollama,
    start_ollama,
    write_llama_server_unit,
    wait_for_llama_server_ready,
    start_llama_server,
    stop_llama_server,
    _resolve_llama_cpp_model_id,
    activate_llm_provider,
)
from korina.services.response_llm import (
    format_voice_reply,
    response_llm_chat,
)
from korina.services.whisper_service import (
    normalize_device,
    compute_type_for,
    get_asr,
    transcribe_wav_segments,
    transcribe_upload_file,
    sse_event,
    convert_to_16k_wav,
)




































PRIORITY_ORDER = {'low': 0, 'normal': 1, 'important': 2, 'critical': 3}












@app.on_event('startup')
def startup_generate_default_acks():
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    enqueue_missing_acks(str(load_config().get('voice') or ACK_DEFAULT_VOICE))


def _route_index():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail='index.html missing')
    return FileResponse(INDEX_PATH)


def _route_health():
    config = load_config()
    return {
        'ok': True,
        'service': 'korina-voice-lab',
        'whisper_backend': 'faster-whisper',
        'whisper_model': WHISPER_MODEL_ID,
        'whisper_model_choices': WHISPER_MODEL_CHOICES,
        'whisper_loaded': bool(state.asr.models),
        'whisper_loaded_devices': sorted(state.asr.models.keys()),
        'whisper_loaded_at_by_device': state.asr.loaded_at_by_device,
        'whisper_device': state.asr.device or normalize_device(None, default_env=WHISPER_DEVICE),
        'whisper_compute_type': state.asr.compute_type or compute_type_for(normalize_device(None, default_env=WHISPER_DEVICE)),
        'cuda_available': torch.cuda.is_available(),
        'whisper_beam_size': WHISPER_BEAM_SIZE,
        'whisper_cpu_threads': WHISPER_CPU_THREADS,
        'partial_min_seconds': PARTIAL_MIN_SECONDS,
        'min_speech_ms': config_min_speech_ms(config),
        'partial_window_ms': config_partial_window_ms(config),
        'cuda': torch.cuda.is_available(),
        'response_llm_provider': str(config.get('llm_provider') or 'llama.cpp'),
        'response_llm_base_url': config_llm_base_url(config),
        'response_llm_chat_url': config_llm_chat_url(config),
        'response_llm_model': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'agent_provider': agent_provider(config),
        'agent_base_url': agent_base_url(config),
        'agent_chat_url': agent_chat_url(config),
        'agent_model': str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
        'multimodal_stt_provider': config_stt_llm_provider(config),
        'multimodal_stt_base_url': config_stt_llm_base_url(config),
        'multimodal_stt_chat_url': config_stt_llm_chat_url(config),
        'multimodal_stt_model': config_stt_llm_model(config),
        'tts_base_url': config_tts_base_url(config),
        'ack_count': len(ack_files_for(state.ack.current_voice)),
        'ack_status': ack_status(state.ack.current_voice),
    }


def _route_get_config():
    return load_config()


def _route_update_config(payload: dict):
    current = load_config()
    previous = dict(current)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in CONFIG_KEYS:
                current[key] = value
    current = synchronize_llm_dependents(current, previous)
    saved = save_config(current)
    try:
        llm_provider_now = str(saved.get('llm_provider') or '').strip().lower()
        llm_provider_before = str(previous.get('llm_provider') or '').strip().lower()
        if llm_provider_now in {'llama.cpp', 'lmstudio', 'ollama'} and (
            str(saved.get('lm_model') or '') != str(previous.get('lm_model') or '')
            or config_llm_reasoning(saved) != config_llm_reasoning(previous)
            or llm_provider_before != llm_provider_now
        ):
            activate_llm_provider(llm_provider_now, saved, model=str(saved.get('lm_model') or ''))
        elif llm_provider_before in {'llama.cpp', 'lmstudio', 'ollama'} and llm_provider_now == 'openai-compatible':
            activate_llm_provider('openai-compatible', saved, model=str(saved.get('lm_model') or ''))
    except Exception:
        pass
    return saved


def _route_models(llm_base_url: Optional[str] = Query(None), llm_api_key_env: Optional[str] = Query(None), stt_llm_base_url: Optional[str] = Query(None), stt_llm_api_key_env: Optional[str] = Query(None)):
    config = load_config()
    llm_base = str(llm_base_url or config_llm_base_url(config)).strip().rstrip('/')
    llm_api_env_name = str(llm_api_key_env or config.get('llm_api_key_env') or '').strip()
    stt_base = str(stt_llm_base_url or config_stt_llm_base_url(config)).strip().rstrip('/')
    stt_api_env_name = str(stt_llm_api_key_env or config_stt_llm_api_env(config) or '').strip()
    llm_error = None
    llm_models = []
    stt_llm_error = None
    stt_llm_models = []
    try:
        llm_models = llm_models_for(llm_base, llm_api_env_name)
        if llm_base == "http://127.0.0.1:8080/v1":
            for d in discover_local_gguf_models():
                if d not in llm_models:
                    llm_models.append(d)
    except Exception as e:
        llm_error = str(e)
    try:
        stt_llm_models = llm_models_for(stt_base, stt_api_env_name)
        if stt_base == "http://127.0.0.1:8080/v1":
            for d in discover_local_gguf_models():
                if d not in stt_llm_models:
                    stt_llm_models.append(d)
    except Exception as e:
        stt_llm_error = str(e)
    return {
        'whisper_models': WHISPER_MODEL_CHOICES,
        'llm_models': llm_models,
        'llm_default': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'llm_base_url': llm_base,
        'llm_error': llm_error,
        'stt_llm_models': stt_llm_models,
        'stt_llm_default': config_stt_llm_model(config),
        'stt_llm_base_url': stt_base,
        'stt_llm_error': stt_llm_error,
        'llama_cpp_local_models': discover_local_gguf_models(),
        'lmstudio_catalog_models': discover_lmstudio_catalog_models(),
        'labels': {mid: display_model_label(mid) for mid in list(dict.fromkeys(llm_models + stt_llm_models + discover_local_gguf_models()))},
    }


def _route_activate_provider(req: ProviderActivateRequest):
    config = load_config()
    provider = str(req.provider or config.get('llm_provider') or 'openai-compatible').strip()
    current = dict(config)
    current['llm_provider'] = provider
    preset = provider_preset_base_url(provider)
    if provider == 'openai-compatible':
        current['llm_base_url'] = str(config.get('llm_base_url') or current.get('llm_base_url') or '').strip()
    elif preset:
        current['llm_base_url'] = preset
    if req.model:
        current['lm_model'] = str(req.model).strip()
    current = synchronize_llm_dependents(current, config)
    saved = save_config(current)
    try:
        result = activate_llm_provider(provider, saved, model=req.model)
        return {'ok': True, 'saved': saved, 'activation': result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _route_acks(voice: str = Query(ACK_DEFAULT_VOICE), tag: Optional[str] = Query(None)):
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    enqueue_missing_acks(voice, tag)
    return {
        'acks': ack_files_for(voice, tag),
        'tag': tag or 'global',
        'status': ack_status(voice),
    }


def _route_acks_status(voice: str = Query(ACK_DEFAULT_VOICE)):
    return ack_status((voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE)


def _route_acks_rebuild(payload: dict):
    voice = (payload.get('voice') or ACK_DEFAULT_VOICE).strip()
    tag = payload.get('tag')
    clear = bool(payload.get('clear', False))
    if clear:
        removed = clear_ack_wavs()
    else:
        removed = 0
    state.ack.current_voice = voice
    missing = enqueue_missing_acks(voice, tag)
    return {'ok': True, 'voice': voice, 'tag': tag, 'removed': removed, 'queued_or_missing': missing, 'status': ack_status(voice)}


def _route_agent_status():
    return {'ok': True, **agent_snapshot()}


def _route_agent_events(after: int = Query(0)):
    with state.agent.lock:
        events = [e for e in state.agent.events if int(e.get('id', 0)) > after]
        last_id = state.agent.event_seq
    return {'ok': True, 'events': events, 'last_event_id': last_id, **agent_snapshot()}


def _route_agent_transcript(req: AgentTranscriptRequest):
    return submit_agent_transcript(req)


def _route_agent_permission_answer(req: AgentPermissionAnswer):
    push_agent_event({'type': 'permission_answer', 'priority': 'normal', 'request_id': req.request_id, 'answer': req.answer, 'transcript': req.transcript})
    return submit_agent_transcript(AgentTranscriptRequest(transcript=req.transcript, delivery_mode='prompt', reason=f'permission_answer:{req.answer}', turn_count=0))


def _route_agent_reset():
    with state.agent.lock:
        state.agent.events.clear()
        state.agent.event_seq = 0
        state.agent.pending_injections.clear()
        state.agent.busy = False
        state.agent.status = 'idle'
        state.agent.last_report = ''
        state.agent.last_error = None
        state.agent.last_emitted_report_hash = ''
        state.agent.last_emitted_report_at = 0.0
    return {'ok': True, 'status': agent_snapshot()}


def _route_agent_models():
    try:
        models = agent_model_choices()
        return {'ok': True, 'models': models, 'provider': agent_provider(), 'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL), 'error': None}
    except Exception as e:
        return {'ok': False, 'models': [], 'provider': agent_provider(), 'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL), 'error': str(e)}


def _route_agent_state_report(req: AgentStateRequest):
    if str(load_config().get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'state_report': '', 'disabled': True}
    try:
        report = generate_agent_state_report(req)
        return {'ok': True, 'state_report': report, 'model': req.model or str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _route_transcribe(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'
    try:
        return JSONResponse(transcribe_upload_file(audio.file, suffix, vad_filter=True, device=device, model_id=model, backend=backend, llm_model=llm_model))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _route_transcribe_partial(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
    """Low-latency rolling partial transcript for live mode.

    Browser sends the growing current utterance every ~1.8s while the user is
    still speaking. faster-whisper itself is not a streaming decoder, so this
    endpoint transcribes snapshots of the in-progress utterance and returns the
    latest best partial.
    """
    suffix = Path(audio.filename or 'partial.webm').suffix or '.webm'
    try:
        result = transcribe_upload_file(audio.file, suffix, vad_filter=False, device=device, model_id=model, backend=backend, llm_model=llm_model)
        result['partial'] = True
        result['stable'] = False
        if result['duration'] < PARTIAL_MIN_SECONDS:
            result['text'] = ''
            result['segments'] = []
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _route_transcribe_stream(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
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
                yield sse_event('status', {'message': 'transcribing', 'samples': int(len(data)), 'sample_rate': int(sr), 'backend': backend})
                if backend == 'llm':
                    result = lmstudio_transcribe_wav(wav, model=llm_model, base_url=config_stt_llm_chat_url(load_config()), api_env=config_stt_llm_api_env(load_config()))
                    text = result.get('text', '')
                    if text:
                        yield sse_event('segment', {'start': None, 'end': None, 'text': text, 'index': 1})
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


def _route_chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail='No message provided')
    started = time.time()
    try:
        reply = response_llm_chat(req)
        return JSONResponse({
            'reply': reply,
            'seconds': time.time() - started,
            'model': req.model or str(load_config().get('lm_model') or LMSTUDIO_MODEL),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




# ============================================================
# Korina Agent: State Report + Permission Answer Endpoints
# ============================================================

async def _route_alpha_agent_state_report(request: Request):
    """
    Receives transcript deltas/turns from Korina Converse.
    Returns state_report, interrupt, and/or permission_request.
    """
    body = await request.json()
    cfg = load_config()
    agent_cfg = {
        'enabled': cfg.get('agent_enabled') == 'on',
        'model': cfg.get('agent_model', ''),
        'base_url': cfg.get('agent_base_url', ''),
    }

    # TODO: wire up to Korina Agent Alpha here
    # For now, return empty responses — Agent not yet connected
    return {
        "state_report": None,
        "interrupt": None,
        "permission_request": None,
        "debug": {
            "turns_received": len(body.get('turns', [])),
            "trigger": body.get('trigger', '?'),
            "agent_enabled": agent_cfg['enabled'],
        }
    }


async def _route_alpha_agent_permission_answer(request: Request):
    """
    Receives the user's answer to a permission question.
    Routes it back to Korina Agent.
    """
    body = await request.json()
    request_id = body.get('request_id', 'unknown')
    answer = body.get('answer', 'no')
    # TODO: wire up to Korina Agent
    print(f"[Korina Agent] Permission answer: {request_id} -> {answer}")
    return {"ok": True, "request_id": request_id, "answer": answer}



register_routes(app, globals())

# Phase 1.8: __main__ block delegates to korina.app.main() so the uvicorn
# launcher lives in one canonical place. The route body, app instance,
# and globals()-based route registration stay here until Step 1.9.
if __name__ == '__main__':
    from korina.app import main
    main(app)
