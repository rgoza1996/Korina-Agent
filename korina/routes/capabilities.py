"""GET /api/capabilities -- provider capability registry."""

from __future__ import annotations

from fastapi import APIRouter

from korina.schemas_capabilities import CapabilitiesResponse
from korina.util.presets import capabilities_for_section


router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    """Return provider capabilities for both response-LLM and agent paths."""
    return CapabilitiesResponse(
        providers=capabilities_for_section("response_llm"),
        agent_providers=capabilities_for_section("agent"),
    )
