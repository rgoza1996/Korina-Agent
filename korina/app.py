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
import os

from korina.app_factory import create_app


def main(host: str | None = None, port: int | None = None) -> None:
    """Build the Korina app via :func:`create_app` and run it under uvicorn."""
    resolved_host = host if host is not None else os.environ.get('KORINA_HOST', '127.0.0.1')
    resolved_port = port if port is not None else int(os.environ.get('KORINA_PORT', '8001'))
    if not 1 <= resolved_port <= 65535:
        raise ValueError('KORINA_PORT must be between 1 and 65535')
    app = create_app()
    uvicorn.run(app, host=resolved_host, port=resolved_port)


if __name__ == "__main__":
    main()
