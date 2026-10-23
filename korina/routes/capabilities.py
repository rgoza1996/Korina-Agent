"""GET /api/capabilities -- provider capability registry."""

from __future__ import annotations

from fastapi import APIRouter

from korina.schemas_capabilities import CapabilitiesResponse, ProviderCapabilities
from korina.util.presets import capabilities_for_section


router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    """Return provider capabilities for both response-LLM and agent paths."""
    return CapabilitiesResponse(
        providers={name: ProviderCapabilities.model_validate(value)
                   for name, value in capabilities_for_section("response_llm").items()},
        agent_providers={name: ProviderCapabilities.model_validate(value)
                         for name, value in capabilities_for_section("agent").items()},
    )
