from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.get('/')
def index():
    return _ctx['_route_index']()
