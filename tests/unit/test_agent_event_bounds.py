"""Retained events and actual HTTP envelopes obey finite UTF-8 JSON budgets."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korina.agents import agent_gateway, register_adapter
from korina.agents.state import agent_state
from korina.routes import agent as agent_routes
from korina.schemas import AgentTranscriptRequest
from korina.services import agent_service
from korina.services.agent_events import (
    AgentEventLimitError, MAX_AGENT_EVENT_BYTES, MAX_AGENT_RESPONSE_BYTES, agent_json_size,
)


@pytest.fixture(autouse=True)
def clean_agent():
    adapter = agent_gateway()
    agent_service.shutdown_agent_worker()
    yield
    register_adapter(adapter)
    agent_service.shutdown_agent_worker()


def test_cached_event_sizes_evict_whole_oldest_events_and_reset(monkeypatch):
    real_size = agent_service.agent_json_size
    calls = []

    def sized(value):
        calls.append(value['id'])
        return real_size(value)

    monkeypatch.setattr(agent_service, 'agent_json_size', sized)
    for index in range(100):
        agent_service.push_agent_event({'type': 'fixture', 'message': '😀' * 20000, 'index': index})
    assert len(calls) == 100  # Existing events were not serialized on each push.
    assert agent_state.event_bytes == sum(real_size(event) for event in agent_state.events)
    assert agent_state.event_bytes <= MAX_AGENT_EVENT_BYTES
    ids = [event['id'] for event in agent_state.events]
    assert ids == list(range(ids[0], 101)) and ids[0] > 1
    assert agent_state.events[-1]['index'] == 99
    assert len(agent_state.event_sizes) == len(agent_state.events)
    agent_service.reset_agent_state()
    assert agent_state.event_bytes == 0 and agent_state.event_sizes == [] and agent_state.events == []


def test_small_event_count_eviction_keeps_size_cache_aligned():
    for _ in range(101):
        agent_service.push_agent_event({'type': 'fixture', 'message': 'small'})
    assert len(agent_state.events) == len(agent_state.event_sizes) == 100
    assert [event['id'] for event in agent_state.events] == list(range(2, 102))
    assert agent_state.event_bytes == sum(agent_state.event_sizes)
    assert agent_state.event_bytes == sum(agent_json_size(event) for event in agent_state.events)


def test_oversize_or_invalid_push_never_retains_input_or_unbounded_diagnostics():
    for message in ['private-' + 'x' * MAX_AGENT_EVENT_BYTES, float('nan'), '\ud800']:
        with pytest.raises(AgentEventLimitError) as caught:
            agent_service.push_agent_event({'type': 'fixture', 'message': message})
        assert len(str(caught.value)) < 128 and 'private-' not in str(caught.value)
        assert agent_state.events == [] and agent_state.event_bytes == 0 and agent_state.event_seq == 0


def test_max_unicode_context_matches_wire_and_100_updates_stay_bounded(monkeypatch):
    config = {'agent_enabled': 'on', 'agent_provider': 'openai-compatible', 'agent_max_turns': 64,
              'agent_model': 'fixture-model', 'agent_base_url': 'http://fixture.invalid/v1',
              'agent_api_key': '', 'llm_api_key_env': ''}
    monkeypatch.setattr(agent_service, 'load_config', lambda: config)
    wire = []

    def request(url, *, json_body, **kwargs):
        wire[:] = [json.loads(json_body['messages'][1]['content'])]
        return {'choices': [{'message': {'content': f'Priority: normal\nUpdate {agent_state.event_seq} ' + '😀' * 6000}}]}

    monkeypatch.setattr(agent_service, 'request_json', request)
    # Four-byte Unicode exercises the maximum context's actual UTF-8 footprint.
    text = '😀' * 8192
    req = AgentTranscriptRequest(transcript=[{'role': 'user', 'content': text} for _ in range(64)])
    agent_state.last_report = '😀' * 6000
    for _ in range(100):
        agent_service._process_transcript(req, agent_state.generation)
    report_event = agent_state.events[-1]
    context = report_event['agent_input']
    assert context['transcript'] == wire[0]['recent_transcript']
    assert context['previous_report'] == wire[0]['previous_report']
    assert len(context['transcript']) == 64 and len(context['transcript'][0]['content']) == 2000
    assert len(context['previous_report']) == 4000
    assert len(req.transcript[0]['content']) == 8192
    assert agent_state.event_seq == 200 and len(agent_state.events) < 100
    assert agent_state.event_bytes == sum(agent_json_size(event) for event in agent_state.events)
    assert agent_state.event_bytes <= MAX_AGENT_EVENT_BYTES
    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        response = client.get('/api/agent/events?after=0')
        assert response.status_code == 200 and len(response.content) <= MAX_AGENT_RESPONSE_BYTES
        payload = response.json()
        assert payload['last_event_id'] == 200
        assert payload['events'][-1]['id'] == 200
        assert payload['events'][-1]['agent_input'] == context
        latest = client.get('/api/agent/events?after=200').json()
        assert latest['events'] == [] and latest['last_event_id'] == 200


def test_response_envelope_evicts_only_oldest_whole_event_and_preserves_cursor():
    agent_state.last_report = '😀"\\\n' * 1000
    for index in range(2):
        agent_service.push_agent_event({'type': 'fixture', 'index': index,
                                       'message': 'x' * (MAX_AGENT_EVENT_BYTES // 2 - 150)})
    assert len(agent_state.events) == 2 and agent_state.event_bytes <= MAX_AGENT_EVENT_BYTES
    app = FastAPI()
    app.include_router(agent_routes.router)
    with TestClient(app) as client:
        response = client.get('/api/agent/events')
    assert response.status_code == 200 and len(response.content) <= MAX_AGENT_RESPONSE_BYTES
    payload = response.json()
    assert payload['events_truncated'] is True and payload['events_omitted'] == 1
    assert [event['id'] for event in payload['events']] == [2]
    assert payload['events'][0]['message'] == agent_state.events[-1]['message']
    assert payload['last_event_id'] == 2


def test_custom_snapshot_over_budget_is_clear_bounded_error():
    original = agent_gateway()

    class LargeSnapshot:
        def poll_events(self, cursor):
            return 1, []

    # Patch only this route's gateway/bridge seam: arbitrary adapters may have
    # valid core methods while returning an excessive diagnostic snapshot.
    from unittest.mock import patch
    app = FastAPI()
    app.include_router(agent_routes.router)
    with patch.object(agent_routes, 'agent_gateway', return_value=LargeSnapshot()), patch.object(
        agent_routes.AgentHttpBridge, 'snapshot', return_value={'last_report': 'private-' + 'x' * MAX_AGENT_RESPONSE_BYTES},
    ), TestClient(app) as client:
        response = client.get('/api/agent/events')
    assert response.status_code == 502 and len(response.content) < 256
    assert '4 MiB' in response.json()['detail'] and b'private-' not in response.content
    assert agent_gateway() is original
