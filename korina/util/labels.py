"""Pure helper utilities for Korina Voice Lab.

Slugs, model display labels, and other small string-shaping helpers that
are pure functions with no runtime side effects.
"""

from __future__ import annotations

import re
from pathlib import Path


def safe_slug(value: str) -> str:
    """Normalize a free-form string into a filesystem-safe slug.

    Strips characters outside ``[A-Za-z0-9_-]``, lowercases, and falls back
    to ``"ack"`` for empty inputs.
    """
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip()).strip("_").lower()
    return slug or "ack"


def display_model_label(model_id: str) -> str:
    """Turn a raw model id (path-like for local GGUF, identifier for HF/API)
    into a short human-readable label.

    For paths, returns ``"<file> — <parent>"`` so the dropdown can show both
    the GGUF filename and its parent directory.
    """
    text = str(model_id or "").strip()
    if not text:
        return ""
    if "/" in text or text.endswith(".gguf"):
        p = Path(text)
        return f"{p.name} — {p.parent.name}" if p.parent.name else p.name
    return text
