from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Query, UploadFile

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.post('/api/transcribe')
async def transcribe(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    return await _ctx['_route_transcribe'](audio, device, model, backend, llm_model)


@router.post('/api/transcribe/partial')
async def transcribe_partial(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    return await _ctx['_route_transcribe_partial'](audio, device, model, backend, llm_model)


@router.post('/api/transcribe/stream')
async def transcribe_stream(
    audio: UploadFile = File(...),
    device: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    backend: str = Query('whisper'),
    llm_model: Optional[str] = Query(None),
):
    return await _ctx['_route_transcribe_stream'](audio, device, model, backend, llm_model)
