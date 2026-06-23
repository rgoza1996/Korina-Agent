"""Provider preset helpers for Korina Voice Lab.

Pure helpers that resolve a provider name to its canonical local base URL
or check whether a base URL points at a locally-hosted inference server.
No state, no side effects -- safe to import anywhere.
"""

from __future__ import annotations


_PROVIDER_PRESETS = {
    "llama.cpp": "http://127.0.0.1:8080/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "ollama":    "http://127.0.0.1:11434/v1",
}

_LOCAL_BASE_URLS = {
    "http://127.0.0.1:8080/v1",
    "http://127.0.0.1:1234/v1",
    "http://127.0.0.1:11434/v1",
}


def provider_preset_base_url(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    return _PROVIDER_PRESETS.get(provider, "")


def is_local_provider_base_url(base_url: str, provider: str = "") -> bool:
    base = str(base_url or "").strip().rstrip("/")
    provider = str(provider or "").strip().lower()
    if provider in {"llama.cpp", "lmstudio", "ollama"}:
        return True
    return base in _LOCAL_BASE_URLS
