from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.post('/api/chat')
async def chat(request: Request):
    req = _ctx['ChatRequest'](**(await request.json()))
    return _ctx['_route_chat'](req)
