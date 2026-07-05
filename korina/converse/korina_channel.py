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
    * The adapter emits events whose ``type`` field is
      ``"state_report"`` / ``"injection"`` / ``"permission_request"``.
      We match those explicitly instead of guessing the canonical name.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, List

from korina.schemas import UserTurn

from .protocol import ConverseChannel, ConverseRequest, ConverseResponse
from .registry import register_converse_channel


_MAX_POLL_SECONDS = 30.0
_POLL_INTERVAL = 0.1  # 100ms; caps at 300 polls/send for 30s ceiling


# Event-type names produced by the in-process adapter. Match by exact set.
_REPORT_EVENT_TYPES = frozenset({"state_report", "injection"})
_TERMINAL_EVENT_TYPES = frozenset({"idle", "error"})


class KorinaConverseChannel(ConverseChannel):
    """Bridge between the ``ConverseChannel`` protocol and ``agent_gateway()``.

    Semantics:
        * ``send()`` — submit the request, poll ``last_report`` until it
          is non-empty or the deadline passes. The adapter's ``last_report``
          is the canonical completion signal; polled events supplement it.
        * ``stream()`` — yields an interim "thinking" event, polls the
          adapter state itself (no double-poll), then yields the final.
        * ``cancel()`` — placeholder; the wrapped adapter has no
          per-channel cancellation surface today.
    """

    name = "korina"

    def __init__(self) -> None:
        # Lazy import: gateway instantiates KorinaAgentAdapter on first
        # call, which pulls in agent_service — keep the import out of
        # this module's import-time path.
        from korina.agents.gateway import agent_gateway
        self._agent_gateway = agent_gateway

    # ---- helpers -----------------------------------------------------

    def _adapter(self):
        return self._agent_gateway()

    @staticmethod
    def _flatten_transcript(transcript: List[dict]) -> str:
        """Flatten a transcript list into a single space-joined string."""
        parts = []
        for turn in transcript or []:
            if not isinstance(turn, dict):
                continue
            text = turn.get("text") or turn.get("content") or ""
            if not text:
                continue
            role = (turn.get("role") or "user").lower()
            if role == "assistant":
                continue  # never replay assistant turns as user input
            parts.append(str(text).strip())
        return " ".join(parts).strip()

    @staticmethod
    def _turn_from_request(req: ConverseRequest) -> UserTurn:
        """Coerce a ConverseRequest into the schema's UserTurn.

        ``UserTurn.text`` is ``str`` (not a list); the older transcript/
        delivery_mode/reason fields are no longer on the schema.
        """
        text = KorinaConverseChannel._flatten_transcript(req.transcript)
        meta = req.metadata or {}
        session_id = str(meta.get("session_id", "")) or ""
        return UserTurn(
            text=text,
            session_id=session_id,
            turn_count=int(meta.get("turn_count", 0) or 0),
        )

    @staticmethod
    def _poll_state(adapter) -> tuple[str, str, bool]:
        """Read adapter state under its lock.

        Returns ``(last_report, status, busy)``. ``last_report`` is a
        string (the most recent assistant reply), not a dict.
        """
        from korina.agents.state import agent_state

        with agent_state.lock:
            return (
                str(agent_state.last_report or ""),
                str(agent_state.status or "idle"),
                bool(agent_state.busy),
            )

    async def _poll_until_report(self, started_at: float) -> tuple[str, str, int, str | None]:
        """Poll until ``last_report`` is set or we time out.

        Returns ``(text, status, scanned_event_count, last_error)``.
        We rely on ``last_report`` as the canonical completion signal
        rather than ad-hoc event-type matching (which drifted between
        Phase 3 and Phase 4).
        """
        adapter = self._adapter()
        cursor = 0
        last_error: str | None = None
        scanned = 0
        text = ""
        status = "idle"

        while time.monotonic() - started_at < _MAX_POLL_SECONDS:
            try:
                text, status, _ = await asyncio.to_thread(self._poll_state, adapter)
            except Exception as e:  # pragma: no cover
                last_error = f"state probe failed: {e}"
                break

            # Drain any new events once; capture a non-fatal agent error if any.
            try:
                new_cursor, events = await asyncio.to_thread(adapter.poll_events, cursor)
                cursor = new_cursor
                scanned += len(events)
                for ev in events or []:
                    if not isinstance(ev, dict):
                        continue
                    et = ev.get("type") or ""
                    if et == "error":
                        last_error = str(ev.get("error") or last_error or "agent error")
            except Exception as e:  # pragma: no cover
                last_error = f"poll_events failed: {e}"

            if text:
                return text, status, scanned, last_error

            await asyncio.sleep(_POLL_INTERVAL)

        return text or "", status, scanned, last_error or "timeout"

    # ---- ConverseChannel surface ------------------------------------

    async def send(self, req: ConverseRequest) -> ConverseResponse:
        adapter = self._adapter()
        turn = self._turn_from_request(req)
        try:
            await asyncio.to_thread(adapter.submit_turn, turn)
        except Exception as e:
            return ConverseResponse(text="", finished=True, error=f"submit failed: {e}")

        started_at = time.monotonic()
        text, status, scanned, err = await self._poll_until_report(started_at)
        return ConverseResponse(
            text=text or "",
            finished=True,
            error=err,
            metadata={"status": status, "events_scanned": scanned},
        )

    async def stream(self, req: ConverseRequest) -> AsyncIterator[ConverseResponse]:
        adapter = self._adapter()
        turn = self._turn_from_request(req)
        try:
            await asyncio.to_thread(adapter.submit_turn, turn)
        except Exception as e:
            yield ConverseResponse(text="", finished=True, error=f"submit failed: {e}")
            return

        yield ConverseResponse(
            text="",
            finished=False,
            metadata={"phase": "thinking"},
        )

        started_at = time.monotonic()
        text, status, scanned, err = await self._poll_until_report(started_at)
        yield ConverseResponse(
            text=text or "",
            finished=True,
            error=err,
            metadata={"status": status, "events_scanned": scanned},
        )

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


def ensure_default_registered() -> None:
    """Register the default ``KorinaConverseChannel`` if nothing is active yet.

    Idempotent. Phase 5 startup code should call this exactly once.
    """
    global _DEFAULT_REGISTERED
    if _DEFAULT_REGISTERED:
        return
    register_converse_channel(KorinaConverseChannel())
    _DEFAULT_REGISTERED = True


__all__ = ["KorinaConverseChannel", "ensure_default_registered"]
