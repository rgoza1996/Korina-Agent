"""Canonical application entry point for Korina Voice Lab.

Phase 1.9: ``main()`` now builds the FastAPI app via
:func:`korina.app_factory.create_app` and hands it to uvicorn. The
no-arg fallback to ``Korina.korina_voice_lab.app`` is gone — that
module is now a 3-line shim with no app of its own.

Both invocations work today::

    python3 Korina/korina_voice_lab.py   # systemd uses this path
    python3 -m korina.app                # canonical package entry
"""

from __future__ import annotations

import uvicorn

from korina.app_factory import create_app


def main(host: str = "0.0.0.0", port: int = 8001) -> None:
    """Build the Korina app via :func:`create_app` and run it under uvicorn."""
    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()