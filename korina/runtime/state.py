"""Runtime state container for Korina Voice Lab.

Phase 1.3 gathers the module-level runtime mutables that previously lived at
the top of ``Korina/korina_voice_lab.py`` into a single ``RuntimeState``
dataclass. Each subsystem (``asr``, ``ack``, ``agent``) owns its own
dataclass so the call sites read as ``state.asr.models`` /
``state.ack.queue`` / ``state.agent.events``.

The dataclass fields are the *exact* set of variables that previously lived
as module globals. No behavior changes; this is a pure refactor.

Locks are upgraded from ``threading.Lock`` to ``threading.RLock`` so nested
acquisitions (e.g. an agent worker that already holds ``state.agent.lock``
calling another helper that re-acquires it) do not deadlock. The blueprint
explicitly called this out.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from korina.util.paths import ACK_DEFAULT_VOICE


@dataclass
class AsrState:
    """State for the Whisper/faster-whisper ASR subsystem."""

    models: dict = field(default_factory=dict)
    loaded_at_by_device: dict[str, float] = field(default_factory=dict)
    lock: "threading.RLock" = field(default_factory=threading.RLock)
    infer_lock: "threading.RLock" = field(default_factory=threading.RLock)
    device: Optional[str] = None
    compute_type: Optional[str] = None


@dataclass
class AckState:
    """State for the ACK phrase generation queue."""

    queue: list = field(default_factory=list)
    in_progress: set = field(default_factory=set)
    queue_lock: "threading.RLock" = field(default_factory=threading.RLock)
    worker_running: bool = False
    last_error: Optional[str] = None
    last_generated: Optional[str] = None
    current_voice: str = ACK_DEFAULT_VOICE


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


@dataclass
class RuntimeState:
    """Top-level runtime state for Korina Voice Lab."""

    asr: AsrState = field(default_factory=AsrState)
    ack: AckState = field(default_factory=AckState)
    agent: AgentState = field(default_factory=AgentState)


# Module-level singleton. Imported as `state` by the monolith and any
# future korina.* modules. Do not instantiate more than once per process.
state = RuntimeState()
