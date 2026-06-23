"""Canonical application entry point for Korina Voice Lab.

Phase 1.8 introduces this module so the uvicorn launcher lives in one
canonical place. Step 1.9 will move the FastAPI ``app`` instance here too,
at which point ``Korina/korina_voice_lab.py`` becomes a 3-line shim and
the ``app is None`` fallback in :func:`main` is removed.

Both invocations work today::

    python3 Korina/korina_voice_lab.py   # systemd uses this path
    python3 -m korina.app                # canonical package entry
"""

from __future__ import annotations

from typing import Optional

import uvicorn
from fastapi import FastAPI


def main(app: Optional[FastAPI] = None, host: str = "0.0.0.0", port: int = 8001) -> None:
    """Run uvicorn against the given FastAPI app.

    If ``app`` is not supplied, fall back to the legacy monolith's app so
    ``python3 -m korina.app`` works as a drop-in replacement during
    Phase 1.8. Step 1.9 removes that fallback once the app lives in this
    package directly.
    """
    if app is None:
        # Backward-compat path: launch the monolith's app directly.
        from Korina.korina_voice_lab import app as legacy_app
        app = legacy_app
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
