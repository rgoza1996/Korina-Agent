"""AgentAdapter protocol and supporting types.

Any agent backend that wants to plug into Converse must satisfy this
Protocol. The runtime-checkable decorator lets us assert adapter conformance
without requiring explicit inheritance.

The HTTP bridge and default Converse channel use this contract directly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from korina.schemas import (
    AgentCapabilities,
    AgentPermissionAnswer,
    AgentTranscriptRequest,
    PermissionAnswerEvent,
    UserTurn,
)

# Built-in report transport allowance; the channel adds completion grace.
AGENT_REPORT_TIMEOUT_SECONDS = 90.0


class AgentAdmissionRejected(RuntimeError):
    """The adapter guarantees this request admitted no background work.

    Callers may retry without resetting agent memory. Other submission
    exceptions remain uncertain and may require explicit recovery.
    """


@runtime_checkable
class AgentAdapter(Protocol):
    """Stable contract: Converse talks to any agent through this interface."""

    @property
    def id(self) -> str:
        """Adapter identifier (e.g. ``"korina"``, ``"hermes"``)."""
        ...

    @property
    def capabilities(self) -> AgentCapabilities:
        """What this adapter supports."""
        ...

    def submit_turn(self, turn: UserTurn) -> None:
        """Submit a user utterance to the agent.

        The synchronous submission returns immediately; background work emits
        events that are observable via :meth:`poll_events`.
        AgentAdmissionRejected explicitly guarantees no work was admitted;
        callers treat other submission failures as uncertain.
        """
        ...

    def poll_events(self, cursor: int) -> tuple[int, list[dict]]:
        """Return ``(new_cursor, events)`` since ``cursor``.

        ``new_cursor`` is the high-water mark the caller should pass on the
        next poll. ``events`` is the list of agent events emitted since then.
        """
        ...

    def answer_permission(self, answer: PermissionAnswerEvent) -> None:
        """Forward a user's permission-answer to the agent."""
        ...

    def reset(self) -> None:
        """Clear agent state (events, busy flag, etc.)."""
        ...

    def health_check(self) -> dict:
        """Lightweight liveness probe; returns ``{"ok": bool, ...}``."""
        ...

@runtime_checkable
class LegacyAgentHttpAdapter(Protocol):
    """Optional compatibility extension for the original HTTP wire contract.

    The core AgentAdapter remains sufficient for new backends. Adapters that
    need full transcript/injection semantics can additionally implement this
    extension; the built-in Korina adapter already does.
    """

    def snapshot(self) -> dict[str, Any]:
        ...

    def submit_transcript_legacy(self, req: AgentTranscriptRequest) -> dict[str, Any]:
        ...

    def answer_permission_legacy(self, req: AgentPermissionAnswer) -> dict[str, Any]:
        ...
