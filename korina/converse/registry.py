"""Adapter registry for ConverseChannel implementations.

The registry is the consumer-facing surface for swapping channel
implementations. The default channel (korina_channel.KorinaConverseChannel)
is registered at import time; adapters replace it by calling
register_converse_channel() with a new instance.

Selection persisted by Converse config lives in memory only for now;
persistent config (config.json) is a Phase 5 Commit 2 concern.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional

from .protocol import ConverseChannel


_lock = threading.RLock()
_channels: Dict[str, ConverseChannel] = {}
_active: Optional[str] = None


def register_converse_channel(channel: ConverseChannel) -> None:
    """Register or replace a channel under its `name`.

    If the channel is the first registered, it becomes the active channel.
    Replacing the active channel does NOT change which channel is active;
    use set_active_converse_channel() for that.
    """
    if not isinstance(channel, ConverseChannel):
        raise TypeError(f"expected ConverseChannel, got {type(channel).__name__}")
    with _lock:
        _channels[channel.name] = channel
        global _active
        if _active is None:
            _active = channel.name


def unregister_converse_channel(name: str) -> bool:
    """Remove a channel by name. Returns True if removed."""
    with _lock:
        if name not in _channels:
            return False
        del _channels[name]
        if _active == name:
            # Pick any remaining channel, or None.
            globals()["_active"] = next(iter(_channels), None)
        return True


def get_converse_channel(name: Optional[str] = None) -> ConverseChannel:
    """Resolve and return a channel.

    If `name` is None, returns the currently active channel.
    Raises KeyError if the requested channel is not registered.
    """
    with _lock:
        target = name if name else _active
        if target is None or target not in _channels:
            raise KeyError(f"no ConverseChannel registered for {name!r}; active={_active!r}")
        return _channels[target]


def get_active_channel_name() -> Optional[str]:
    """Return the name of the currently active channel (or None)."""
    with _lock:
        return _active


def set_active_converse_channel(name: str) -> ConverseChannel:
    """Switch the active channel. Returns the new active channel.

    Raises KeyError if `name` is not registered.
    """
    with _lock:
        if name not in _channels:
            raise KeyError(f"no ConverseChannel registered for {name!r}")
        globals()["_active"] = name
        return _channels[name]


def list_channels() -> list[str]:
    """Return the names of all registered channels in registration order."""
    with _lock:
        return list(_channels.keys())


def reset_for_testing() -> None:
    """Drop all registrations. Used by tests; do not call from production."""
    with _lock:
        _channels.clear()
        globals()["_active"] = None


__all__ = [
    "register_converse_channel",
    "unregister_converse_channel",
    "get_converse_channel",
    "get_active_channel_name",
    "set_active_converse_channel",
    "list_channels",
    "reset_for_testing",
]
