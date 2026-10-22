"""GET /api/health — reports service status, loaded models, config snapshot.

Phase 1.9: inlined from the monolith's _route_health helper.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from korina.config import (
    agent_base_url,
    agent_chat_url,
    agent_models_url,
    agent_provider,
    agent_auth_headers,
    config_llm_base_url,
    config_llm_chat_url,
    config_llm_models_url,
    config_min_speech_ms,
    config_partial_window_ms,
    config_stt_llm_base_url,
    config_stt_llm_chat_url,
    config_stt_llm_models_url,
    config_stt_llm_model,
    config_stt_llm_provider,
    config_stt_llm_api_env,
    config_tts_base_url,
    load_config,
    config_tts_provider,
)
from korina.runtime import state
from korina.model_ids import limit_model_ids, model_list_info, normalize_model_id
from korina.runtime.limits import AudioLimits, MAX_AUDIO_UPLOAD_BYTES, resolve_audio_limits
from korina.runtime.http import auth_headers_from_env
from korina.runtime.json_http import HTTPStatusError, request_json
from korina.services.ack_service import ack_files_for, ack_status
from korina.services.whisper_service import compute_type_for, cuda_available, normalize_device
from korina.util.paths import (
    LMSTUDIO_MODEL,
    PARTIAL_MIN_SECONDS,
    WHISPER_BEAM_SIZE,
    WHISPER_CPU_THREADS,
    WHISPER_DEVICE,
    WHISPER_MODEL_CHOICES,
    WHISPER_MODEL_ID,
    APP_DIR,
)

router = APIRouter()


def _probe_tts_backend(base_url, timeout=2.0, health_url=None):
    """Probe the TTS backend's /health endpoint with a short timeout.

    Returns (ok, parsed_body, error_message). ok=True when the server responded
    2xx and JSON-decoded; the caller still inspects `loaded` for the runtime
    state. ok=False when the request timed out, was refused, or returned
    non-2xx - error_message describes why.
    """
    import json as _json
    import socket

    if not base_url:
        return (False, {}, "no base_url configured")
    try:
        try:
            body = request_json(health_url or base_url.rstrip('/') + '/health', timeout=timeout)
        except _json.JSONDecodeError:
            return (False, {}, f'non-JSON response from {base_url}/health')
        if not isinstance(body, dict):
            return (False, {}, f'invalid health response from {base_url}/health')
        return (True, body, None)
    except socket.timeout:
        return (False, {}, f'timeout after {timeout}s probing {base_url}/health')
    except HTTPStatusError as e:
        return (False, {}, f'HTTP {e.status_code} from {base_url}/health')
    except (ConnectionRefusedError, ConnectionResetError) as e:
        return (False, {}, f'{type(e).__name__}: {e}')
    except (OSError, ValueError) as e:
        return (False, {}, f'OSError: {e}')


def _probe_llm_models(base_url, expected, timeout=1.5, headers=None, *, models_url=None):
    """Probe a provider's /v1/models endpoint and check whether ``expected``
    appears in the response. Tolerant of:

    - llama.cpp returning absolute GGUF paths (e.g. /home/r/.../model.gguf)
    - LM Studio returning bare catalog ids (e.g. publisher/model-id)
    - Ollama returning name:tag strings (e.g. llama3.2:3b)

    IDs match case-insensitively, with a bare GGUF filename alias and Ollama's
    untagged name alias. Two explicit GGUF paths must identify the same path;
    a matching basename alone cannot establish that the selected file loaded.

    Returns a dict with keys:
        provider, base_url, expected_model, ok, loaded, loaded_model,
        loaded_models, error
    """
    import json as _json
    import socket

    if models_url is None:
        base_clean = (base_url or "").strip().rstrip("/")
        for suffix in ('/chat/completions', '/messages'):
            if base_clean.endswith(suffix):
                base_clean = base_clean[:-len(suffix)].rstrip('/')
        models_url = f'{base_clean}/models' if base_clean else ''
    expected_clean = (expected or "").strip()
    block = {
        "provider": "",
        "base_url": base_url or "",
        "expected_model": expected_clean,
        "ok": False,
        "loaded": False,
        "loaded_model": None,
        "loaded_models": [],
        "error": None,
    }
    if not models_url:
        block["error"] = "no base_url configured"
        return block
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(models_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('expected an http(s) provider URL')
        body = request_json(models_url, headers=headers or {}, timeout=timeout)
    except _json.JSONDecodeError:
        block["error"] = f"non-JSON response from {models_url}"
        return block
    except (socket.timeout, ConnectionRefusedError, ConnectionResetError) as e:
        block["error"] = f"{type(e).__name__}: {e}"
        return block
    except HTTPStatusError as e:
        block["error"] = f"HTTP {e.status_code} from {models_url}"
        return block
    except (OSError, ValueError) as e:
        block["error"] = f"OSError: {e}"
        return block
    items = body.get("data") if isinstance(body, dict) else None
    if items is None and isinstance(body, list):
        items = body
    if not isinstance(items, list):
        block["ok"] = True
        block["error"] = "unexpected /v1/models response shape (no data list)"
        return block

    def _candidate_ids(item):
        if not isinstance(item, dict):
            return []
        ids = []
        for k in ("id", "name", "model"):
            if k in item:
                ids.append(item[k])
        return ids

    raw_ids = [mid for item in items for mid in _candidate_ids(item)]

    expected_lc = expected_clean.lower()
    if expected_lc:
        for raw in raw_ids:
            mid = normalize_model_id(raw)
            if mid is None:
                continue
            ml = mid.lower()
            gguf = ml.endswith('.gguf') and expected_lc.endswith('.gguf')
            explicit_paths = gguf and '/' in mid and '/' in expected_clean
            same_file = gguf and not explicit_paths and ml.rsplit('/', 1)[-1] == expected_lc.rsplit('/', 1)[-1]
            untagged_alias = ':' not in expected_lc and ml.split(':', 1)[0] == expected_lc
            exact = mid == expected_clean if explicit_paths else ml == expected_lc
            if exact or same_file or (untagged_alias and not explicit_paths):
                block["loaded_model"] = mid
                break
    listed = limit_model_ids(raw_ids, pinned=[block['loaded_model']])
    block['loaded_models'] = listed
    block['models_info'] = model_list_info(listed)
    block["loaded"] = block["loaded_model"] is not None
    block["ok"] = True
    return block


def _aggregate_provider_load(provider, base_url, expected):
    """Build a load block for a single LLM-facing role."""
    block = _probe_llm_models(base_url, expected)
    block["provider"] = str(provider or "")
    return block


def _aggregate_tts(config):
    """Build the tts block: {ok, loaded, device, cuda_available, provider, base_url, error}."""
    provider = config_tts_provider(config)
    base_url = config_tts_base_url(config)
    health_url = str(config.get('tts_health_url') or '').strip()
    if provider != 'kokoro' and not health_url:
        return {
            'ok': False, 'loaded': False, 'device': None, 'cuda_available': False,
            'provider': provider, 'base_url': base_url, 'status': 'unverified',
            'probe_supported': False,
            'error': 'Configure tts_health_url or verify speech manually; this provider has no standard health endpoint',
        }
    kwargs = {'health_url': health_url} if health_url else {}
    ok, body, error = _probe_tts_backend(base_url, timeout=2.0, **kwargs)
    if ok:
        try:
            body = _validated_tts_health(body)
        except ValueError as exc:
            ok, error = False, str(exc)
        else:
            if body.get('ok') is False or body.get('runtime_available') is False or body.get('status') in {'error', 'failed', 'degraded', 'unavailable'}:
                ok = False
                error = body.get('error') or 'TTS backend reports unavailable'
    if not ok:
        return {
            'ok': False,
            'loaded': False,
            'device': None,
            'cuda_available': False,
            'provider': provider,
            'base_url': base_url,
            'error': error,
        }
    loaded = bool(body.get('loaded'))
    device = body.get('device')
    cuda = bool(body.get('cuda_available'))
    return {
        'ok': True,
        'loaded': loaded,
        'device': device,
        'cuda_available': cuda,
        'provider': provider,
        'base_url': base_url,
        'error': None,
    }


def _validated_tts_health(body: object) -> dict:
    """Reject malformed readiness flags without taking down Korina liveness."""
    if not isinstance(body, dict):
        raise ValueError('Invalid TTS health response: expected an object')
    result = {}
    for key in ('ok', 'runtime_available', 'loaded', 'cuda_available'):
        if key in body:
            if not isinstance(body[key], bool):
                raise ValueError(f'Invalid TTS health response: {key} must be boolean')
            result[key] = body[key]
    for key, maximum in (('status', 64), ('device', 128), ('error', 1024)):
        value = body.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f'Invalid TTS health response: {key} must be text')
        try:
            value.encode('utf-8', errors='strict')
        except UnicodeError as exc:
            raise ValueError(f'Invalid TTS health response: {key} contains invalid Unicode') from exc
        result[key] = value.strip()[:maximum]
    statuses = {'ok', 'healthy', 'ready', 'starting', 'loading', 'initializing', 'error', 'failed', 'degraded', 'unavailable'}
    if not any(key in result for key in ('ok', 'runtime_available', 'loaded')) and result.get('status') not in statuses:
        raise ValueError('Invalid TTS health response: no recognized readiness signal')
    return result


@router.get('/api/health')
async def health(request: Request):
    config = load_config()
    audio_limits: AudioLimits | None
    audio_limits_error: str | None = None
    try:
        audio_limits = resolve_audio_limits(
            max_upload_bytes=request.scope.get('korina.audio_max_upload_bytes', MAX_AUDIO_UPLOAD_BYTES),
        )
    except ValueError as exc:
        audio_limits = None
        audio_limits_error = str(exc)
    roles = {
        'response_llm_load': (
            str(config.get('llm_provider') or 'llama.cpp'), config_llm_base_url(config),
            config_llm_models_url(config), str(config.get('lm_model') or LMSTUDIO_MODEL),
            auth_headers_from_env(str(config.get('llm_api_key_env') or '')),
        ),
        'multimodal_stt_load': (
            config_stt_llm_provider(config), config_stt_llm_base_url(config),
            config_stt_llm_models_url(config), config_stt_llm_model(config),
            auth_headers_from_env(config_stt_llm_api_env(config)),
        ),
        'agent_load': (
            agent_provider(config), agent_base_url(config), agent_models_url(config),
            str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
            agent_auth_headers(config),
        ),
    }
    # Independent probes share a timeout window. Roles using the same model
    # and endpoint reuse the request instead of querying it three times.
    probes = {}
    for _, base, models_url, model, headers in roles.values():
        key = (models_url, model, tuple(sorted(headers.items())))
        if key not in probes:
            probes[key] = asyncio.create_task(asyncio.to_thread(
                _probe_llm_models, base, model, headers=headers, models_url=models_url,
            ))
    tts_task = asyncio.create_task(asyncio.to_thread(_aggregate_tts, config))
    has_cuda = await asyncio.to_thread(cuda_available)
    loads = {}
    for name, (provider, base, models_url, model, headers) in roles.items():
        loads[name] = {
            **await probes[(models_url, model, tuple(sorted(headers.items())))],
            'provider': provider, 'base_url': base,
        }
    whisper_available = importlib.util.find_spec('faster_whisper') is not None
    result = {
        'ok': True,
        'service': 'korina-voice-lab',
        'audio_limits': audio_limits,
        'audio_limits_error': audio_limits_error,
        'whisper_backend': 'faster-whisper',
        'whisper_model': WHISPER_MODEL_ID,
        'whisper_model_choices': WHISPER_MODEL_CHOICES,
        'whisper_loaded': bool(state.asr.models),
        'whisper_available': whisper_available,
        'stt_backend': str(config.get('stt_backend') or 'whisper'),
        'whisper_loaded_devices': sorted(state.asr.models.keys()),
        'whisper_loaded_at_by_device': state.asr.loaded_at_by_device,
        'whisper_device': state.asr.device or normalize_device(None, default_env=WHISPER_DEVICE),
        'whisper_compute_type': state.asr.compute_type or compute_type_for(normalize_device(None, default_env=WHISPER_DEVICE)),
        'cuda_available': has_cuda,
        'whisper_beam_size': WHISPER_BEAM_SIZE,
        'whisper_cpu_threads': WHISPER_CPU_THREADS,
        'partial_min_seconds': PARTIAL_MIN_SECONDS,
        'min_speech_ms': config_min_speech_ms(config),
        'partial_window_ms': config_partial_window_ms(config),
        'cuda': has_cuda,
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
        'tts_provider': config_tts_provider(config),
        'tts_base_url': config_tts_base_url(config),
        'tts': await tts_task,
        **loads,
        'ack_count': len(ack_files_for(state.ack.current_voice)),
        'ack_status': ack_status(state.ack.current_voice),
    }
    checks = {
        'audio_limits': audio_limits is not None,
        'frontend': (APP_DIR / 'index.html').is_file() and (APP_DIR / 'js' / 'app.js').is_file() and (APP_DIR / 'styles.css').is_file(),
        'response_llm': bool(loads['response_llm_load']['ok'] and loads['response_llm_load']['loaded']),
        'stt': bool(shutil.which('ffmpeg')) and (
            bool(loads['multimodal_stt_load']['ok'] and loads['multimodal_stt_load']['loaded'])
            if result['stt_backend'] == 'llm' else whisper_available
        ),
        'tts': bool(result['tts']['ok']),  # lazy Kokoro loads on first speech
        'agent': config.get('agent_enabled') != 'on' or bool(loads['agent_load']['ok'] and loads['agent_load']['loaded']),
    }
    result['readiness'] = {'ready': all(checks.values()), 'checks': checks}
    return result


@router.get('/api/ready')
async def ready(request: Request):
    """Voice-stack readiness for rehearsal/monitoring; liveness stays HTTP 200."""
    result = (await health(request))['readiness']
    return JSONResponse(result, status_code=200 if result['ready'] else 503)
