"""Agent adapter package.

Phase 2 (Converse/Agent adapter separation). Converse owns channel I/O and
speaks to agents through the ``AgentAdapter`` protocol. Korina Agent is one
adapter implementation; Hermes Agent, OpenClaw, etc. are future additions.

Public API:
    - ``AgentAdapter`` -- structural protocol every adapter satisfies.
    - ``agent_gateway()`` -- get-or-create the active adapter instance.
    - ``register_adapter(adapter)`` -- swap the active adapter at runtime.
"""

from korina.agents.base import AgentAdapter
from korina.agents.gateway import (
    agent_gateway,
    register_adapter,
)

__all__ = [
    "AgentAdapter",
    "agent_gateway",
    "register_adapter",
]