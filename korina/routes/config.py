from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.get('/api/config')
def get_config():
    return _ctx['_route_get_config']()


@router.post('/api/config')
async def update_config(request: Request):
    return _ctx['_route_update_config'](await request.json())
