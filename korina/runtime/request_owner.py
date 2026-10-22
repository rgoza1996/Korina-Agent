"""Async request ownership for cancellable HTTP work in existing pool workers.

Retiring an owner closes its connected provider I/O. It does not establish
remote compute cancellation or interrupt native inference already executing.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

import anyio
from starlette.requests import Request
from starlette.types import Receive

from korina.runtime.json_http import RequestCancelledError


def check_current(is_current: Callable[[], bool] | None) -> None:
    if is_current is not None and not is_current():
        raise RequestCancelledError('provider request was cancelled')


class RequestOwner:
    def __init__(self) -> None:
        self._retired = threading.Event()

    def is_current(self) -> bool:
        return not self._retired.is_set()

    def retire(self) -> None:
        self._retired.set()

    def receive(self, receive: Receive) -> Receive:
        """Observe disconnect even when StreamingResponse consumes receive."""
        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.disconnect':
                self.retire()
            return message
        return observed_receive

    @asynccontextmanager
    async def watch(self, request: Request):
        failure: BaseException | None = None

        async def watch_disconnect():
            try:
                while self.is_current():
                    if await request.is_disconnected():
                        return
                    await anyio.sleep(.05)
            except Exception:
                pass  # A broken receive channel also retires the owner.
            finally:
                # Outer ASGI cancellation can cancel this task while the pool
                # await is shielded. Retire I/O before waiting for that worker.
                self.retire()

        async with anyio.create_task_group() as group:
            group.start_soon(watch_disconnect)
            try:
                yield self
            except BaseException as exc:
                failure = exc
            finally:
                self.retire()
                group.cancel_scope.cancel()
        # Keep upstream/cancellation exception types out of ExceptionGroup.
        if failure is not None:
            raise failure
