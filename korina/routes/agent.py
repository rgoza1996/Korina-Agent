from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter()
_ctx: dict = {}


def init(ctx: dict) -> None:
    _ctx.clear(); _ctx.update(ctx)


@router.get('/api/agent/status')
def agent_status():
    return _ctx['_route_agent_status']()


@router.get('/api/agent/events')
def agent_events(after: int = Query(0)):
    return _ctx['_route_agent_events'](after)


@router.post('/api/agent/transcript')
async def agent_transcript(request: Request):
    req = _ctx['AgentTranscriptRequest'](**(await request.json()))
    return _ctx['_route_agent_transcript'](req)


@router.post('/api/agent/permission-answer')
async def agent_permission_answer(request: Request):
    req = _ctx['AgentPermissionAnswer'](**(await request.json()))
    return _ctx['_route_agent_permission_answer'](req)


@router.post('/api/agent/reset')
def agent_reset():
    return _ctx['_route_agent_reset']()


@router.get('/api/agent/models')
def agent_models():
    return _ctx['_route_agent_models']()


@router.post('/api/agent/state-report')
async def agent_state_report(request: Request):
    req = _ctx['AgentStateRequest'](**(await request.json()))
    return _ctx['_route_agent_state_report'](req)
