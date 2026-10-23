"""Built-in agent runtime state and request-local admission generations.

The process-wide singleton is separate from ASR/ACK runtime state. This module
has no gateway or service imports, keeping the shared dependency acyclic.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentState:
    """State for the Korina Agent event bus and pending injections."""

    lock: "threading.RLock" = field(default_factory=threading.RLock)
    events: list = field(default_factory=list)
    event_sizes: list[int] = field(default_factory=list)
    event_bytes: int = 0
    event_seq: int = 0
    generation: int = 0
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

@dataclass
class _BuiltinAdmission:
    generation: int
    cursor: int | None = None


_admission: ContextVar[_BuiltinAdmission | None] = ContextVar('builtin_agent_admission_generation', default=None)


@contextmanager
def bind_builtin_admission(generation: int):
    """Carry an HTTP admission epoch into synchronous built-in dispatch.

    Adapter protocols/signatures remain unchanged. Only the built-in service
    consumes this task/thread-local context; custom state is never reset by
    binding it. The yielded private record also returns a core turn's cursor
    captured atomically during admission. Nested dispatch and exceptions
    restore the previous binding. Worker jobs receive generation explicitly.
    """
    admission = _BuiltinAdmission(generation)
    token = _admission.set(admission)
    try:
        yield admission
    finally:
        _admission.reset(token)


def expected_builtin_generation() -> int | None:
    admission = _admission.get()
    return admission.generation if admission is not None else None


def record_builtin_admission_cursor(cursor: int) -> None:
    """Return the atomic pre-submission cursor to an owned built-in caller."""
    admission = _admission.get()
    if admission is not None:
        admission.cursor = cursor


__all__ = ["AgentState", "agent_state"]
