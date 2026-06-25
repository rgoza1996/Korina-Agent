"""Public service surface for Korina Voice Lab.

Phase 1.4 split the monolith into focused service modules. Keep this package
import-light: importing ``korina.services`` must not import optional runtime
engines such as torch/faster-whisper. Submodules are loaded lazily via
``__getattr__`` so tests and CI can import mocked/lightweight services without
installing model-serving dependencies.
"""

from __future__ import annotations

import importlib

__all__ = [
    "ack_service",
    "agent_service",
    "audio_probe",
    "model_capability",
    "model_catalog",
    "multimodal_stt",
    "provider_manager",
    "response_llm",
    "whisper_service",
]


def __getattr__(name: str):
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
