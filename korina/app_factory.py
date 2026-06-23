"""FastAPI application factory for Korina Voice Lab.

Phase 1.9 moves the FastAPI ``app`` instance, CORS middleware, ``/Ack``
static mount, and the startup hook out of ``Korina/korina_voice_lab.py``
(where the monolith owned them via module globals). The package now
owns app construction, and ``korina_voice_lab.py`` is a 3-line shim.

``create_app()`` is the canonical builder. ``korina.app.main()`` calls it
before handing the app to uvicorn.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from korina.config import load_config
from korina.routes import acks as _acks_routes
from korina.routes import capabilities as _capabilities_routes
from korina.routes import agent as _agent_routes
from korina.routes import chat as _chat_routes
from korina.routes import config as _config_routes
from korina.routes import health as _health_routes
from korina.routes import index as _index_routes
from korina.routes import models as _models_routes
from korina.routes import providers as _providers_routes
from korina.routes import stt as _stt_routes
from korina.services.ack_service import enqueue_missing_acks
from korina.util.paths import ACK_DEFAULT_VOICE, ACK_DIR


def _startup_generate_default_acks() -> None:
    """Enqueue any missing ACK WAVs for the configured default voice."""
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    enqueue_missing_acks(str(load_config().get('voice') or ACK_DEFAULT_VOICE))


def create_app() -> FastAPI:
    """Build and return the fully-wired Korina Voice Lab FastAPI app."""
    app = FastAPI(
        title='Korina Voice Lab: Built-in Whisper, multimodal STT, llama.cpp, and Kokoro',
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    app.mount('/Ack', StaticFiles(directory=str(ACK_DIR)), name='ack')

    @app.on_event('startup')
    def _startup() -> None:
        _startup_generate_default_acks()

    for module in (
        _index_routes, _health_routes, _config_routes, _models_routes,
        _providers_routes, _capabilities_routes, _acks_routes, _agent_routes, _stt_routes, _chat_routes,
    ):
        app.include_router(module.router)

    return app