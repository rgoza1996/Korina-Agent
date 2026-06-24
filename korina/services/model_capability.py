"""Per-model capability detection.

Returns capability metadata for any model id the user can pick in the
UI. The shape matches `ModelCapabilities` in korina/schemas_capabilities.py.

Strategy:
  1. If the model id is in MODEL_CAPABILITIES (substring match),
     return the explicit entry.
  2. Otherwise, compute a heuristic based on:
     a. find_mmproj_for_model(model_id) -- for local GGUFs
     b. name substring matches: vision/audio/ultravox/vl/qwen2-audio
     c. embedding/TTS blacklist: nomic-embed-*, orpheus-*
  3. If still unknown, default to supports_audio_input=False.

Heuristic is a UX filter, not a truth claim. Users can override via
the `?All models` dropdown toggle in the multimodal STT UI.
"""

from __future__ import annotations

import os
from typing import Any

from korina.util.presets import (
    MODEL_CAPABILITIES,
    _lookup_registry_capability,
)


# Name-substring patterns that strongly suggest audio input support.
# Substring (not regex) matching; lowercased.
_MULTIMODAL_HINTS = ("vision", "audio", "ultravox", "-vl-", "qwen2-audio")

# Name-substring patterns that explicitly mark a model as non-multimodal
# even if it has an mmproj (e.g. embeddings always have one for completeness,
# TTS models don't consume audio input).
_NON_MULTIMODAL_HINTS = ("nomic-embed", "orpheus-", "embed-text", "qwen3-vl")

# Hardcoded allowlist per blueprint §4.1.
_HARD_ALLOWLIST = ("gemma-4-e2b", "gpt-4o-audio", "ultravox", "qwen2-audio")


def _has_mmproj(model_id: str) -> bool:
    """True iff find_mmproj_for_model() finds a matching mmproj file.

    Imported lazily to avoid a top-level circular import (model_catalog
    imports util.paths which is fine, but keeping the import local makes
    the dependency direction obvious).
    """
    try:
        from korina.services.model_catalog import find_mmproj_for_model
        return bool(find_mmproj_for_model(model_id))
    except Exception:
        return False


def _heuristic_capability(model_id: str) -> dict[str, Any]:
    """Compute capability from name and filesystem signals.

    The result is a best-effort filter, not a truth claim. Defaults to
    `supports_audio_input=False` when no positive signal is present.
    """
    if not model_id:
        return _default_capability(model_id, reason="empty")

    needle = model_id.lower()
    name = os.path.basename(needle)

    # Explicit blacklist: embeddings and TTS models are never multimodal.
    for hint in _NON_MULTIMODAL_HINTS:
        if hint in needle:
            return {
                "supports_audio_input": False,
                "source": "local_gguf",
                "has_mmproj": _has_mmproj(model_id),
                "approx_vram_gb": None,
                "_inference": "blacklist",
            }

    # Positive signal 1: model has a real mmproj file on disk.
    if model_id.endswith(".gguf") and _has_mmproj(model_id):
        return {
            "supports_audio_input": True,
            "source": "local_gguf",
            "has_mmproj": True,
            "approx_vram_gb": None,
            "_inference": "mmproj_present",
        }

    # Positive signal 2: allowlist hit.
    for allowed in _HARD_ALLOWLIST:
        if allowed in needle:
            return {
                "supports_audio_input": True,
                "source": "local_gguf" if model_id.endswith(".gguf") else "endpoint_loaded",
                "has_mmproj": False,
                "approx_vram_gb": None,
                "_inference": f"allowlist:{allowed}",
            }

    # Positive signal 3: name heuristic.
    for hint in _MULTIMODAL_HINTS:
        if hint in needle or hint in name:
            return {
                "supports_audio_input": True,
                "source": "local_gguf" if model_id.endswith(".gguf") else "endpoint_loaded",
                "has_mmproj": False,
                "approx_vram_gb": None,
                "_inference": f"name_hint:{hint}",
            }

    return _default_capability(model_id, reason="no_signal")


def _default_capability(model_id: str, reason: str) -> dict[str, Any]:
    return {
        "supports_audio_input": False,
        "source": "inferred",
        "has_mmproj": False,
        "approx_vram_gb": None,
        "_inference": reason,
    }


def get_model_capability(
    model_id: str,
    *,
    allowlist: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Return capability metadata for a single model id.

    Lookup order:
      1. Runtime allowlist (from config.multimodal_stt_model_allowlist)
         — exact id match (case-sensitive).
      2. MODEL_CAPABILITIES registry (substring match, lowercased).
      3. Heuristic.

    The `_inference` key is internal — schemas_capabilities strips it
    before serialization. It exists so debug logs can show why the
    heuristic made a particular decision.
    """
    if allowlist and model_id in allowlist:
        return {
            "supports_audio_input": True,
            "source": "endpoint_loaded",
            "has_mmproj": False,
            "approx_vram_gb": None,
            "_inference": "runtime_allowlist",
        }

    explicit = _lookup_registry_capability(model_id)
    if explicit is not None:
        explicit["_inference"] = "registry"
        return explicit

    return _heuristic_capability(model_id)
