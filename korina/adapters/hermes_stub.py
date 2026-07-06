"""Hermes adapter (STUB) -- Phase 5 Commit 5.

This stub exists to validate the ConverseChannel contract for an
external agent type. Hermes Agent is the intended consumer of this
adapter; OpenClaw or other OpenAI-compatible agents will follow the
same pattern. The stub interface covers:

  - name = "hermes-stub"                       (registry id)
  - send(req) -> ConverseResponse              (single-turn reply)
  - stream(req) -> AsyncIterator               (SSE-style)
  - cancel() -> None                           (idempotent)
  - health() -> dict                           (liveness probe)

It does NOT make any network calls. Wire this behind a real Hermes
adapter by subclassing ConverseChannel with a real Hermes client;
the registry + routes do not need to change.
"""
from __future__ import annotations

from typing import AsyncIterator

from korina.converse import ConverseChannel, ConverseRequest, ConverseResponse
from korina.converse.registry import register_converse_channel


class HermesStubChannel(ConverseChannel):
    """Stub Hermes adapter implementing the ConverseChannel protocol.

    Returns a canned reply regardless of the request transcript. The
    canned reply text contains the channel name so that round-trip
    behavior is observable in tests:

        >>> asyncio.run(ch.send(req)).text
        "[hermes-stub] ok"

    The stub deliberately performs no I/O, no scheduling, and no state
    mutation: this keeps the test surface to just the protocol shape.
    """

    name = "hermes-stub"

    async def send(self, req: ConverseRequest) -> ConverseResponse:
        """Single-turn reply: a canned `[hermes-stub] ok` string."""
        return ConverseResponse(text="[hermes-stub] ok", finished=True)

    async def stream(self, req: ConverseRequest) -> AsyncIterator[ConverseResponse]:
        """Stream a single partial then a final chunk."""
        yield ConverseResponse(text="[hermes-stub] ", finished=False)
        yield ConverseResponse(text="ok", finished=True)

    async def cancel(self) -> None:
        """Stub has no in-flight state to cancel."""
        return None

    async def health(self) -> dict:
        """Stub is always healthy."""
        return {"ok": True, "channel": self.name, "stub": True}


_STUB_REGISTERED = False


def register_hermes_stub(register: bool = True) -> None:
    """Register (or unregister) the Hermes stub under name "hermes-stub".

    Tests and the startup hook opt in via this function. It is NOT
    called at import time -- importing korina.adapters.hermes_stub
    must not mutate the global registry, because the default channel
    should remain `korina` until something explicitly swaps it.

    Calling with register=False removes a previously registered stub
    (used by tests to leave global state clean at teardown). It is
    a no-op if the stub was never registered.
    """
    global _STUB_REGISTERED
    if register:
        register_converse_channel(HermesStubChannel())
        _STUB_REGISTERED = True
        return
    if not _STUB_REGISTERED:
        return
    from korina.converse.registry import unregister_converse_channel
    unregister_converse_channel("hermes-stub")
    _STUB_REGISTERED = False


__all__ = ["HermesStubChannel", "register_hermes_stub"]
