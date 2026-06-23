"""Pure helper utilities for Korina Voice Lab.

Phase 1.5 will move more helpers here. Phase 1.4 pulls ``safe_slug`` out
of the monolith because ``ack_service`` needs it without depending on the
monolith.
"""

from __future__ import annotations

import re


def safe_slug(value: str) -> str:
    """Normalize a free-form string into a filesystem-safe slug.

    Strips characters outside ``[A-Za-z0-9_-]``, lowercases, and falls back
    to ``"ack"`` for empty inputs.
    """
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip()).strip("_").lower()
    return slug or "ack"
