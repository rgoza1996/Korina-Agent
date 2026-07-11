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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from korina.config import load_config
from korina.routes import acks as _acks_routes
from korina.routes import capabilities as _capabilities_routes
from korina.routes import agent as _agent_routes
from korina.routes import converse as _converse_routes
from korina.routes import chat as _chat_routes
from korina.routes import config as _config_routes
from korina.routes import health as _health_routes
from korina.routes import index as _index_routes
from korina.routes import model_roots as _model_roots_routes
from korina.routes import models as _models_routes
from korina.routes import providers as _providers_routes
from korina.routes import stt as _stt_routes
from korina.services.ack_service import enqueue_missing_acks
from korina.util.paths import ACK_DEFAULT_VOICE, ACK_DIR, APP_DIR


def _startup_generate_default_acks() -> None:
    """Enqueue any missing ACK WAVs for the configured default voice."""
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    enqueue_missing_acks(str(load_config().get('voice') or ACK_DEFAULT_VOICE))


def create_app() -> FastAPI:
    """Build and return the fully-wired Korina Voice Lab FastAPI app."""
    app = FastAPI(
        title='Korina Voice Lab: Built-in Whisper, multimodal STT, llama.cpp, and Kokoro',
    )

    cfg = load_config()
    _converse_block = cfg.get("converse") if isinstance(cfg.get("converse"), dict) else None
    allowed_origins = (
        _converse_block.get("allowed_origins") if _converse_block else None
    ) or ['http://127.0.0.1:8001', 'http://localhost:8001']
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        # Spec-compliant: wildcard+credentials is invalid. No frontend
        # code sends cookies (see commit: docs(cors): credentialed-fetch
        # audit). Credentials flow through env-var API keys at the
        # backend boundary, not browser cookie jars.
        allow_credentials=False,
        allow_methods=['*'],
        allow_headers=['*'],
    )
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    app.mount('/Ack', StaticFiles(directory=str(ACK_DIR)), name='ack')
    # Phase 3+ frontend: serve the ES modules under /js and the external
    # stylesheet. APP_DIR points at the runtime (/home/roggoz/Korina/) which
    # has js/ and styles.css alongside index.html.
    js_dir = APP_DIR / 'js'
    if js_dir.is_dir():
        app.mount('/js', StaticFiles(directory=str(js_dir)), name='js')

    @app.get('/styles.css')
    def _styles_css():
        path = APP_DIR / 'styles.css'
        if not path.exists():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail='styles.css missing')
        return FileResponse(path)

    @app.on_event('startup')
    def _startup() -> None:
        _startup_generate_default_acks()
        # Phase 5 (Converse/Agent boundary): ensure the default
        # ConverseChannel is registered before routes serve traffic.
        from korina.converse import ensure_default_registered
        ensure_default_registered()

    for module in (
        _index_routes, _health_routes, _config_routes, _models_routes,
        _model_roots_routes, _providers_routes, _capabilities_routes,
        _acks_routes, _agent_routes, _stt_routes, _chat_routes,
        _converse_routes,
    ):
        app.include_router(module.router)

    return app