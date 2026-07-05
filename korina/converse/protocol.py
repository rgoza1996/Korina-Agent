"""Converse channel protocol and shared types.

A ConverseChannel is the contract between Korina Converse (the chat
channel that ships with Korina Voice Lab) and any agent implementation
(Korina, Hermes Agent, OpenClaw, ...). It replaces the implicit HTTP
boundary with an explicit async interface so adapters can be swapped
without touching Converse code.

This module has zero coupling to korina.services.agent_service — the
default KorinaConverseChannel (in korina/converse/korina_channel.py)
is the only place that knows about agent_service. New adapters register
themselves via korina.converse.registry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, List


@dataclass
class ConverseRequest:
    """A single user turn being sent to the channel.

    Attributes:
        transcript: ordered list of {role, text} turns from the user.
            The Converse layer flattens voice session events into this list.
        delivery_mode: "prompt" (treat as a new turn) or "inject" (prepend
            without resetting the assistant's state). Default "prompt".
        reason: short human-readable reason for the request (used by the
            KorinaAdapter to log hidden-mode injections). Default "".
        metadata: free-form adapter-defined metadata (e.g. session_id,
            feature_flag overrides).
    """
    transcript: List[dict]
    delivery_mode: str = "prompt"
    reason: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ConverseResponse:
    """An adapter response event from the channel.

    Adapters may return either a single ConverseResponse (send) or a
    stream of them (stream). Both shapes share this dataclass so consumer
    code is uniform.
    """
    text: str = ""
    finished: bool = True
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class ConverseChannel(ABC):
    """Channel abstraction between Korina Converse and any agent adapter.

    Implementations MUST be thread-safe: Converse routes call into the
    channel from FastAPI's async task pool and from the STT worker thread
    concurrently.
    """

    name: str = "abstract"

    @abstractmethod
    async def send(self, req: ConverseRequest) -> ConverseResponse:
        """Send a single turn and block until the adapter is done."""
        ...

    @abstractmethod
    async def stream(self, req: ConverseRequest) -> AsyncIterator[ConverseResponse]:
        """Stream partial responses as the adapter produces them.

        Implementations must yield at least one ConverseResponse with
        finished=True; they may yield additional ConverseResponse items
        with finished=False for streaming UX.
        """
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel any in-flight request this channel is processing."""
        ...

    async def health(self) -> dict:
        """Optional adapter-defined health check. Default returns {"ok": True}."""
        return {"ok": True, "channel": self.name}


__all__ = [
    "ConverseChannel",
    "ConverseRequest",
    "ConverseResponse",
]
