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

from korina.agents import AgentHttpBridge, UnsupportedAgentOperation, agent_gateway
from korina.config import agent_provider, load_config
from korina.schemas import (
    AgentPermissionAnswer,
    AgentStateRequest,
    AgentTranscriptRequest,
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
    return {'ok': True, **AgentHttpBridge(agent_gateway()).snapshot()}


@router.get('/api/agent/events')
def agent_events(after: int = Query(0, ge=0)):
    """Compat alias: raw dicts pass through unchanged (frontend contract)."""
    gateway = agent_gateway()
    cursor, events = gateway.poll_events(after)
    return {
        'ok': True,
        'events': events,  # already raw dicts from poll_events
        **AgentHttpBridge(gateway).snapshot(),
        'last_event_id': cursor,
    }


@router.post('/api/agent/transcript')
def agent_transcript(req: AgentTranscriptRequest):
    """Preserve rich legacy delivery, or bridge prompt turns to a core adapter."""
    try:
        return AgentHttpBridge(agent_gateway()).submit_transcript(req)
    except UnsupportedAgentOperation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/api/agent/permission-answer')
def agent_permission_answer(req: AgentPermissionAnswer):
    """Legacy compat: push event + submit transcript, return legacy service shape."""
    try:
        return AgentHttpBridge(agent_gateway()).answer_permission(req)
    except UnsupportedAgentOperation as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/api/agent/reset')
def agent_reset():
    """Compat alias: reset via gateway, return legacy shape."""
    gateway = agent_gateway()
    gateway.reset()
    return {'ok': True, 'status': AgentHttpBridge(gateway).snapshot()}


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
def agent_state_report(req: AgentStateRequest):
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