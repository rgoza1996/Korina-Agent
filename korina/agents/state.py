"""Agent runtime state (Phase 4 migration).

Phase 1.3 introduced a ``RuntimeState`` dataclass that owned ``AsrState``,
``AckState``, and ``AgentState`` as flat siblings. Phase 4 (Converse/Agent
adapter separation) moves ``AgentState`` out of Converse runtime state and
into this module. The remaining ``AsrState`` / ``AckState`` stay in
``korina/runtime/state.py`` where the chat / STT / ack code expects them.

This module is intentionally tiny -- it only exposes the dataclass and a
process-wide singleton. It deliberately does NOT import from
``korina.agents.gateway`` or ``korina.agents.korina_adapter`` to avoid the
circular import that motivated the split in the first place.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentState:
    """State for the Korina Agent event bus and pending injections."""

    lock: "threading.RLock" = field(default_factory=threading.RLock)
    events: list = field(default_factory=list)
    event_seq: int = 0
    busy: bool = False
    status: str = "idle"
    last_report: str = ""
    pending_injections: list = field(default_factory=list)
    last_error: Optional[str] = None
    last_emitted_report_hash: str = ""
    last_emitted_report_at: float = 0.0


# Process-wide singleton. Replaces ``runtime_state.agent`` for all
# agent_service and adapter code paths.
agent_state = AgentState()


__all__ = ["AgentState", "agent_state"]
