"""AgentAdapter protocol and supporting types.

Any agent backend that wants to plug into Converse must satisfy this
Protocol. The runtime-checkable decorator lets us assert adapter conformance
without requiring explicit inheritance.

Phase 2: define the contract. Phase 3: route layer talks to it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from korina.schemas import (
    AgentCapabilities,
    AgentEvent,
    PermissionAnswerEvent,
    UserTurn,
)


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

        Fire-and-forget or async; the agent processes the turn and emits
        events that are observable via :meth:`poll_events`.
        """
        ...

    def poll_events(self, cursor: int) -> tuple[int, list[AgentEvent]]:
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