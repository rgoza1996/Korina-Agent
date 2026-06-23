from __future__ import annotations

from fastapi import FastAPI

from . import acks, agent, chat, config, health, index, models, providers, stt


def register_routes(app: FastAPI, ctx: dict) -> None:
    """Register Korina Voice Lab API routers.

    Phase 1.6 keeps route implementations delegated to legacy helper
    functions while moving FastAPI route ownership into korina.routes modules.
    Later phases can move helper bodies behind services without changing the
    public routes again.
    """
    for module in (index, health, config, models, providers, acks, agent, stt, chat):
        module.init(ctx)
        app.include_router(module.router)
