"""Agent endpoints -- status, events, transcript, permission answer, reset, models, state-report.

Phase 1.9: inlined from the monolith's _route_agent* helpers.

Phase 3 (Converse/Agent adapter separation): handlers delegate to
``agent_gateway()`` instead of touching ``state.agent`` directly. The gateway
is the single seam between the route layer and any agent implementation.

Service functions that don't mutate ``AgentState`` (model choices, state
reports) keep their direct imports for now -- they are pure functions, not
state operations. Phase 4 will internalize them into the adapter when
``AgentState`` itself moves out of ``korina/runtime/state.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from korina.agents import agent_gateway
from korina.config import agent_provider, load_config
from korina.schemas import (
    AgentPermissionAnswer,
    AgentStateRequest,
    AgentTranscriptRequest,
    PermissionAnswerEvent,
    UserTurn,
)
from korina.services.agent_service import (
    agent_model_choices,
    generate_agent_state_report,
)
from korina.util.paths import LMSTUDIO_MODEL

router = APIRouter()


def _default_model() -> str:
    """Resolve the configured model name, matching the legacy fallback chain."""
    return str(
        load_config().get('agent_model')
        or load_config().get('lm_model')
        or LMSTUDIO_MODEL
    )


@router.get('/api/agent/status')
def agent_status():
    """Compat alias: same shape as legacy endpoint, served via the gateway."""
    return {'ok': True, **agent_gateway().snapshot()}


@router.get('/api/agent/events')
def agent_events(after: int = Query(0)):
    """Compat alias: raw dicts pass through unchanged (frontend contract)."""
    gateway = agent_gateway()
    cursor, events = gateway.poll_events(after)
    return {
        'ok': True,
        'events': events,  # already raw dicts from poll_events
        'last_event_id': cursor,
        **gateway.snapshot(),
    }


@router.post('/api/agent/transcript')
async def agent_transcript(req: AgentTranscriptRequest):
    """Legacy compat: preserve ``delivery_mode`` / ``reason`` / ``turn_count``.

    Phase 8 will add a native ``/api/agent/turn`` endpoint that accepts
    ``UserTurn`` directly. Until then, this endpoint passes the legacy
    ``AgentTranscriptRequest`` straight through via the adapter's
    compatibility method.
    """
    return agent_gateway().submit_transcript_legacy(req)


@router.post('/api/agent/permission-answer')
async def agent_permission_answer(req: AgentPermissionAnswer):
    """Legacy compat: push event + submit transcript, return legacy service shape."""
    return agent_gateway().answer_permission_legacy(req)


@router.post('/api/agent/reset')
def agent_reset():
    """Compat alias: reset via gateway, return legacy shape."""
    gateway = agent_gateway()
    gateway.reset()
    return {'ok': True, 'status': gateway.snapshot()}


@router.get('/api/agent/models')
def agent_models():
    """Service-function call; not state-coupled. No gateway change yet."""
    try:
        models = agent_model_choices()
        return {
            'ok': True,
            'models': models,
            'provider': agent_provider(),
            'default': _default_model(),
            'error': None,
        }
    except Exception as e:
        return {
            'ok': False,
            'models': [],
            'provider': agent_provider(),
            'default': _default_model(),
            'error': str(e),
        }


@router.post('/api/agent/state-report')
async def agent_state_report(req: AgentStateRequest):
    """Service-function call; not state-coupled. No gateway change yet."""
    if str(load_config().get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'state_report': '', 'disabled': True}
    try:
        report = generate_agent_state_report(req)
        return {
            'ok': True,
            'state_report': report,
            'model': req.model or _default_model(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))