"""POST /api/chat — single-turn chat completion via the configured LLM.

Phase 1.9: inlined from the monolith's _route_chat helper.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from korina.config import load_config
from korina.schemas import ChatRequest
from korina.services.response_llm import response_llm_chat
from korina.util.paths import LMSTUDIO_MODEL

router = APIRouter()


@router.post('/api/chat')
async def chat(request: Request):
    try:
        req = ChatRequest(**(await request.json()))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())
    if not req.message.strip():
        raise HTTPException(status_code=400, detail='No message provided')
    started = time.time()
    try:
        reply = response_llm_chat(req)
        return JSONResponse({
            'reply': reply,
            'seconds': time.time() - started,
            'model': req.model or str(load_config().get('lm_model') or LMSTUDIO_MODEL),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))