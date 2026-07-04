"""GET /api/models — list available LLM/STT/whisper models.

Phase 1.9: inlined from the monolith's _route_models helper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from korina.config import (
    config_audio_unsupported,
    config_llm_base_url,
    config_stt_llm_api_env,
    config_stt_llm_base_url,
    config_stt_llm_model,
    load_config,
)
from korina.services.audio_probe import triple_key
from korina.services.model_catalog import (
    discover_local_gguf_models,
    discover_lmstudio_catalog_models,
    llm_models_for,
)
from korina.services.provider_manager import provider_supports_model
from korina.util.labels import display_model_label
from korina.util.paths import LMSTUDIO_MODEL, WHISPER_MODEL_CHOICES

router = APIRouter()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or '').strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _looks_like_mmproj_sidecar(model_id: str) -> bool:
    return 'mmproj' in Path(str(model_id or '')).name.lower()


def _has_catalog_mmproj_sibling(model_id: str, all_models: list[str]) -> bool:
    mid = str(model_id or '').strip()
    if not mid.endswith('.gguf') or _looks_like_mmproj_sidecar(mid):
        return False
    parent = mid.rsplit('/', 1)[0] if '/' in mid else ''
    for other in all_models:
        candidate = str(other or '').strip()
        if not _looks_like_mmproj_sidecar(candidate):
            continue
        other_parent = candidate.rsplit('/', 1)[0] if '/' in candidate else ''
        if parent == other_parent:
            return True
    return False


def _status_for_model(
    model_id: str,
    *,
    provider: str,
    base_url: str,
    require_audio: bool,
    capability: dict,
    audio_cache: dict[str, dict],
    all_models: list[str],
) -> dict:
    reasons: list[str] = []
    mid = str(model_id or '').strip()

    if _looks_like_mmproj_sidecar(mid):
        reasons.append('mmproj sidecar, not a runnable model')
    if require_audio and provider == 'lmstudio' and _has_catalog_mmproj_sibling(mid, all_models):
        reasons.append('paired mmproj sidecar exists for this GGUF')
    if require_audio and mid.endswith('.gguf') and not capability.get('has_mmproj'):
        reasons.append('missing mmproj for multimodal audio')

    supported, provider_reason = provider_supports_model(provider, mid)
    if not supported:
        reasons.append(str(provider_reason))

    if require_audio and not capability.get('supports_audio_input'):
        reasons.append('no audio input support')

    if require_audio:
        probe = audio_cache.get(triple_key(provider, base_url, mid))
        if probe:
            reasons.append(
                f"audio probe says unsupported ({str(probe.get('reason') or 'unsupported')})"
            )

    reasons = _dedupe(reasons)
    return {
        'usable': len(reasons) == 0,
        'summary': reasons[0] if reasons else '',
        'reasons': reasons,
    }


@router.get('/api/models')
def models(
    llm_base_url: Optional[str] = Query(None),
    llm_api_key_env: Optional[str] = Query(None),
    stt_llm_base_url: Optional[str] = Query(None),
    stt_llm_api_key_env: Optional[str] = Query(None),
    llm_provider: Optional[str] = Query(None),
    stt_llm_provider: Optional[str] = Query(None),
):
    config = load_config()
    llm_base = str(llm_base_url or config_llm_base_url(config)).strip().rstrip('/')
    llm_api_env_name = str(llm_api_key_env or config.get('llm_api_key_env') or '').strip()
    stt_base = str(stt_llm_base_url or config_stt_llm_base_url(config)).strip().rstrip('/')
    stt_api_env_name = str(stt_llm_api_key_env or config_stt_llm_api_env(config) or '').strip()
    llm_provider_id = str(llm_provider or config.get('llm_provider') or 'llama.cpp').strip()
    stt_provider_id = str(
        stt_llm_provider or config.get('stt_llm_provider') or config.get('llm_provider') or 'llama.cpp'
    ).strip()

    llm_error = None
    llm_models: list[str] = []
    stt_llm_error = None
    stt_llm_models: list[str] = []
    try:
        llm_models = llm_models_for(llm_base, llm_api_env_name)
    except Exception as e:
        llm_error = str(e)
    try:
        stt_llm_models = llm_models_for(stt_base, stt_api_env_name)
    except Exception as e:
        stt_llm_error = str(e)

    llama_cpp_local_models = discover_local_gguf_models()
    lmstudio_catalog_models = discover_lmstudio_catalog_models()

    if llm_base == 'http://127.0.0.1:8080/v1':
        for d in llama_cpp_local_models:
            if d not in llm_models:
                llm_models.append(d)
    if stt_base == 'http://127.0.0.1:8080/v1':
        for d in llama_cpp_local_models:
            if d not in stt_llm_models:
                stt_llm_models.append(d)

    from korina.services.model_capability import get_model_capability
    from korina.config import config_multimodal_stt_model_allowlist

    _allowlist = config_multimodal_stt_model_allowlist(config)

    def _cap_for(mid: str) -> dict:
        cap = get_model_capability(mid, allowlist=_allowlist)
        return {k: v for k, v in cap.items() if not k.startswith('_')}

    all_known_models = list(dict.fromkeys(
        llm_models + stt_llm_models + llama_cpp_local_models + lmstudio_catalog_models
    ))
    llm_models_capabilities = {m: _cap_for(m) for m in all_known_models}
    stt_llm_models_capabilities = {m: llm_models_capabilities[m] for m in all_known_models}
    audio_cache = config_audio_unsupported(config)
    llm_models_status = {
        m: _status_for_model(
            m,
            provider=llm_provider_id,
            base_url=llm_base,
            require_audio=False,
            capability=llm_models_capabilities[m],
            audio_cache=audio_cache,
            all_models=all_known_models,
        )
        for m in all_known_models
    }
    stt_llm_models_status = {
        m: _status_for_model(
            m,
            provider=stt_provider_id,
            base_url=stt_base,
            require_audio=True,
            capability=stt_llm_models_capabilities[m],
            audio_cache=audio_cache,
            all_models=all_known_models,
        )
        for m in all_known_models
    }

    return {
        'whisper_models': WHISPER_MODEL_CHOICES,
        'llm_models': llm_models,
        'llm_models_capabilities': llm_models_capabilities,
        'llm_models_status': llm_models_status,
        'llm_default': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'llm_base_url': llm_base,
        'llm_error': llm_error,
        'stt_llm_models': stt_llm_models,
        'stt_llm_models_capabilities': stt_llm_models_capabilities,
        'stt_llm_models_status': stt_llm_models_status,
        'stt_llm_default': config_stt_llm_model(config),
        'stt_llm_base_url': stt_base,
        'stt_llm_error': stt_llm_error,
        'llama_cpp_local_models': llama_cpp_local_models,
        'lmstudio_catalog_models': lmstudio_catalog_models,
        'labels': {mid: display_model_label(mid) for mid in all_known_models},
    }
