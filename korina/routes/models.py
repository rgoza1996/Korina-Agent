"""GET /api/models — list available LLM/STT/whisper models.

Phase 1.9: inlined from the monolith's _route_models helper.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from korina.config import (
    config_llm_base_url,
    config_stt_llm_api_env,
    config_stt_llm_base_url,
    config_stt_llm_model,
    load_config,
)
from korina.services.model_catalog import (
    discover_local_gguf_models,
    discover_lmstudio_catalog_models,
    llm_models_for,
)
from korina.util.labels import display_model_label
from korina.util.paths import LMSTUDIO_MODEL, WHISPER_MODEL_CHOICES

router = APIRouter()


@router.get('/api/models')
def models(
    llm_base_url: Optional[str] = Query(None),
    llm_api_key_env: Optional[str] = Query(None),
    stt_llm_base_url: Optional[str] = Query(None),
    stt_llm_api_key_env: Optional[str] = Query(None),
):
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
    # Phase 4.2: per-model capabilities for the multimodal-STT filter
    # and the response-LLM info dropdown. Cached per request so we
    # don't recompute on every model in the list.
    from korina.services.model_capability import get_model_capability
    from korina.config import config_multimodal_stt_model_allowlist
    _allowlist = config_multimodal_stt_model_allowlist(config)

    def _cap_for(mid: str) -> dict:
        cap = get_model_capability(mid, allowlist=_allowlist)
        # Strip the internal _inference key before returning.
        return {k: v for k, v in cap.items() if not k.startswith('_')}

    llm_models_capabilities = {m: _cap_for(m) for m in llm_models}
    stt_llm_models_capabilities = {m: _cap_for(m) for m in stt_llm_models}
    return {
        'whisper_models': WHISPER_MODEL_CHOICES,
        'llm_models': llm_models,
        'llm_models_capabilities': llm_models_capabilities,
        'llm_default': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'llm_base_url': llm_base,
        'llm_error': llm_error,
        'stt_llm_models': stt_llm_models,
        'stt_llm_models_capabilities': stt_llm_models_capabilities,
        'stt_llm_default': config_stt_llm_model(config),
        'stt_llm_base_url': stt_base,
        'stt_llm_error': stt_llm_error,
        'llama_cpp_local_models': discover_local_gguf_models(),
        'lmstudio_catalog_models': discover_lmstudio_catalog_models(),
        'labels': {mid: display_model_label(mid) for mid in list(dict.fromkeys(llm_models + stt_llm_models + discover_local_gguf_models()))},
    }