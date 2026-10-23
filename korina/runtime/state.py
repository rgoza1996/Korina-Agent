"""Shared speech-model and acknowledgement state.

Each subsystem owns its cache, counters, and reentrant locks. Agent events
and worker state live separately in ``korina.agents.state``. Lazy Whisper
iterators retain their owning AsrState so cleanup remains valid across pool
threads and runtime replacement.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from korina.util.paths import ACK_DEFAULT_VOICE


@dataclass
class AsrState:
    """One cached Whisper model, pinned while lazy transcription uses it."""

    models: dict = field(default_factory=dict)
    loaded_at_by_device: dict[str, float] = field(default_factory=dict)
    model_users: int = 0
    lock: "threading.RLock" = field(default_factory=threading.RLock)
    infer_lock: "threading.RLock" = field(default_factory=threading.RLock)
    device: Optional[str] = None
    compute_type: Optional[str] = None


@dataclass
class AckState:
    """State for the ACK phrase generation queue."""

    queue: list[tuple[str, str, str, bool]] = field(default_factory=list)
    in_progress: dict[tuple[str, str], int] = field(default_factory=dict)
    generation: int = 0
    queue_lock: "threading.RLock" = field(default_factory=threading.RLock)
    rebuild_generation: int = 0
    rebuild_lock: "threading.Lock" = field(default_factory=threading.Lock)
    worker_running: bool = False
    last_error: Optional[str] = None
    last_generated: Optional[str] = None
    current_voice: str = ACK_DEFAULT_VOICE


@dataclass
class RuntimeState:
    """Top-level runtime state for Korina Voice Lab."""

    asr: AsrState = field(default_factory=AsrState)
    ack: AckState = field(default_factory=AckState)


# One shared owner for the process; tests may substitute isolated owners.
state = RuntimeState()
