"""AgentGateway -- runtime-selected adapter injector.

The gateway is the single mutable global that holds the active adapter
instance. Routes call :func:`agent_gateway` to reach whichever adapter is
currently registered; the gateway lazily constructs the default
``KorinaAgentAdapter`` on first call.

Phase 3: route handlers migrate from direct ``state.agent`` access to
``agent_gateway()``. Phase 4: ``AgentState`` moves into the adapter itself.
"""

from __future__ import annotations

from typing import Optional

from korina.agents.base import AgentAdapter

# Module-level mutable singleton. Tests can swap this via :func:`register_adapter`.
_active: Optional[AgentAdapter] = None


def register_adapter(adapter: AgentAdapter) -> None:
    """Swap the active adapter. Idempotent: last call wins.

    Typically called once at app startup (after config load) or in tests.
    """
    global _active
    _active = adapter


def agent_gateway() -> AgentAdapter:
    """Return the currently active adapter.

    Lazy-loads :class:`KorinaAgentAdapter` as the default if nothing has been
    registered. The lazy import avoids a circular dependency: ``korina_adapter``
    imports ``agent_service``, which itself imports ``schemas`` and
    ``runtime.state``. Importing the adapter at module load would force
    that whole chain to resolve before any consumer of the gateway runs.
    """
    global _active
    if _active is None:
        from korina.agents.korina_adapter import KorinaAgentAdapter
        _active = KorinaAgentAdapter()
    return _active