"""HTTP / auth helpers for Korina Voice Lab.

Phase 1.5 extracts these out of ``korina.config`` so config code does not
also own transport concerns. The helpers are pure: they read environment
variables and config dicts but never mutate them.
"""

from __future__ import annotations

import os


def auth_headers_from_env(env_name: str) -> dict:
    """Return ``{"Authorization": "Bearer <value>"}`` if ``env_name`` is set,
    otherwise an empty dict."""
    env_name = (env_name or "").strip()
    if not env_name:
        return {}
    value = os.environ.get(env_name, "").strip()
    if not value:
        return {}
    return {"Authorization": f"Bearer {value}"}


def api_key_from_config(config: dict, direct_key: str = "", env_key_name: str = "") -> str:
    """Resolve an API key with priority: explicit ``direct_key`` first, then
    the environment variable named by ``env_key_name``. Returns the empty
    string if neither yields a value."""
    direct = str(direct_key or "").strip()
    if direct:
        return direct
    env_name = str(env_key_name or "").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""
