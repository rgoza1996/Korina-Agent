"""Provider preset + capability registry for Korina Voice Lab.

Single source of truth for:
  * canonical local base URLs per provider
  * provider capability metadata served at GET /api/capabilities

All callers (preset helpers, the capabilities route, and future provider
lifecycle code) read from PROVIDER_CAPABILITIES. Do not duplicate
provider rules anywhere else in the codebase.

No state, no side effects -- safe to import anywhere.
"""

from __future__ import annotations

from typing import Any


# Registry: {provider_id: capability_dict}.
# Capability keys (snake_case JSON, served verbatim by /api/capabilities):
#   label:                 str   -- user-facing dropdown label
#   default_base_url:      str   -- canonical local URL; "" for non-local
#   editable_base_url:     bool  -- whether the UI lets the user override
#   manageable:            bool  -- backend can start/stop the server
#   model_sources:         list  -- which of:
#                                  endpoint_loaded, local_gguf, catalog
#   is_local:              bool  -- is this a localhost inference server?
#   agent_only:            bool  -- only valid for the agent path
#   response_llm_only:     bool  -- only valid for the response-LLM path
PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "llama.cpp": {
        "label": "llama.cpp local",
        "default_base_url": "http://127.0.0.1:8080/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded", "local_gguf"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": True,
    },
    "lmstudio": {
        "label": "LM Studio local",
        "default_base_url": "http://127.0.0.1:1234/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded", "catalog"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": True,
    },
    "ollama": {
        "label": "Ollama local",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": True,
    },
    "openai-compatible": {
        "label": "OpenAI-compatible / generic",
        "default_base_url": "",
        "editable_base_url": True,
        "manageable": False,
        "model_sources": ["endpoint_loaded"],
        "is_local": False,
        "agent_only": False,
        "response_llm_only": False,
    },
    "anthropic": {
        "label": "Anthropic-compatible",
        "default_base_url": "",
        "editable_base_url": True,
        "manageable": False,
        "model_sources": ["endpoint_loaded"],
        "is_local": False,
        "agent_only": True,
        "response_llm_only": False,
    },
}


def provider_preset_base_url(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    cap = PROVIDER_CAPABILITIES.get(provider, {})
    if cap.get("agent_only"):
        return ""
    return str(cap.get("default_base_url") or "")


def is_local_provider_base_url(base_url: str, provider: str = "") -> bool:
    base = str(base_url or "").strip().rstrip("/")
    provider = str(provider or "").strip().lower()
    cap = PROVIDER_CAPABILITIES.get(provider, {})
    if cap.get("is_local"):
        return True
    for cap in PROVIDER_CAPABILITIES.values():
        if cap.get("is_local") and base == str(cap.get("default_base_url") or "").rstrip("/"):
            return True
    return False


def capabilities_for_section(section: str) -> dict[str, dict[str, Any]]:
    """Return the subset of PROVIDER_CAPABILITIES valid for section."""
    section = str(section or "").strip().lower()
    out: dict[str, dict[str, Any]] = {}
    for pid, cap in PROVIDER_CAPABILITIES.items():
        if section == "agent" and cap.get("response_llm_only"):
            continue
        if section == "response_llm" and cap.get("agent_only"):
            continue
        out[pid] = cap
    return out
