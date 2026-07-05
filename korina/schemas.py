"""Pydantic request/response schemas for the Korina Voice Lab API.

Phase 1.4 splits these out of the monolith so service modules can type-annotate
without depending on the monolith (which itself imports the services).
"""

from __future__ import annotations

import time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProviderActivateRequest(BaseModel):
    provider: str
    model: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    history: list = []
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int = 180
    model: str | None = None
    reasoning: str | None = None


class AgentStateRequest(BaseModel):
    transcript: list = []
    previous_report: str = ""
    model: str | None = None
    max_tokens: int = 512


class AgentTranscriptRequest(BaseModel):
    transcript: list = []
    delivery_mode: str = "prompt"
    reason: str = ""
    turn_count: int = 0


class AgentPermissionAnswer(BaseModel):
    request_id: str = ""
    answer: str = ""
    transcript: list = []

# Phase 1 (Converse/Agent adapter separation) -- wire protocol between
# Converse and any agent adapter. Existing AgentStateRequest,
# AgentTranscriptRequest, AgentPermissionAnswer remain for backward compat
# with live routes.


class UserTurn(BaseModel):
    """Inbound: Converse -> adapter (one user utterance)."""
    text: str
    session_id: str
    turn_count: int
    audio_seconds: float | None = None
    timestamp: float = Field(default_factory=time.time)


class AgentEvent(BaseModel):
    """Outbound: adapter -> Converse (state report, injection, permission request)."""
    id: int = 0  # producer assigns after construction via state.agent.event_seq
    type: str  # "state_report" | "injection" | "permission_request" | ...
    priority: str = "normal"
    payload: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")  # tolerate extra fields from existing event dicts


class PermissionAnswerEvent(BaseModel):
    """Converse -> adapter: user answered a permission request."""
    request_id: str
    answer: Literal["yes", "no"]
    transcript: str


class AgentCapabilities(BaseModel):
    """What a given adapter supports."""
    adapter_id: str
    supports_state_reports: bool = True
    supports_injections: bool = True
    supports_permission_flow: bool = True
    supports_streaming: bool = False
    http_compatible: bool = False

