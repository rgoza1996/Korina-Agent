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
from korina.routes.validation import SanitizedValidationRoute

from korina.agents import AgentHttpBridge, LegacyAgentHttpAdapter, UnsupportedAgentOperation, agent_gateway
from korina.config import agent_models_url, agent_provider, load_config
from korina.schemas import (
    AgentPermissionAnswer,
    AgentModelDiscoveryRequest,
    AgentProtocolInfo,
    AgentStateRequest,
    AgentTranscriptRequest,
)
from korina.services.agent_service import (
    agent_model_choices,
    generate_agent_state_report,
)
from korina.util.paths import LMSTUDIO_MODEL
from korina.runtime.json_http import HTTPStatusError
from korina.model_ids import limit_model_ids, model_list_info
from korina.services.agent_events import AgentEventLimitError, bounded_events_response

router = APIRouter()


def _default_model(config: dict | None = None) -> str:
    """Resolve the configured model name, matching the legacy fallback chain."""
    config = config if config is not None else load_config()
    return str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL)


@router.get('/api/agent/capabilities', response_model=AgentProtocolInfo)
def agent_capabilities():
    """Describe the real bridge surface without submitting work or probing models."""
    adapter = agent_gateway()
    legacy = isinstance(adapter, LegacyAgentHttpAdapter)
    return AgentProtocolInfo(
        adapter_id=adapter.id,
        adapter_capabilities=adapter.capabilities,
        legacy_http_supported=legacy,
        http_operations={
            'status': True,
            'events': True,
            'reset': True,
            'transcript_prompt': True,
            'transcript_injection': legacy,
            'permission_answer': legacy or adapter.capabilities.supports_permission_flow,
        },
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
    try:
        return bounded_events_response(cursor, events, AgentHttpBridge(gateway).snapshot())
    except AgentEventLimitError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    """Discover the saved endpoint, preserving the existing GET contract."""
    return _discover_agent_models(load_config())


def _discover_agent_models(config: dict, *, candidate: bool = False):
    result = {'provider': agent_provider(config), 'default': _default_model(config)}
    try:
        discovered = agent_model_choices(config)
        choices = limit_model_ids(discovered, pinned=[result['default']], sources=[discovered])
        return {'ok': True, 'models': choices, 'catalog_info': model_list_info(choices), **result, 'error': None}
    except Exception as e:
        # Candidate keys travel only in the request body. Do not reflect an
        # upstream diagnostic that could contain those credentials.
        error = str(e)
        if candidate:
            error = f'Agent model discovery HTTP {e.status_code}' if isinstance(e, HTTPStatusError) else f'Agent model discovery failed ({type(e).__name__})'
        return {'ok': False, 'models': [], **result, 'error': error}


def agent_candidate_models(req: AgentModelDiscoveryRequest):
    """Discover without saving; omitted keys inherit only for the saved API URL."""
    saved = load_config()
    updates = req.model_dump(exclude_unset=True)
    config = {**saved, **updates}
    if 'agent_api_key' not in updates and agent_models_url(config) != agent_models_url(saved):
        config['agent_api_key'] = ''
    return _discover_agent_models(config, candidate=True)


router.add_api_route('/api/agent/models', agent_candidate_models, methods=['POST'],
                     route_class_override=SanitizedValidationRoute)


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
