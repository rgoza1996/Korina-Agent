"""Korina Converse public surface.

This subpackage is the consumer-facing API for the Converse channel.
Third-party agent adapters (Hermes, OpenClaw, ...) implement
``ConverseChannel`` and call ``register_converse_channel`` to plug in.

Public exports:
    ConverseChannel      - protocol to implement
    ConverseRequest      - input dataclass
    ConverseResponse     - output dataclass
    register_converse_channel,
    unregister_converse_channel,
    get_converse_channel,
    set_active_converse_channel,
    get_active_channel_name,
    list_channels
    KorinaConverseChannel        - default in-process adapter
    ensure_default_registered   - idempotent startup hook
"""
from __future__ import annotations

from .protocol import ConverseChannel, ConverseRequest, ConverseResponse
from .registry import (
    get_active_channel_name,
    get_converse_channel,
    list_channels,
    register_converse_channel,
    set_active_converse_channel,
    unregister_converse_channel,
    reset_for_testing,
)
from .korina_channel import KorinaConverseChannel, ensure_default_registered


__all__ = [
    "ConverseChannel",
    "ConverseRequest",
    "ConverseResponse",
    "register_converse_channel",
    "unregister_converse_channel",
    "get_converse_channel",
    "set_active_converse_channel",
    "get_active_channel_name",
    "list_channels",
    "reset_for_testing",
    "KorinaConverseChannel",
    "ensure_default_registered",
]
