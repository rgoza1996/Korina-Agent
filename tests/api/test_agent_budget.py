"""Agent output-budget failures remain visible without losing prior context."""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from korina.agents import gateway
from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.agents.state import agent_state
from korina.services import agent_service


@pytest.fixture(params=['meta', 'openai-compatible'])
def builtin(monkeypatch, config_module, request):
    agent_service.shutdown_agent_worker()
    provider = request.param
    base = 'https://api.meta.ai/v1' if provider == 'meta' else 'https://agent-fixture.test/v1'
    config_module.update_config_values({
        'agent_enabled': 'on', 'agent_provider': provider, 'agent_model': 'team-fixture',
        'agent_base_url': base, 'agent_api_key': '' if provider == 'meta' else 'fixture-agent-key',
        'llm_api_key_env': '',
    })
    monkeypatch.setenv('MODEL_API_KEY', 'fixture-agent-key')
    monkeypatch.setattr(gateway, '_active', KorinaAgentAdapter())
    with agent_state.lock:
        agent_state.last_report = 'Priority: normal\nRemembered appointment: Tuesday.'
    try:
        yield base
    finally:
        agent_service.shutdown_agent_worker()


def submit(client, text):
    response = client.post('/api/agent/transcript', json={
        'transcript': [{'role': 'user', 'content': text}], 'delivery_mode': 'prompt',
    })
    assert response.status_code == 200
    assert response.json()['accepted'] is True


def idle_status(client):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get('/api/agent/status')
        assert response.status_code == 200
        snapshot = response.json()
        if not snapshot['busy']:
            return snapshot
        time.sleep(.005)
    pytest.fail('Agent worker did not become idle')


def test_agent_budget_error_is_visible_and_same_worker_can_retry_without_losing_memory(
    builtin, client, mock_provider_http,
):
    calls = []

    def reply(request):
        assert str(request.url) == f'{builtin}/chat/completions'
        assert request.headers['Authorization'] == 'Bearer fixture-agent-key'
        calls.append((threading.get_ident(), json.loads(request.content)))
        content = None if len(calls) == 1 else 'Priority: normal\nRecovered report: appointment Tuesday.'
        return httpx.Response(200, json={
            'choices': [{'message': {'content': content}, 'finish_reason': 'length' if content is None else 'stop'}],
        })

    mock_provider_http(reply)
    previous = agent_service.agent_snapshot()['last_report']
    submit(client, 'Remember the appointment.')
    failed = idle_status(client)
    executor = agent_service._executor
    assert failed['last_report'] == previous
    assert failed['last_error'] and 'State report max tokens' in failed['last_error']
    events = client.get('/api/agent/events').json()['events']
    assert any(event['type'] == 'agent_error' and 'output-token budget' in event['message'] for event in events)
    assert not any(event.get('status') == 'empty_report_suppressed' for event in events)
    assert len(calls) == 1, 'An exhausted budget must not trigger an automatic retry'
    assert calls[0][1]['max_tokens'] == 512
    assert 'reasoning' not in calls[0][1] and 'reasoning_effort' not in calls[0][1]
    saved = client.post('/api/config', json={'agent_max_tokens': 2048})
    assert saved.status_code == 200
    submit(client, 'Use the remembered appointment.')
    recovered = idle_status(client)
    assert recovered['last_error'] is None
    assert recovered['last_report'] == 'Priority: normal\nRecovered report: appointment Tuesday.'
    assert agent_service._executor is executor
    assert calls[0][0] == calls[1][0]
    assert len(calls) == 2
    assert calls[1][1]['max_tokens'] == 2048
    assert json.loads(calls[1][1]['messages'][1]['content'])['previous_report'] == previous
    assert any(event['type'] == 'state_report' and event['report'] == recovered['last_report']
               for event in client.get('/api/agent/events').json()['events'])


@pytest.mark.parametrize('content', [None, '', '   '])
def test_intentional_empty_stop_still_suppresses_report_without_losing_memory(
    builtin, client, mock_provider_http, content,
):
    mock_provider_http(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
    }))
    previous = agent_service.agent_snapshot()['last_report']
    submit(client, 'An intentional empty report.')
    snapshot = idle_status(client)
    assert snapshot['last_report'] == previous
    assert snapshot['last_error'] is None
    events = client.get('/api/agent/events').json()['events']
    assert any(event.get('status') == 'empty_report_suppressed' for event in events)
    assert not any(event['type'] == 'agent_error' for event in events)


def test_visible_length_limited_agent_report_keeps_existing_behavior(builtin, client, mock_provider_http):
    report = 'Priority: normal\nA visible partial report.'
    mock_provider_http(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': report}, 'finish_reason': 'length'}],
    }))
    submit(client, 'Return visible report text.')
    snapshot = idle_status(client)
    assert snapshot['last_report'] == report
    assert snapshot['last_error'] is None
    assert any(event['type'] == 'state_report' and event['report'] == report
               for event in client.get('/api/agent/events').json()['events'])
