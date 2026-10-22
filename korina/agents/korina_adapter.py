"""Built-in adapter for the in-process background report service.

Core turn/permission methods and the optional legacy HTTP extension share the
same service admission and agents.state singleton. Events retain their flat
wire dictionaries. Lazy service imports keep the gateway dependency acyclic.
"""

from __future__ import annotations

import time
from typing import Any

from korina.schemas import (
    AgentCapabilities,
    PermissionAnswerEvent,
    UserTurn,
)



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
        The bounded request retains the session label and turn count.
        """
        from korina.schemas import AgentTranscriptRequest
        from korina.services import agent_service

        req = AgentTranscriptRequest(
            transcript=[{"role": "user", "content": turn.text}],
            delivery_mode="prompt",
            reason=f"user_turn:{turn.session_id}",
            turn_count=turn.turn_count,
        )
        result = agent_service.submit_agent_transcript(req)
        if isinstance(result, dict) and result.get("disabled"):
            raise RuntimeError("Korina Agent is disabled in settings")

    # ---- outbound: adapter -> Converse ---------------------------------------

    def poll_events(self, cursor: int) -> tuple[int, list[dict[str, Any]]]:
        """Return events with id > cursor as raw dicts.

        Callers see the same flat-dict events the frontend renders.
        Wrapping in ``AgentEvent`` would drop or rename unknown fields that
        the frontend relies on (``created_at``, ``message``, ``status``, etc.).
        """
        from korina.agents.state import agent_state

        agent = agent_state
        with agent.lock:
            events: list[dict[str, Any]] = [
                e for e in agent.events if int(e.get("id", 0)) > cursor
            ]
            new_cursor = agent.event_seq
        return new_cursor, events

    def answer_permission(self, answer: PermissionAnswerEvent) -> None:
        """Forward a permission-answer to the underlying service.

        Mirrors the legacy behaviour from ``routes/agent.py``:
        push the answer event, then submit a transcript so the agent picks
        up the decision on its next turn.
        """
        from korina.services import agent_service
        from korina.schemas import AgentTranscriptRequest

        result = agent_service.submit_agent_permission(AgentTranscriptRequest(
            transcript=[{"role": "user", "content": answer.transcript}],
            delivery_mode="prompt",
            reason=f"permission_answer:{answer.answer}",
            turn_count=0,
        ), {
            "type": "permission_answer",
            "priority": "normal",
            "request_id": answer.request_id,
            "answer": answer.answer,
            "transcript": answer.transcript,
        })
        if result.get('disabled'):
            raise RuntimeError('Korina Agent is disabled in settings')
        if result.get('stale'):
            raise RuntimeError('Korina Agent session changed before permission admission')

    def reset(self) -> None:
        """Clear agent state. Matches legacy /api/agent/reset semantics."""
        from korina.services.agent_service import reset_agent_state
        reset_agent_state()

    # ---- diagnostics ---------------------------------------------------------

    def health_check(self) -> dict:
        """Lightweight probe; returns ``status`` and event count."""
        from korina.agents.state import agent_state

        agent = agent_state
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

        Delegates to ``agent_service.agent_snapshot()`` so status and HTTP
        responses share one shape.
        """
        from korina.services import agent_service
        return agent_service.agent_snapshot()


    # ---- legacy compatibility passthroughs ----------------------------------

    def submit_transcript_legacy(self, req) -> dict:
        """Legacy compat: preserve /api/agent/transcript behaviour exactly.

        The legacy endpoint accepts ``AgentTranscriptRequest`` with arbitrary
        ``delivery_mode`` (e.g. ``"injection"``), ``reason``, ``turn_count``.
        Wrapping through ``UserTurn`` would drop those fields. This method
        passes the request straight to ``agent_service.submit_agent_transcript``
        so existing frontend and automation callers keep working unchanged.
        """
        from korina.services import agent_service
        return agent_service.submit_agent_transcript(req)

    def answer_permission_legacy(self, req) -> dict:
        """Legacy compat: preserve /api/agent/permission-answer behaviour.

        Mirrors the original route handler:
            1. push_agent_event with full legacy dict shape
            2. submit_agent_transcript to feed the decision back to the agent
        Returns the service response dict (which contains the legacy shape
        ``{"ok": True, "accepted": ..., "queued_as": ..., "status": ...}``).
        """
        from korina.services import agent_service
        from korina.schemas import AgentTranscriptRequest

        return agent_service.submit_agent_permission(AgentTranscriptRequest(
            transcript=req.transcript,
            delivery_mode="prompt",
            reason=f"permission_answer:{req.answer or 'no'}",
            turn_count=0,
        ), {
            "type": "permission_answer",
            "priority": "normal",
            "request_id": req.request_id or "",
            "answer": req.answer or "no",
            "transcript": req.transcript,
        })
