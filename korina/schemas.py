"""Pydantic request/response schemas for the Korina Voice Lab API.

Phase 1.4 splits these out of the monolith so service modules can type-annotate
without depending on the monolith (which itself imports the services).
"""

from __future__ import annotations

import time
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


# Bound accepted payloads before they reach model prompts or background queues.
MAX_TURNS = 64
MAX_TEXT_LENGTH = 8192


def _validate_turn(turn: dict[str, str]) -> dict[str, str]:
    if set(turn) != {"role", "content"}:
        raise ValueError("Each turn must contain only role and content")
    if turn["role"] not in {"user", "assistant", "system", "tool"}:
        raise ValueError("Unsupported conversation role")
    if len(turn["content"]) > MAX_TEXT_LENGTH:
        raise ValueError(f"Turn content exceeds {MAX_TEXT_LENGTH} characters")
    return turn


ConversationTurn = Annotated[dict[str, str], AfterValidator(_validate_turn)]
Conversation = Annotated[list[ConversationTurn], Field(max_length=MAX_TURNS)]
ModelName = Annotated[str, Field(max_length=512)]
Text = Annotated[str, Field(max_length=MAX_TEXT_LENGTH)]
TokenBudget = Annotated[int, Field(ge=1, le=8192)]


class ProviderActivateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: Optional[ModelName] = None


class ChatRequest(BaseModel):
    message: Text
    history: Conversation = Field(default_factory=list)
    system: Text | None = None
    temperature: float = Field(0.7, ge=0, le=2)
    max_tokens: TokenBudget = 180
    model: ModelName | None = None
    reasoning: str | None = Field(None, max_length=64)


class AgentStateRequest(BaseModel):
    transcript: Conversation = Field(default_factory=list)
    previous_report: Text = ""
    model: ModelName | None = None
    max_tokens: TokenBudget = 512


class AgentTranscriptRequest(BaseModel):
    transcript: Conversation = Field(default_factory=list)
    delivery_mode: Literal["prompt", "injection"] = "prompt"
    reason: str = Field("", max_length=512)
    turn_count: int = Field(0, ge=0)


class AgentPermissionAnswer(BaseModel):
    request_id: str = Field("", max_length=128)
    answer: str = Field("", max_length=512)
    transcript: Conversation = Field(default_factory=list)

# Phase 1 (Converse/Agent adapter separation) -- wire protocol between
# Converse and any agent adapter. Existing AgentStateRequest,
# AgentTranscriptRequest, AgentPermissionAnswer remain for backward compat
# with live routes.


class UserTurn(BaseModel):
    """Inbound: Converse -> adapter (one user utterance)."""
    text: Text
    session_id: str = Field(max_length=128)
    turn_count: int = Field(ge=0)
    audio_seconds: float | None = Field(default=None, ge=0)
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
    request_id: str = Field(max_length=128)
    answer: str = Field(max_length=512)
    transcript: Text


class AgentCapabilities(BaseModel):
    """What a given adapter supports."""
    adapter_id: str
    supports_state_reports: bool = True
    supports_injections: bool = True
    supports_permission_flow: bool = True
    supports_streaming: bool = False
    http_compatible: bool = False


class AgentProtocolInfo(BaseModel):
    """Read-only discovery of the selected adapter and its HTTP bridge."""
    ok: Literal[True] = True
    protocol_version: Literal[1] = 1
    adapter_id: str
    adapter_capabilities: AgentCapabilities
    legacy_http_supported: bool
    http_operations: dict[str, bool]
