from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, Request

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.get('/api/acks')
def acks(voice: str = Query('af_heart'), tag: Optional[str] = Query(None)):
    return _ctx['_route_acks'](voice, tag)


@router.get('/api/acks/status')
def acks_status(voice: str = Query('af_heart')):
    return _ctx['_route_acks_status'](voice)


@router.post('/api/acks/rebuild')
async def acks_rebuild(request: Request):
    return _ctx['_route_acks_rebuild'](await request.json())
