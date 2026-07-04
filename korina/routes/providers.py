"""POST /api/llm/provider/activate — switch active LLM provider.

Phase 1.9: inlined from the monolith's _route_activate_provider helper.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from korina.config import load_config, save_config, synchronize_llm_dependents
from korina.schemas import ProviderActivateRequest
from korina.services.provider_manager import activate_llm_provider
from korina.services.active_probe import probe_audio_support
from korina.util.presets import provider_preset_base_url

router = APIRouter()


@router.post('/api/llm/provider/activate')
async def activate_provider(req: ProviderActivateRequest):
    config = load_config()
    provider = str(req.provider or config.get('llm_provider') or 'openai-compatible').strip()
    # Phase 4.5: clear probe cache entries for this provider. Re-activating
    # may have changed base_url or model, so old probe failures no longer apply.
    # Reload after clearing so the later save_config(current) cannot resurrect
    # stale audio_unsupported entries from the pre-clear config snapshot.
    from korina.config import clear_audio_unsupported_for_provider
    cleared = clear_audio_unsupported_for_provider(provider)
    if cleared:
        print(f"[activate] cleared {cleared} audio probe cache entries for {provider}")
        config = load_config()  # reload after cache clear
    current = dict(config)
    current['llm_provider'] = provider
    preset = provider_preset_base_url(provider)
    if provider == 'openai-compatible':
        current['llm_base_url'] = str(config.get('llm_base_url') or current.get('llm_base_url') or '').strip()
    elif preset:
        current['llm_base_url'] = preset
    if req.model:
        current['lm_model'] = str(req.model).strip()
    # Phase 4.4: pre-flight provider/model compatibility check.
    if req.model:
        from korina.services.provider_manager import provider_supports_model
        supported, reason = provider_supports_model(provider, str(req.model).strip())
        if not supported:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "incompatible_provider_model",
                    "provider": provider,
                    "model": str(req.model).strip(),
                    "reason": reason,
                },
            )
    current = synchronize_llm_dependents(current, config)
    saved = save_config(current)
    try:
        result = activate_llm_provider(provider, saved, model=req.model)
        return {'ok': True, 'saved': saved, 'activation': result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/audio-probe")
def list_audio_probes():
    """List all cached audio-unsupported entries. Used by the settings UI."""
    from korina.config import config_audio_unsupported
    return {"entries": config_audio_unsupported()}


@router.delete("/api/audio-probe")
def clear_audio_probe(provider: str, base_url: str, model: str):
    """Clear a single audio-unsupported cache entry. Power-user escape hatch.

    Query params (NOT path params) -- two consecutive FastAPI {x:path}
    converters greedily consume the URL and break for HF-style ids
    like `org/repo/model.gguf`.
    """
    from korina.services.audio_probe import triple_key
    from korina.config import clear_audio_unsupported
    triple = triple_key(provider, base_url, model)
    removed = clear_audio_unsupported(provider, base_url, model)
    return {"triple": triple, "removed": removed}


class ProbeModelsRequest(BaseModel):
    provider: str = ''
    base_url: str = ''
    models: list[str] = Field(default_factory=list, max_length=64)


@router.post('/api/llm/probe-models')
def probe_models(req: ProbeModelsRequest):
    """Phase 5.0: actively probe audio-capability of a list of models.

    Drives the existing ``active_probe.probe_audio_support`` for each model,
    which fires a tiny synthetic audio request at the configured STT
    endpoint and classifies the response. Cacheable failures are persisted
    to the same ``audio_unsupported`` cache the dropdown consults, so the
    next ``/api/models`` call reflects the new state immediately.

    Returns one result per model, with a ``cached`` boolean indicating
    whether the failure (if any) was written to the persistent cache.
    """
    from korina.services.audio_probe import triple_key
    from korina.config import config_audio_unsupported

    provider_id = str(req.provider or '').strip().lower()
    base_url_clean = str(req.base_url or '').strip()
    if not provider_id:
        raise HTTPException(status_code=400, detail='provider is required')
    if not base_url_clean:
        raise HTTPException(status_code=400, detail='base_url is required')
    if not req.models:
        raise HTTPException(status_code=400, detail='models list is empty')

    cache_before = config_audio_unsupported()
    triple_prefix = f"{provider_id}::"

    results: dict[str, dict] = {}
    for model in req.models:
        mid = str(model or '').strip()
        if not mid:
            continue
        probe = probe_audio_support(provider_id, base_url_clean, mid)
        triple = triple_key(provider_id, base_url_clean, mid)
        results[mid] = {
            'supported': probe.supported,
            'reason': probe.reason,
            'latency_ms': probe.latency_ms,
            'status': probe.status,
            'previously_cached': triple in cache_before,
        }

    # Snapshot the cache AFTER the probes so the UI can tell which
    # models are currently in the persistent audio_unsupported set.
    cache_after = config_audio_unsupported()
    for mid in results:
        triple = triple_key(provider_id, base_url_clean, mid)
        results[mid]['cached'] = triple in cache_after

    return {
        'ok': True,
        'provider': provider_id,
        'base_url': base_url_clean,
        'triple_prefix': triple_prefix,
        'results': results,
    }
