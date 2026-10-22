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

from korina.agents.base import AgentAdapter, LegacyAgentHttpAdapter
from korina.schemas import AgentPermissionAnswer, AgentTranscriptRequest, PermissionAnswerEvent, UserTurn

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

class UnsupportedAgentOperation(ValueError):
    """An HTTP operation cannot be represented by the selected adapter."""


class AgentHttpBridge:
    """Keep legacy HTTP envelopes independent of optional adapter extensions.

    Full transcript/injection delivery is an optional richer contract. A
    core-only backend receives the latest user utterance for prompt delivery;
    injections are rejected instead of being misrepresented as new turns.
    """

    def __init__(self, adapter: AgentAdapter) -> None:
        self.adapter = adapter

    def snapshot(self) -> dict:
        if isinstance(self.adapter, LegacyAgentHttpAdapter):
            return self.adapter.snapshot()
        health = self.adapter.health_check()
        return {
            'adapter': self.adapter.id,
            'busy': bool(health.get('busy', False)),
            'status': health.get('status') or ('idle' if health.get('ok') else 'error'),
            'last_report': str(health.get('last_report') or ''),
            'pending_injections': int(health.get('pending_injections') or 0),
            'last_error': health.get('last_error', health.get('error')),
            'last_event_id': int(health.get('event_seq') or 0),
        }

    @staticmethod
    def _latest_user_text(transcript: list[dict[str, str]]) -> str:
        return next((turn['content'].strip() for turn in reversed(transcript)
                     if turn.get('role') == 'user' and turn.get('content', '').strip()), '')

    def submit_transcript(self, req: AgentTranscriptRequest) -> dict:
        from korina.config import load_config
        from korina.agents.state import agent_state

        with agent_state.lock:
            admission_generation = agent_state.generation
        if str(load_config().get('agent_enabled') or 'on') == 'off':
            return {'ok': True, 'accepted': False, 'disabled': True, 'status': self.snapshot()}
        with agent_state.lock:
            stale = admission_generation != agent_state.generation
        if stale:
            return {'ok': True, 'accepted': False, 'stale': True, 'status': self.snapshot()}
        if isinstance(self.adapter, LegacyAgentHttpAdapter):
            return self.adapter.submit_transcript_legacy(req)
        if req.delivery_mode != 'prompt':
            raise UnsupportedAgentOperation(
                f'Adapter {self.adapter.id!r} does not support legacy transcript injections'
            )
        text = self._latest_user_text(req.transcript)
        if not text:
            return {'ok': True, 'accepted': False, 'status': self.snapshot()}
        self.adapter.submit_turn(UserTurn(text=text, session_id='legacy-http', turn_count=req.turn_count))
        return {'ok': True, 'accepted': True, 'queued_as': 'prompt', 'status': self.snapshot()}

    def answer_permission(self, req: AgentPermissionAnswer) -> dict:
        from korina.config import load_config
        from korina.agents.state import agent_state

        with agent_state.lock:
            admission_generation = agent_state.generation
        if str(load_config().get('agent_enabled') or 'on') == 'off':
            return {'ok': True, 'accepted': False, 'disabled': True, 'status': self.snapshot()}
        with agent_state.lock:
            stale = admission_generation != agent_state.generation
        if stale:
            return {'ok': True, 'accepted': False, 'stale': True, 'status': self.snapshot()}
        if isinstance(self.adapter, LegacyAgentHttpAdapter):
            return self.adapter.answer_permission_legacy(req)
        if not self.adapter.capabilities.supports_permission_flow:
            raise UnsupportedAgentOperation(f'Adapter {self.adapter.id!r} does not support permissions')
        self.adapter.answer_permission(PermissionAnswerEvent(
            request_id=req.request_id, answer=req.answer or 'no',
            transcript=self._latest_user_text(req.transcript),
        ))
        return {'ok': True, 'accepted': True, 'queued_as': 'permission_answer', 'status': self.snapshot()}
