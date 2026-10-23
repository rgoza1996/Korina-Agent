"""Stateless reports keep admission settings and outbound model labels together."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from korina.routes import agent


@pytest.mark.parametrize('saved_model,request_model,expected_model', [
    ('  saved-old  ', None, 'saved-old'),
    ('', None, 'response-old'),
    ('saved-old', '  request-old  ', 'request-old'),
])
def test_report_model_remains_outbound_model_after_settings_save(
    client, mock_provider_http, saved_model, request_model, expected_model,
):
    assert client.post('/api/config', json={
        'agent_enabled': 'on', 'agent_provider': 'openai-compatible',
        'agent_base_url': 'https://old-agent.fixture/v1', 'agent_model': saved_model,
        'lm_model': '  response-old  ',
    }).status_code == 200
    entered, release = threading.Event(), threading.Event()
    calls = []

    def reply(request):
        payload = json.loads(request.content)
        calls.append((str(request.url), payload))
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return httpx.Response(200, json={'choices': [{'message': {
            'content': 'Priority: normal\nReport from ' + payload['model'],
        }}]})

    mock_provider_http(reply)
    body = {'transcript': [{'role': 'user', 'content': 'Remember the appointment.'}]}
    if request_model is not None:
        body['model'] = request_model
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, '/api/agent/state-report', json=body)
        assert entered.wait(2)
        try:
            saved = client.post('/api/config', json={
                'agent_base_url': 'https://new-agent.fixture/v1', 'agent_model': 'saved-new',
            })
            assert saved.status_code == 200
            assert saved.json()['agent_model'] == 'saved-new'
        finally:
            release.set()
        response = pending.result(timeout=2)
    assert calls[0][0] == 'https://old-agent.fixture/v1/chat/completions'
    assert calls[0][1]['model'] == expected_model
    assert response.status_code == 200
    assert response.json() == {
        'ok': True, 'state_report': 'Priority: normal\nReport from ' + expected_model,
        'model': expected_model,
    }
    fresh = client.post('/api/agent/state-report', json={'transcript': []})
    assert fresh.status_code == 200
    assert fresh.json()['model'] == 'saved-new'
    assert calls[1][0] == 'https://new-agent.fixture/v1/chat/completions'
    assert calls[1][1]['model'] == 'saved-new'


def test_report_generation_uses_the_same_config_as_enabled_admission(
    client, monkeypatch, mock_provider_http,
):
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    assert client.post('/api/config', json={
        'agent_enabled': 'on', 'agent_provider': 'meta', 'agent_model': 'admitted-old',
        'agent_reasoning_effort': 'low',
    }).status_code == 200
    entered, release = threading.Event(), threading.Event()
    generate = agent.generate_agent_state_report

    def held(req, **kwargs):
        entered.set()
        assert release.wait(3)
        return generate(req, **kwargs)

    monkeypatch.setattr(agent, 'generate_agent_state_report', held)
    calls = []

    def reply(request):
        calls.append((str(request.url), json.loads(request.content)))
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'Report.'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Report.'}}]})

    mock_provider_http(reply)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, '/api/agent/state-report', json={'transcript': []})
        assert entered.wait(2)
        try:
            saved = client.post('/api/config', json={
                'agent_enabled': 'off', 'agent_provider': 'anthropic',
                'agent_base_url': 'https://new-agent.fixture/v1', 'agent_model': 'saved-new',
                'agent_reasoning_effort': 'high',
            })
            assert saved.status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=2)
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'state_report': 'Report.', 'model': 'admitted-old'}
    assert len(calls) == 1
    assert calls[0][0] == 'https://api.meta.ai/v1/chat/completions'
    assert calls[0][1]['model'] == 'admitted-old'
    assert calls[0][1]['reasoning_effort'] == 'low'
    # The admitted stateless request completes; a new disabled request does not run.
    assert client.post('/api/agent/state-report', json={'transcript': []}).json() == {
        'ok': True, 'state_report': '', 'disabled': True,
    }
    assert len(calls) == 1
