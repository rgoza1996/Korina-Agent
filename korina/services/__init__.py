"""Public service surface for Korina Voice Lab.

Phase 1.4 split the monolith into focused service modules. Each module owns
a single subsystem (whisper, multimodal STT, response LLM, agent event bus,
ACK phrase generation, model catalog, provider manager).

Route helpers, schemas, and the FastAPI app wiring live in
``korina.routes`` / ``korina.app`` / ``korina.schemas``.
"""

from __future__ import annotations

from . import (
    ack_service,
    agent_service,
    model_catalog,
    multimodal_stt,
    provider_manager,
    response_llm,
    whisper_service,
)

__all__ = [
    "ack_service",
    "agent_service",
    "model_catalog",
    "multimodal_stt",
    "provider_manager",
    "response_llm",
    "whisper_service",
]
