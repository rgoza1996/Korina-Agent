"""POST /api/llm/provider/activate — switch active LLM provider.

Phase 1.9: inlined from the monolith's _route_activate_provider helper.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from korina.config import load_config, save_config, synchronize_llm_dependents
from korina.schemas import ProviderActivateRequest
from korina.services.provider_manager import activate_llm_provider
from korina.util.presets import provider_preset_base_url

router = APIRouter()


@router.post('/api/llm/provider/activate')
async def activate_provider(req: ProviderActivateRequest):
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