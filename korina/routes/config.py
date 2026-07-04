"""GET / POST /api/config — read and update Korina runtime config.

Phase 1.9: inlined from the monolith's _route_get_config / _route_update_config helpers.
"""

from __future__ import annotations

from fastapi import APIRouter

from korina.config import (
    CONFIG_KEYS,
    config_llm_reasoning,
    load_config,
    save_config,
    synchronize_llm_dependents,
)
from korina.services.provider_manager import activate_llm_provider

router = APIRouter()


@router.get('/api/config')
def get_config():
    return load_config()


@router.post('/api/config')
async def update_config(payload: dict):
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