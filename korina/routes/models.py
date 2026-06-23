from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.get('/api/models')
def models(
    llm_base_url: Optional[str] = Query(None),
    llm_api_key_env: Optional[str] = Query(None),
    stt_llm_base_url: Optional[str] = Query(None),
    stt_llm_api_key_env: Optional[str] = Query(None),
):
    return _ctx['_route_models'](llm_base_url, llm_api_key_env, stt_llm_base_url, stt_llm_api_key_env)
