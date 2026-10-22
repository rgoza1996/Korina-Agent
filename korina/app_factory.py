"""Compose the HTTP API, static UI, request policy, and runtime lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
from korina.services.ack_service import cancel_ack_generation, enqueue_missing_acks
from korina.services.ack_manifest import AckManifestError
from korina.runtime.limits import RequestSizeLimit
from korina.routes.validation import sanitized_validation_error
from korina.util.paths import ACK_DEFAULT_VOICE, ACK_DIR, APP_DIR

# NOTE: `load_config` and `save_config` are deliberately NOT imported at
# module top. Both `_startup_generate_default_acks` and the WS1.2 saved-
# channel restore path look them up via `korina.config.load_config()` at
# call time. This lets tests monkeypatch `korina.config.load_config`
# without freezing the value at import time.


def _startup_generate_default_acks() -> None:
    """Enqueue any missing ACK WAVs for the configured default voice."""
    import korina.config as _cfg
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    config = _cfg.load_config()
    if config.get('ack_enabled') == 'on':
        try:
            enqueue_missing_acks(str(config.get('voice') or ACK_DEFAULT_VOICE))
        except AckManifestError as error:
            logging.getLogger(__name__).warning('%s', error)


def create_app() -> FastAPI:
    """Build and return the fully-wired Korina Voice Lab FastAPI app."""
    import korina.config as _cfg  # late-bound so tests can monkeypatch

    @asynccontextmanager
    async def lifespan(app):
        from korina.converse import ensure_default_registered, set_active_converse_channel
        from korina.converse.registry import list_channels
        from korina.services.agent_service import shutdown_agent_worker

        _startup_generate_default_acks()
        ensure_default_registered()
        converse = _cfg.load_config().get('converse') or {}
        saved = converse.get('channel') if isinstance(converse, dict) else None
        channels = list_channels()
        if saved in channels:
            set_active_converse_channel(saved)
        elif 'korina' in channels:
            set_active_converse_channel('korina')
        try:
            yield
        finally:
            cancel_ack_generation()
            shutdown_agent_worker()

    app = FastAPI(
        title='Korina Voice Lab: Built-in Whisper, multimodal STT, llama.cpp, and Kokoro',
        lifespan=lifespan,
    )
    app.add_exception_handler(RequestValidationError, sanitized_validation_error)

    cfg = _cfg.load_config()
    allowed_origins = _cfg.config_converse_allowed_origins(cfg)
    app.add_middleware(RequestSizeLimit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        # Spec-compliant: wildcard+credentials is invalid. No frontend
        # code sends cookies (see commit: docs(cors): credentialed-fetch
        # audit). Credentials flow through env-var API keys at the
        # backend boundary, not browser cookie jars.
        allow_credentials=False,
        allow_methods=['GET', 'POST', 'DELETE'],
        allow_headers=['Content-Type', 'Authorization'],
    )
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    app.mount('/Ack', StaticFiles(directory=str(ACK_DIR)), name='ack')
    # APP_DIR resolves to a checkout, wheel assets, or explicit runtime tree.
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

    for module in (
        _index_routes, _health_routes, _config_routes, _models_routes,
        _model_roots_routes, _providers_routes, _capabilities_routes,
        _acks_routes, _agent_routes, _stt_routes, _chat_routes,
        _converse_routes,
    ):
        app.include_router(module.router)

    return app
