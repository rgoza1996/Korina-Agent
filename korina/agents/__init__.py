"""Agent adapter package.

Converse owns channel I/O and speaks to agents through ``AgentAdapter``.
Korina Agent supplies the built-in implementation; applications can register
their own adapter without changing the HTTP bridge's core contract.

Public API:
    - ``AgentAdapter`` -- structural protocol every adapter satisfies.
    - ``agent_gateway()`` -- get-or-create the active adapter instance.
    - ``register_adapter(adapter)`` -- swap the active adapter at runtime.
"""

from korina.agents.base import AgentAdapter, AgentAdmissionRejected, LegacyAgentHttpAdapter
from korina.agents.gateway import (
    AgentHttpBridge,
    UnsupportedAgentOperation,
    agent_gateway,
    register_adapter,
)

__all__ = [
    "AgentAdapter",
    "AgentAdmissionRejected",
    "LegacyAgentHttpAdapter",
    "AgentHttpBridge",
    "UnsupportedAgentOperation",
    "agent_gateway",
    "register_adapter",
]
