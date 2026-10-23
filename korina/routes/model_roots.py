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

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from korina import config as config_store
from korina.config import (
    config_local_model_roots,
    load_config,
)
from korina.model_roots import ModelRootList, MAX_MODEL_ROOT_PATH_CHARS, validate_model_root_paths
from korina.routes.validation import SanitizedValidationRoute
from korina.schemas import ModelName
from korina.services.model_catalog import AmbiguousProjectorError, discover_local_gguf_models
from korina.services.provider_manager import (
    ProviderRoleConflict,
    InvalidLocalModelPath,
    _resolve_llama_cpp_model_id,
    prepare_provider,
    start_llama_server,
    provider_activation_lock,
)

router = APIRouter(route_class=SanitizedValidationRoute)


class ModelRootsRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    roots: ModelRootList
    append: bool = False


class LlamaRefreshRequest(BaseModel):
    model: Optional[ModelName] = None


def _stats_for(root: str) -> dict:
    try:
        p = Path(root)
        if '\x00' in root:
            raise ValueError('path contains NUL')
        exists = p.exists()
        is_dir = p.is_dir() if exists else False
        reason = '' if is_dir else ('not_found' if not exists else 'not_a_directory')
    except (OSError, ValueError) as exc:
        exists = is_dir = False
        reason = f'invalid_path: {getattr(exc, "strerror", None) or type(exc).__name__}'
    return {
        'path': root,
        'exists': exists,
        'is_directory': is_dir,
        'reason': reason,
    }


@router.get('/api/models/roots')
def get_model_roots():
    """Return the configured GGUF roots (from config + env fallback)."""
    roots = config_local_model_roots(load_config())
    return {'roots': [_stats_for(r) for r in roots]}


@router.post('/api/models/roots')
def post_model_roots(req: ModelRootsRequest):
    """Replace roots, or atomically append when the caller explicitly asks."""
    try:
        cleaned = validate_model_root_paths(list(dict.fromkeys(req.roots)))
        mutate = config_store.append_local_model_roots if req.append else config_store.set_local_model_roots
        persisted = mutate(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {'ok': True, 'roots': [_stats_for(r) for r in persisted], 'count': len(persisted)}


@router.delete('/api/models/roots')
def delete_model_roots(path: str = Query(..., max_length=MAX_MODEL_ROOT_PATH_CHARS * 2)):
    """Remove a single root from config. Does not touch other roots."""
    target = str(path or '').strip()
    if not target:
        raise HTTPException(status_code=422, detail='path query parameter is required')
    # A larger removal-only ceiling also permits repairing older invalid paths.
    removed, persisted = config_store.remove_local_model_root(target)
    return {'ok': True, 'removed': removed, 'roots': [_stats_for(r) for r in persisted]}


@router.post('/api/llm/llama/refresh')
def refresh_llama(req: LlamaRefreshRequest = LlamaRefreshRequest()):
    """Re-walk the GGUF roots and (if llama.cpp is the active provider) restart llama-server.

    Resolves `req.model` to an absolute path if needed (mirrors the activate flow),
    then rewrites the systemd unit and restarts. Non-fatal failures (e.g. llama.cpp
    is not active) return 200 with `restarted: false` and the discovered list.
    """
    if not provider_activation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail='Another provider activation is in progress')
    try:
        return _refresh_llama(req)
    finally:
        provider_activation_lock.release()


def _refresh_llama(req: LlamaRefreshRequest):
    cfg = load_config()
    provider = str(cfg.get('llm_provider') or '').strip().lower()
    selected = (req.model or cfg.get('lm_model') or '').strip()
    resolved = _resolve_llama_cpp_model_id(selected) if selected else ''
    found = discover_local_gguf_models()
    if provider == 'llama.cpp' and resolved:
        try:
            resolved, _, _ = prepare_provider(provider, cfg, resolved, restart=True)
        except (InvalidLocalModelPath, AmbiguousProjectorError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProviderRoleConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f'llama.cpp refresh preflight failed: {exc}') from exc
    try:
        cfg, cleared_audio_probes = config_store.prepare_llama_refresh(provider, str(cfg.get('lm_model') or '').strip(), resolved)
    except config_store.ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    payload = {
        'cleared_audio_probes': cleared_audio_probes,
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
        # The atomic save keeps concurrent settings/cache writes. Recheck its
        # current role snapshot before the start helper writes the unit once.
        resolved, _, _ = prepare_provider(provider, cfg, resolved, restart=True)
        start_llama_server(resolved, config=cfg)
        payload['restarted'] = True
        payload['model'] = resolved
    except ProviderRoleConflict as exc:
        raise HTTPException(
            status_code=409,
            detail=f'Refresh selection/cache was saved, but restart was blocked: {exc}',
        ) from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Refresh selection/cache was saved, but restart failed: {e}') from e
    return payload
