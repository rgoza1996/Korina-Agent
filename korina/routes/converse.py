"""GET/POST /api/converse/* -- adapter selection routes (Phase 5 Commit 4).

These routes expose the consumer-facing surface for swapping which
ConverseChannel implementation serves traffic. They do NOT touch the
chat completion path (routes/chat.py) -- that path is a one-shot
LLM call and lives independently of the Converse/Agent split.

Endpoints:
    GET  /api/converse/channel     -> {"channel": "<name>"}
    GET  /api/converse/channels    -> {"channels": ["<name>", ...]}
    POST /api/converse/channel/{name} -> {"channel": "<name>", "ok": true}

Switching the active channel is process-global. Tests must call
``reset_for_testing()`` + ``reset_default_registered()`` to restore state.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from korina.converse.registry import (
    get_active_channel_name,
    get_converse_channel,
    list_channels,
    set_active_converse_channel,
)


router = APIRouter()


@router.get('/api/converse/channel')
def get_active_channel():
    """Return the name of the currently active ConverseChannel."""
    name = get_active_channel_name()
    if name is None:
        # Surface a payload (status 200) with channel=None rather than a 500;
        # consumers should treat None as "no channel registered" and call
        # /channels to inspect what's available.
        return {"channel": None, "registered": list_channels()}
    return {"channel": name}


@router.get('/api/converse/channels')
def list_registered_channels():
    """Return all registered ConverseChannel names in registration order."""
    return {"channels": list_channels()}


@router.post('/api/converse/channel/{name}')
def switch_active_channel(name: str):
    """Switch the active ConverseChannel. Returns the new channel.

    Raises 404 if ``name`` is not registered -- never silently creates a
    channel; that is the consumer's job via ``register_converse_channel()``.
    """
    try:
        set_active_converse_channel(name)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"ConverseChannel {name!r} is not registered. "
                f"Registered: {list_channels()}"
            ),
        )
    return {"channel": name, "ok": True}


__all__ = ["router"]
