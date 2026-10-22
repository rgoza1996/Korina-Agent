"""GET /api/health — reports service status, loaded models, config snapshot.

Phase 1.9: inlined from the monolith's _route_health helper.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from korina.config import (
    agent_base_url,
    agent_chat_url,
    agent_provider,
    agent_auth_headers,
    config_llm_base_url,
    config_llm_chat_url,
    config_min_speech_ms,
    config_partial_window_ms,
    config_stt_llm_base_url,
    config_stt_llm_chat_url,
    config_stt_llm_model,
    config_stt_llm_provider,
    config_stt_llm_api_env,
    config_tts_base_url,
    load_config,
    config_tts_provider,
)
from korina.runtime import state
from korina.runtime.http import auth_headers_from_env
from korina.services.ack_service import ack_files_for, ack_status
from korina.services.whisper_service import compute_type_for, cuda_available, normalize_device
from korina.util.paths import (
    LMSTUDIO_MODEL,
    PARTIAL_MIN_SECONDS,
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
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
    import urllib.error
    import urllib.request

    if not base_url:
        return (False, {}, "no base_url configured")
    try:
        req = urllib.request.Request(health_url or base_url.rstrip('/') + '/health', method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        try:
            body = _json.loads(raw)
        except _json.JSONDecodeError:
            return (False, {}, f'non-JSON response from {base_url}/health')
        if not isinstance(body, dict):
            return (False, {}, f'invalid health response from {base_url}/health')
        return (True, body, None)
    except socket.timeout:
        return (False, {}, f'timeout after {timeout}s probing {base_url}/health')
    except urllib.error.HTTPError as e:
        return (False, {}, f'HTTP {e.code} from {base_url}/health')
    except urllib.error.URLError as e:
        return (False, {}, f'{type(e).__name__}: {e.reason}')
    except (ConnectionRefusedError, ConnectionResetError) as e:
        return (False, {}, f'{type(e).__name__}: {e}')
    except (OSError, ValueError) as e:
        return (False, {}, f'OSError: {e}')


def _probe_llm_models(base_url, expected, timeout=1.5, headers=None):
    """Probe a provider's /v1/models endpoint and check whether ``expected``
    appears in the response. Tolerant of:

    - llama.cpp returning absolute GGUF paths (e.g. /home/r/.../model.gguf)
    - LM Studio returning bare catalog ids (e.g. publisher/model-id)
    - Ollama returning name:tag strings (e.g. llama3.2:3b)

    Match strategy: case-insensitive substring match. llama.cpp absolute
    paths will match both the bare model filename (suffix) and any parent
    directory component; LM Studio bare ids match themselves; Ollama
    name:tag strings are compared both with and without the tag suffix.

    Returns a dict with keys:
        provider, base_url, expected_model, ok, loaded, loaded_model,
        loaded_models, error
    """
    import json as _json
    import socket
    import urllib.error
    import urllib.request

    base_clean = (base_url or "").strip().rstrip("/")
    for suffix in ('/chat/completions', '/messages'):
        if base_clean.endswith(suffix):
            base_clean = base_clean[:-len(suffix)].rstrip('/')
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
    if not base_clean:
        block["error"] = "no base_url configured"
        return block
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(base_clean)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
            raise ValueError('expected an http(s) provider URL')
        req = urllib.request.Request(base_clean + "/models", headers=headers or {}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (socket.timeout, ConnectionRefusedError, ConnectionResetError) as e:
        block["error"] = f"{type(e).__name__}: {e}"
        return block
    except urllib.error.HTTPError as e:
        block["error"] = f"HTTP {e.code} from {base_clean}/v1/models"
        return block
    except urllib.error.URLError as e:
        block["error"] = f"URLError: {e.reason}"
        return block
    except (OSError, ValueError) as e:
        block["error"] = f"OSError: {e}"
        return block
    try:
        body = _json.loads(raw) if raw else {}
    except _json.JSONDecodeError:
        block["error"] = f"non-JSON response from {base_clean}/v1/models"
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
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                ids.append(v.strip())
        return ids

    raw_ids = []
    seen = set()
    for it in items:
        for mid in _candidate_ids(it):
            if mid not in seen:
                seen.add(mid)
                raw_ids.append(mid)
    block["loaded_models"] = raw_ids

    expected_lc = expected_clean.lower()
    if expected_lc:
        for mid in raw_ids:
            ml = mid.lower()
            same_file = ml.endswith('.gguf') and expected_lc.endswith('.gguf') and ml.rsplit('/', 1)[-1] == expected_lc.rsplit('/', 1)[-1]
            untagged_alias = ':' not in expected_lc and ml.split(':', 1)[0] == expected_lc
            if ml == expected_lc or same_file or untagged_alias:
                block["loaded_model"] = mid
                break
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
    if ok and (body.get('ok') is False or body.get('runtime_available') is False or body.get('status') in {'error', 'failed', 'degraded'}):
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


@router.get('/api/health')
async def health():
    config = load_config()
    roles = {
        'response_llm_load': (str(config.get('llm_provider') or 'llama.cpp'), config_llm_base_url(config), str(config.get('lm_model') or LMSTUDIO_MODEL), auth_headers_from_env(str(config.get('llm_api_key_env') or ''))),
        'multimodal_stt_load': (config_stt_llm_provider(config), config_stt_llm_base_url(config), config_stt_llm_model(config), auth_headers_from_env(config_stt_llm_api_env(config))),
        'agent_load': (agent_provider(config), agent_base_url(config), str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL), agent_auth_headers(config)),
    }
    # Independent probes share a timeout window. Roles using the same model
    # and endpoint reuse the request instead of querying it three times.
    probes = {}
    for _, base, model, headers in roles.values():
        key = (base, model, tuple(sorted(headers.items())))
        if key not in probes:
            kwargs = {'headers': headers} if headers else {}
            probes[key] = asyncio.create_task(asyncio.to_thread(_probe_llm_models, base, model, **kwargs))
    tts_task = asyncio.create_task(asyncio.to_thread(_aggregate_tts, config))
    has_cuda = await asyncio.to_thread(cuda_available)
    loads = {}
    for name, (provider, base, model, headers) in roles.items():
        loads[name] = {**await probes[(base, model, tuple(sorted(headers.items())))], 'provider': provider}
    whisper_available = importlib.util.find_spec('faster_whisper') is not None
    result = {
        'ok': True,
        'service': 'korina-voice-lab',
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
async def ready():
    """Voice-stack readiness for rehearsal/monitoring; liveness stays HTTP 200."""
    result = (await health())['readiness']
    return JSONResponse(result, status_code=200 if result['ready'] else 503)
