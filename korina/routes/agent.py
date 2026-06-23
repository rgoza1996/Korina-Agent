"""Agent endpoints — status, events, transcript, permission answer, reset, models, state-report.

Phase 1.9: inlined from the monolith's _route_agent* helpers.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from korina.config import agent_provider, load_config
from korina.runtime import state
from korina.schemas import AgentPermissionAnswer, AgentStateRequest, AgentTranscriptRequest
from korina.services.agent_service import (
    agent_model_choices,
    agent_snapshot,
    generate_agent_state_report,
    push_agent_event,
    submit_agent_transcript,
)
from korina.util.paths import LMSTUDIO_MODEL

router = APIRouter()


@router.get('/api/agent/status')
def agent_status():
    return {'ok': True, **agent_snapshot()}


@router.get('/api/agent/events')
def agent_events(after: int = Query(0)):
    with state.agent.lock:
        events = [e for e in state.agent.events if int(e.get('id', 0)) > after]
        last_id = state.agent.event_seq
    return {'ok': True, 'events': events, 'last_event_id': last_id, **agent_snapshot()}


@router.post('/api/agent/transcript')
async def agent_transcript(req: AgentTranscriptRequest):
    return submit_agent_transcript(req)


@router.post('/api/agent/permission-answer')
async def agent_permission_answer(req: AgentPermissionAnswer):
    push_agent_event({'type': 'permission_answer', 'priority': 'normal',
                       'request_id': req.request_id, 'answer': req.answer,
                       'transcript': req.transcript})
    return submit_agent_transcript(AgentTranscriptRequest(
        transcript=req.transcript, delivery_mode='prompt',
        reason=f'permission_answer:{req.answer}', turn_count=0,
    ))


@router.post('/api/agent/reset')
def agent_reset():
    with state.agent.lock:
        state.agent.events.clear()
        state.agent.event_seq = 0
        state.agent.pending_injections.clear()
        state.agent.busy = False
        state.agent.status = 'idle'
        state.agent.last_report = ''
        state.agent.last_error = None
        state.agent.last_emitted_report_hash = ''
        state.agent.last_emitted_report_at = 0.0
    return {'ok': True, 'status': agent_snapshot()}


@router.get('/api/agent/models')
def agent_models():
    try:
        models = agent_model_choices()
        return {'ok': True, 'models': models, 'provider': agent_provider(),
                'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL),
                'error': None}
    except Exception as e:
        return {'ok': False, 'models': [], 'provider': agent_provider(),
                'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL),
                'error': str(e)}


@router.post('/api/agent/state-report')
async def agent_state_report(req: AgentStateRequest):
    if str(load_config().get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'state_report': '', 'disabled': True}
    try:
        report = generate_agent_state_report(req)
        return {'ok': True, 'state_report': report,
                'model': req.model or str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))