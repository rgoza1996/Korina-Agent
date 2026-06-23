"""Pydantic request/response schemas for the Korina Voice Lab API.

Phase 1.4 splits these out of the monolith so service modules can type-annotate
without depending on the monolith (which itself imports the services).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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
