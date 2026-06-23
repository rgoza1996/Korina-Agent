"""GET / route — serves index.html from the configured path.

Phase 1.9: inlined from the monolith's _route_index helper.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from korina.util.paths import INDEX_PATH

router = APIRouter()


@router.get('/')
def index():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail='index.html missing')
    return FileResponse(INDEX_PATH)