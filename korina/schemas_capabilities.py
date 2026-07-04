"""Pydantic schemas for GET /api/capabilities.

Separated from korina.schemas to keep capability metadata in a
single, import-light module.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


ModelSource = Literal["endpoint_loaded", "local_gguf", "catalog"]


class ProviderCapabilities(BaseModel):
    label: str
    default_base_url: str = ""
    editable_base_url: bool
    manageable: bool
    model_sources: list[ModelSource] = Field(default_factory=list)
    is_local: bool
    agent_only: bool = False
    response_llm_only: bool = False


class CapabilitiesResponse(BaseModel):
    providers: dict[str, ProviderCapabilities]
    agent_providers: dict[str, ProviderCapabilities]
    version: int = 1


# Phase 4.2 model-level capabilities. Mirrors the shape returned by
# korina.services.model_capability.get_model_capability(). The `_inference`
# internal key is excluded from the serialized response.


class ModelCapabilities(BaseModel):
    supports_audio_input: bool
    source: Literal["endpoint_loaded", "local_gguf", "catalog", "inferred"]
    has_mmproj: bool = False
    approx_vram_gb: Optional[float] = None
