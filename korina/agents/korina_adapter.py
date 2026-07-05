"""KorinaAgentAdapter -- wraps the existing in-process agent_service.

Phase 2: this is a thin adapter that delegates to the existing ``agent_service``
module. The wrapper exists so that:

1. Routes can call ``agent_gateway()`` instead of touching ``state.agent``
   directly (Phase 3 does the migration).
2. ``AgentEvent`` is the canonical event type at the adapter boundary, even
   though the underlying service still emits flat dicts into
   ``state.agent.events`` (we coerce on the way out via ``extra="allow"``).
3. Tests can swap in a fake adapter via :func:`korina.agents.register_adapter`.

Phase 4: ``AgentState`` is migrated from ``korina/runtime/state.py`` into this
adapter's internal state, removing the shared singleton.

Lazy imports: ``agent_service`` and ``runtime.state`` are imported inside
method bodies, not at module top. This keeps the adapter from being pulled
in by anything that imports ``agent_service`` or ``runtime.state`` --
which would create a cycle during Phase 3 when ``routes/agent.py`` starts
importing the gateway.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from korina.agents.base import AgentAdapter
from korina.schemas import (
    AgentCapabilities,
    AgentEvent,
    PermissionAnswerEvent,
    UserTurn,
)

logger = logging.getLogger(__name__)


class KorinaAgentAdapter:
    """In-process adapter for the existing Korina Agent implementation."""

    id = "korina"
    capabilities = AgentCapabilities(
        adapter_id="korina",
        supports_state_reports=True,
        supports_injections=True,
        supports_permission_flow=True,
        supports_streaming=False,
        http_compatible=False,
    )

    # ---- inbound: Converse -> adapter ----------------------------------------

    def submit_turn(self, turn: UserTurn) -> None:
        """Submit a user utterance. Delegates to agent_service.

        Wraps the new ``UserTurn`` type into the existing
        ``AgentTranscriptRequest`` shape so the live service keeps working.
        Future phases can rewrite this to call ``agent_service`` directly with
        the new type once the routes migrate.
        """
        from korina.schemas import AgentTranscriptRequest
        from korina.services import agent_service

        req = AgentTranscriptRequest(
            transcript=[turn.text],
            delivery_mode="prompt",
            reason=f"user_turn:{turn.session_id}",
            turn_count=turn.turn_count,
        )
        agent_service.submit_agent_transcript(req)

    # ---- outbound: adapter -> Converse ---------------------------------------

    def poll_events(self, cursor: int) -> tuple[int, list[AgentEvent]]:
        """Return events with id > cursor.

        The underlying service still pushes flat dicts into
        ``state.agent.events``; we coerce each dict into ``AgentEvent`` here.
        ``AgentEvent.id`` defaults to 0 so old dicts that already carry their
        own ``id`` field pass validation, and ``extra="allow"`` lets unknown
        fields through (matches Phase 1 contract).
        """
        from korina.runtime import state as runtime_state

        agent = runtime_state.agent
        with agent.lock:
            events: list[dict[str, Any]] = [
                e for e in agent.events if int(e.get("id", 0)) > cursor
            ]
            new_cursor = agent.event_seq
        out: list[AgentEvent] = []
        for ev in events:
            try:
                out.append(AgentEvent(**ev))
            except Exception as exc:
                # Defensive: log and skip. The legacy frontend reads raw dicts,
                # so dropping one malformed event is preferable to crashing
                # the poll loop. Phase 4 will fix this at the source.
                logger.warning("korina_adapter: skipping malformed event: %s", exc)
                continue
        return new_cursor, out

    def answer_permission(self, answer: PermissionAnswerEvent) -> None:
        """Forward a permission-answer to the underlying service.

        Mirrors the legacy behaviour from ``routes/agent.py``:
        push the answer event, then submit a transcript so the agent picks
        up the decision on its next turn.
        """
        from korina.services import agent_service
        from korina.schemas import AgentTranscriptRequest

        agent_service.push_agent_event({
            "type": "permission_answer",
            "priority": "normal",
            "request_id": answer.request_id,
            "answer": answer.answer,
            "transcript": answer.transcript,
        })
        agent_service.submit_agent_transcript(
            AgentTranscriptRequest(
                transcript=[answer.transcript],
                delivery_mode="prompt",
                reason=f"permission_answer:{answer.answer}",
                turn_count=0,
            )
        )

    def reset(self) -> None:
        """Clear agent state. Matches legacy /api/agent/reset semantics."""
        from korina.runtime import state as runtime_state

        agent = runtime_state.agent
        with agent.lock:
            agent.events.clear()
            agent.event_seq = 0
            agent.pending_injections.clear()
            agent.busy = False
            agent.status = "idle"
            agent.last_report = ""
            agent.last_error = None
            agent.last_emitted_report_hash = ""
            agent.last_emitted_report_at = 0.0

    # ---- diagnostics ---------------------------------------------------------

    def health_check(self) -> dict:
        """Lightweight probe; returns ``status`` and event count."""
        from korina.runtime import state as runtime_state

        agent = runtime_state.agent
        with agent.lock:
            return {
                "ok": True,
                "adapter": self.id,
                "status": agent.status,
                "busy": agent.busy,
                "event_seq": agent.event_seq,
                "pending_injections": len(agent.pending_injections),
                "timestamp": time.time(),
            }

    def snapshot(self) -> dict:
        """Return a snapshot suitable for ``/api/agent/status``.

        Delegates to ``agent_service.agent_snapshot()`` to keep one source of
        truth for the response shape; Phase 3 will wire this into the route
        without changing the response.
        """
        from korina.services import agent_service
        return agent_service.agent_snapshot()