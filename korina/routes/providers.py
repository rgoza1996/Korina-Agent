from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from korina.schemas import ProviderActivateRequest

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.post('/api/llm/provider/activate')
async def activate_provider(request: Request):
    try:
        req = ProviderActivateRequest(**(await request.json()))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    return _ctx['_route_activate_provider'](req)
