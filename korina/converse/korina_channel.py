"""Default ConverseChannel implementation, backed by agent_gateway().

Wraps the in-process ``KorinaAgentAdapter`` (reached via
``agent_gateway()``) so Converse code calls into a ``ConverseChannel``,
not directly into the legacy service.

Adapters (Hermes, OpenClaw, ...) implement ``ConverseChannel`` against
their own backends and register via ``register_converse_channel``.

Notes on schema vs events:
    * ``UserTurn.text`` is a single ``str`` — we flatten the transcript
      list to a space-joined string, since the adapter protocol does not
      expose the older ``transcript``/``delivery_mode`` fields.
    * The adapter emits events whose ``type`` field is one of
      ``"state_report"`` / ``"injection"`` / ``"permission_request"``.
    * Completion is a fresh report/error event after the submission cursor.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
import time
from typing import AsyncIterator, List

from korina.agents import AgentAdapter, AgentHttpBridge
from korina.schemas import UserTurn

from .protocol import ConverseChannel, ConverseRequest, ConverseResponse
from .registry import register_converse_channel


_MAX_POLL_SECONDS = 30.0
_POLL_INTERVAL = 0.1  # 100ms; caps at ~300 polls/send for 30s ceiling


class KorinaConverseChannel(ConverseChannel):
    """Bridge between the ``ConverseChannel`` protocol and ``agent_gateway()``.

    Semantics:
        * ``send()`` — submit once and wait for a fresh completion event.
        * ``stream()``— yields the interim "thinking" event, then calls
          ``send()`` once for the final. No double-submit.
        * ``cancel()`` — invalidate the wait and reset queued adapter work.
    """

    name = "korina"

    def __init__(self) -> None:
        # Lazy import: gateway instantiates KorinaAgentAdapter on first call,
        # which pulls in agent_service. Keep the import out of this module's
        # import-time path to avoid cyclic imports during Phase 3+ wiring.
        from korina.agents.gateway import agent_gateway
        self._agent_gateway = agent_gateway
        self._send_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._generation = 0
        self._active_adapter: AgentAdapter | None = None
        self._pending_resets = 0

    # ---- helpers (testable; exposed at module level too) ------------

    @staticmethod
    def _flatten_transcript(transcript: List[dict]) -> str:
        """Flatten a transcript list into a single space-joined string.

        Drops assistant turns (never replay them as user input). Empty
        or non-dict turns are skipped.
        """
        parts = []
        for turn in transcript or []:
            if not isinstance(turn, dict):
                continue
            text = turn.get("text") or turn.get("content") or ""
            if not text:
                continue
            role = (turn.get("role") or "user").lower()
            if role == "assistant":
                continue
            parts.append(str(text).strip())
        return " ".join(parts).strip()

    @classmethod
    def turn_from_request(cls, req: ConverseRequest) -> UserTurn:
        """Coerce a ConverseRequest into the schema's UserTurn.

        ``UserTurn.text`` is ``str`` (not a list); the older transcript/
        delivery_mode/reason fields are no longer on the schema.
        """
        text = cls._flatten_transcript(req.transcript)
        meta = req.metadata or {}
        session_id = str(meta.get("session_id", "")) or ""
        return UserTurn(
            text=text,
            session_id=session_id,
            turn_count=int(meta.get("turn_count", 0) or 0),
        )

    async def _poll_until_report(
        self, adapter: AgentAdapter, started_at: float, cursor: int, generation: int
    ) -> tuple[str, str, int, int, str | None]:
        """Wait for a new report event instead of replaying last_report.

        The cursor is captured before submission. Old reports remain useful
        diagnostics but can never complete the next turn.
        """
        scanned = 0
        while time.monotonic() - started_at < _MAX_POLL_SECONDS:
            if self._cancelled(generation):
                return '', 'idle', scanned, cursor, 'cancelled'
            try:
                cursor, events = await asyncio.to_thread(adapter.poll_events, cursor)
                if self._cancelled(generation):
                    return '', 'idle', scanned, cursor, 'cancelled'
                scanned += len(events)
                for event in events:
                    kind = event.get('type')
                    if kind in {'state_report', 'permission_request'}:
                        text = str(event.get('report') or event.get('message') or '')
                        if text:
                            return text, 'idle', scanned, cursor, None
                    if kind in {'agent_error', 'error'}:
                        error = str(event.get('message') or event.get('error') or 'agent error')
                        return '', 'idle', scanned, cursor, error
                    if event.get('status') == 'duplicate_report_suppressed':
                        snapshot = await asyncio.to_thread(AgentHttpBridge(adapter).snapshot)
                        if self._cancelled(generation):
                            return '', 'idle', scanned, cursor, 'cancelled'
                        return str(snapshot.get('last_report') or ''), 'idle', scanned, cursor, None
                    if event.get('status') == 'empty_report_suppressed':
                        return '', 'idle', scanned, cursor, None
            except Exception as exc:
                if self._cancelled(generation):
                    return '', 'idle', scanned, cursor, 'cancelled'
                return '', 'idle', scanned, cursor, f'poll_events failed: {exc}'
            await asyncio.sleep(_POLL_INTERVAL)
        return '', 'idle', scanned, cursor, 'timeout'

    # ---- ConverseChannel surface ------------------------------------

    def _adapter(self):
        return self._agent_gateway()

    def _cancelled(self, generation: int) -> bool:
        with self._lifecycle_lock:
            return generation != self._generation

    @asynccontextmanager
    async def _serialize_sends(self):
        # Channels can be shared by several event loops; do not bind the lock
        # to one loop or block that loop while another caller holds it.
        while not self._send_lock.acquire(blocking=False):
            await asyncio.sleep(_POLL_INTERVAL)
        try:
            # Claim the adapter and cancellation epoch together. A queued send
            # must not submit while an earlier cancel is resetting that adapter.
            while True:
                with self._lifecycle_lock:
                    if not self._pending_resets:
                        adapter = self._adapter()
                        generation = self._generation
                        self._active_adapter = adapter
                        break
                await asyncio.sleep(_POLL_INTERVAL)
            try:
                yield adapter, generation
            finally:
                with self._lifecycle_lock:
                    self._active_adapter = None
        finally:
            self._send_lock.release()

    async def send(self, req: ConverseRequest) -> ConverseResponse:
        # The adapter is a single conversational worker. Serialize awaited
        # channel sends so each caller receives its own completion event.
        async with self._serialize_sends() as (adapter, generation):
            try:
                turn = self.turn_from_request(req)
                cursor, _ = await asyncio.to_thread(adapter.poll_events, 0)
                if self._cancelled(generation):
                    return ConverseResponse(error='cancelled')
                await asyncio.to_thread(adapter.submit_turn, turn)
            except Exception as exc:
                if self._cancelled(generation):
                    return ConverseResponse(error='cancelled')
                return ConverseResponse(text='', finished=True, error=f'submit failed: {exc}')
            text, status, scanned, cursor, error = await self._poll_until_report(
                adapter, time.monotonic(), cursor, generation
            )
            if self._cancelled(generation):
                text, error = '', 'cancelled'
            return ConverseResponse(
                text=text, finished=True, error=error,
                metadata={'status': status, 'events_scanned': scanned, 'next_cursor': cursor},
            )

    async def stream(
        self, req: ConverseRequest
    ) -> AsyncIterator[ConverseResponse]:
        """Stream: emit the interim "thinking" event, then call ``send()`` once.

        We intentionally delegate to ``send()`` rather than running our
        own submit+poll loop. That keeps the single-submit guarantee and
        lets adapters swap the polling implementation without two code
        paths to keep in sync.
        """
        yield ConverseResponse(
            text="",
            finished=False,
            metadata={"phase": "thinking"},
        )
        final = await self.send(req)
        yield final

    async def cancel(self) -> None:
        """Invalidate this channel wait and discard queued agent work."""
        with self._lifecycle_lock:
            self._generation += 1
            adapter = self._active_adapter if self._active_adapter is not None else self._adapter()
            self._pending_resets += 1

        def reset_adapter():
            try:
                adapter.reset()
            finally:
                with self._lifecycle_lock:
                    self._pending_resets -= 1

        # Cancelling the caller cannot stop a synchronous reset already running.
        # Keep new sends blocked until that worker has really finished.
        await asyncio.shield(asyncio.to_thread(reset_adapter))

    async def health(self) -> dict:
        adapter = self._adapter()
        try:
            status = await asyncio.to_thread(adapter.health_check)
            ok = bool(isinstance(status, dict) and status.get("ok"))
            return {"ok": ok, "channel": self.name, "status": status}
        except Exception as e:  # pragma: no cover
            return {"ok": False, "channel": self.name, "error": str(e)}


_DEFAULT_REGISTERED = False


def reset_default_registered() -> None:
    """Reset the module-level _DEFAULT_REGISTERED flag.

    Tests that call reset_for_testing() must also call this so the
    next ensure_default_registered() actually re-registers.
    """
    global _DEFAULT_REGISTERED
    _DEFAULT_REGISTERED = False


def ensure_default_registered() -> None:
    """Register the default ``KorinaConverseChannel`` if nothing is active yet.

    Idempotent. Phase 5 startup code should call this exactly once at app
    boot, before the first HTTP request lands.
    """
    global _DEFAULT_REGISTERED
    if _DEFAULT_REGISTERED:
        return
    register_converse_channel(KorinaConverseChannel())
    _DEFAULT_REGISTERED = True


__all__ = ["KorinaConverseChannel", "ensure_default_registered", "reset_default_registered"]
