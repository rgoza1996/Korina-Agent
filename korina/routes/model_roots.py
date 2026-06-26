"""POST /api/llm/llama/refresh — re-walk GGUF roots and rewrite llama-server.

GET  /api/models/roots       — list configured roots with `exists` flag
POST /api/models/roots       — replace configured roots (validated, persisted)
DELETE /api/models/roots    — remove a single root from config

These endpoints close the loop between the Settings UI and the local
GGUF catalog so users can point llama.cpp at new directories without
editing JSON or restarting the service.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from korina.config import (
    config_local_model_roots,
    load_config,
    save_config,
    set_local_model_roots,
)
from korina.services.model_catalog import discover_local_gguf_models
from korina.services.provider_manager import (
    _resolve_llama_cpp_model_id,
    start_llama_server,
    write_llama_server_unit,
)

router = APIRouter()


class ModelRootsRequest(BaseModel):
    roots: list[str]


class LlamaRefreshRequest(BaseModel):
    model: Optional[str] = None


def _stats_for(root: str) -> dict:
    p = Path(root)
    exists = p.exists()
    is_dir = p.is_dir() if exists else False
    return {
        'path': root,
        'exists': exists,
        'is_directory': is_dir,
        'reason': '' if is_dir else ('not_found' if not exists else 'not_a_directory'),
    }


@router.get('/api/models/roots')
def get_model_roots():
    """Return the configured GGUF roots (from config + env fallback)."""
    roots = config_local_model_roots(load_config())
    return {'roots': [_stats_for(r) for r in roots]}


@router.post('/api/models/roots')
def post_model_roots(req: ModelRootsRequest):
    """Replace the configured GGUF root list. Validates each path."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in req.roots or []:
        if not isinstance(entry, str):
            raise HTTPException(status_code=422, detail=f'root entries must be strings; got {type(entry).__name__}')
        v = entry.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        cleaned.append(v)
    if not cleaned:
        raise HTTPException(status_code=422, detail='at least one root path is required')
    for v in cleaned:
        p = Path(v)
        if not p.exists():
            raise HTTPException(status_code=400, detail=f'path not found: {v}')
        if not p.is_dir():
            raise HTTPException(status_code=400, detail=f'not a directory: {v}')
    persisted = set_local_model_roots(cleaned)
    return {'ok': True, 'roots': [_stats_for(r) for r in persisted], 'count': len(persisted)}


@router.delete('/api/models/roots')
def delete_model_roots(path: str):
    """Remove a single root from config. Does not touch other roots."""
    target = str(path or '').strip()
    if not target:
        raise HTTPException(status_code=422, detail='path query parameter is required')
    current = config_local_model_roots(load_config())
    if target not in current:
        return {'ok': True, 'removed': 0, 'roots': current}
    next_roots = [r for r in current if r != target]
    if next_roots:
        persisted = set_local_model_roots(next_roots)
    else:
        # Persist an explicit empty list so the resolver does not fall back
        # to the env default — the user's intent was to delete that root.
        cfg = load_config()
        cfg['local_model_roots'] = []
        save_config(cfg)
        persisted = []
    return {'ok': True, 'removed': 1, 'roots': [_stats_for(r) for r in persisted]}


@router.post('/api/llm/llama/refresh')
def refresh_llama(req: LlamaRefreshRequest = LlamaRefreshRequest()):
    """Re-walk the GGUF roots and (if llama.cpp is the active provider) restart llama-server.

    Resolves `req.model` to an absolute path if needed (mirrors the activate flow),
    then rewrites the systemd unit and restarts. Non-fatal failures (e.g. llama.cpp
    is not active) return 200 with `restarted: false` and the discovered list.
    """
    cfg = load_config()
    provider = str(cfg.get('llm_provider') or '').strip().lower()
    selected = (req.model or cfg.get('lm_model') or '').strip()
    resolved = _resolve_llama_cpp_model_id(selected) if selected else ''

    found = discover_local_gguf_models()
    payload = {
        'ok': True,
        'roots': config_local_model_roots(cfg),
        'discovered_count': len(found),
        'discovered': found[:50],
        'restarted': False,
        'active_provider': provider,
    }
    if provider != 'llama.cpp':
        payload['note'] = 'llama.cpp is not the active provider; roots are updated but no service restart was performed.'
        return payload
    if not resolved:
        payload['note'] = 'no model selected; choose a model in Settings before refreshing llama.cpp.'
        return payload
    try:
        if resolved and resolved != cfg.get('lm_model'):
            cfg['lm_model'] = resolved
            save_config(cfg)
        write_llama_server_unit(resolved, config=cfg)
        start_llama_server(resolved, config=cfg)
        payload['restarted'] = True
        payload['model'] = resolved
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'llama.cpp refresh failed: {e}')
    return payload