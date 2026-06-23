"""Korina route registry.

Phase 1.9: this module now only re-exports the submodules. The ``create_app()``
function in ``korina/app_factory.py`` is responsible for wiring routers
onto the FastAPI instance. Phase 1.6's ``register_routes(app, ctx)``
helper is gone — route bodies no longer live in the monolith, so the
``globals()`` indirection is no longer needed.
"""

from __future__ import annotations

from . import acks, agent, chat, config, health, index, models, providers, stt

__all__ = ["acks", "agent", "chat", "config", "health", "index",
           "models", "providers", "stt"]