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
    * Completion signal is ``agent_state.last_report`` becoming
      non-empty (it's a plain ``str`` on the adapter state).
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, List

from korina.schemas import UserTurn

from .protocol import ConverseChannel, ConverseRequest, ConverseResponse
from .registry import register_converse_channel


_MAX_POLL_SECONDS = 30.0
_POLL_INTERVAL = 0.1  # 100ms; caps at ~300 polls/send for 30s ceiling


class KorinaConverseChannel(ConverseChannel):
    """Bridge between the ``ConverseChannel`` protocol and ``agent_gateway()``.

    Semantics:
        * ``send()``  — submit a turn, poll ``last_report`` until it is
          non-empty or the deadline passes. Single submission per call.
        * ``stream()``— yields the interim "thinking" event, then calls
          ``send()`` once for the final. No double-submit.
        * ``cancel()``— placeholder; the wrapped adapter has no
          per-channel cancellation surface today.
    """

    name = "korina"

    def __init__(self) -> None:
        # Lazy import: gateway instantiates KorinaAgentAdapter on first call,
        # which pulls in agent_service. Keep the import out of this module's
        # import-time path to avoid cyclic imports during Phase 3+ wiring.
        from korina.agents.gateway import agent_gateway
        self._agent_gateway = agent_gateway

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

    @staticmethod
    def _poll_state() -> tuple[str, str]:
        """Snapshot adapter state under its lock.

        Returns ``(last_report, status)``. ``last_report`` is a string.
        """
        from korina.agents.state import agent_state
        with agent_state.lock:
            return (
                str(agent_state.last_report or ""),
                str(agent_state.status or "idle"),
            )

    async def _poll_until_report(
        self, started_at: float, cursor: int
    ) -> tuple[str, str, int, int, str | None]:
        """Poll until ``last_report`` is set or we time out.

        Returns ``(text, status, scanned_events, new_cursor, last_error)``.
        ``cursor`` is passed in from the caller; we advance it as events
        are drained via ``adapter.poll_events``.
        """
        adapter = self._adapter()
        last_error: str | None = None
        scanned = 0

        while time.monotonic() - started_at < _MAX_POLL_SECONDS:
            try:
                text, status = await asyncio.to_thread(self._poll_state)
            except Exception as e:  # pragma: no cover
                last_error = f"state probe failed: {e}"
                break

            try:
                cursor, events = await asyncio.to_thread(adapter.poll_events, cursor)
                scanned += len(events)
                for ev in events or []:
                    if not isinstance(ev, dict):
                        continue
                    if ev.get("type") == "error":
                        last_error = str(
                            ev.get("error") or last_error or "agent error"
                        )
            except Exception as e:  # pragma: no cover
                last_error = f"poll_events failed: {e}"

            if text:
                return text, status, scanned, cursor, last_error

            await asyncio.sleep(_POLL_INTERVAL)

        text, status = await asyncio.to_thread(self._poll_state)
        return text or "", status, scanned, cursor, last_error or "timeout"

    # ---- ConverseChannel surface ------------------------------------

    def _adapter(self):
        return self._agent_gateway()

    async def send(self, req: ConverseRequest) -> ConverseResponse:
        adapter = self._adapter()
        turn = self.turn_from_request(req)
        try:
            await asyncio.to_thread(adapter.submit_turn, turn)
        except Exception as e:
            return ConverseResponse(text="", finished=True, error=f"submit failed: {e}")

        started_at = time.monotonic()
        text, status, scanned, cursor, err = await self._poll_until_report(started_at, 0)
        return ConverseResponse(
            text=text or "",
            finished=True,
            error=err,
            metadata={
                "status": status,
                "events_scanned": scanned,
                "next_cursor": cursor,
            },
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
        """No-op placeholder; the wrapped adapter has no per-channel cancellation."""
        return None

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
