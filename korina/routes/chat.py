"""POST /api/chat — single-turn completion and optional provider observations."""

from __future__ import annotations

import time
from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from korina.schemas import ChatRequest
from korina.services.response_llm import ResponseChatResult, response_llm_chat_result
from korina.runtime.json_http import RequestCancelledError
from korina.runtime.request_owner import RequestOwner, check_current

router = APIRouter()


@router.post('/api/chat')
async def chat(req: ChatRequest, request: Request):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail='No message provided')
    started = time.time()
    try:
        result = await _owned_chat_result(req, request)
        return JSONResponse({
            **asdict(result),
            'seconds': time.time() - started,
            'model': result.request_settings.model,
        })
    except RequestCancelledError as exc:
        raise HTTPException(status_code=499, detail='Chat request was cancelled') from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _owned_chat_result(req: ChatRequest, request: Request) -> ResponseChatResult:
    """Release connected provider I/O when the browser abandons this reply.

    The watcher is an async task; HTTP work keeps using one existing pool
    worker. Closing that connection does not establish remote compute abort.
    """
    owner = RequestOwner()
    async with owner.watch(request):
        result = await run_in_threadpool(response_llm_chat_result, req, is_current=owner.is_current)
        check_current(owner.is_current)
        return result
