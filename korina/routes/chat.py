"""POST /api/chat — single-turn chat completion via the configured LLM.

Phase 1.9: inlined from the monolith's _route_chat helper.
"""

from __future__ import annotations

import time
import threading

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from korina.config import load_config
from korina.schemas import ChatRequest
from korina.services.response_llm import response_llm_chat
from korina.util.paths import LMSTUDIO_MODEL
from korina.runtime.json_http import RequestCancelledError

router = APIRouter()


@router.post('/api/chat')
async def chat(req: ChatRequest, request: Request):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail='No message provided')
    started = time.time()
    try:
        reply = await _owned_chat_reply(req, request)
        return JSONResponse({
            'reply': reply,
            'seconds': time.time() - started,
            'model': req.model or str(load_config().get('lm_model') or LMSTUDIO_MODEL),
        })
    except RequestCancelledError as exc:
        raise HTTPException(status_code=499, detail='Chat request was cancelled') from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _owned_chat_reply(req: ChatRequest, request: Request) -> str:
    """Release connected provider I/O when the browser abandons this reply.

    The watcher is an async task; HTTP work keeps using one existing pool
    worker. Closing that connection does not establish remote compute abort.
    """
    disconnected = threading.Event()
    failure: BaseException | None = None
    reply = ''

    async def watch_disconnect():
        try:
            while not disconnected.is_set():
                if await request.is_disconnected():
                    disconnected.set()
                    return
                await anyio.sleep(.05)
        except Exception:
            pass  # A broken receive channel also retires this owner.
        finally:
            # Outer ASGI cancellation can stop this task while the pool await
            # is shielded. Retire its HTTP owner before waiting for that worker.
            disconnected.set()

    async with anyio.create_task_group() as group:
        group.start_soon(watch_disconnect)
        try:
            reply = await run_in_threadpool(response_llm_chat, req, is_current=lambda: not disconnected.is_set())
            if disconnected.is_set():
                raise RequestCancelledError('provider request was cancelled')
        except BaseException as exc:
            failure = exc
        finally:
            disconnected.set()
            group.cancel_scope.cancel()
    # Re-raise outside the task group so upstream errors retain their original
    # type/message rather than becoming an ExceptionGroup response diagnostic.
    if failure is not None:
        raise failure
    return reply
