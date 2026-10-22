"""A pre-reset HTTP permission/turn cannot enter the next built-in session."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.agents.state import expected_builtin_generation
from korina.services import agent_service


@pytest.fixture
def builtin(monkeypatch, config_module):
    from korina.agents import gateway
    agent_service.shutdown_agent_worker()
    config_module.update_config_values({'agent_enabled': 'on'})
    adapter = KorinaAgentAdapter()
    monkeypatch.setattr(gateway, '_active', adapter)
    yield adapter
    agent_service.shutdown_agent_worker()


@pytest.mark.parametrize('transition', ['clear', 'disable-enable'])
@pytest.mark.parametrize('operation', ['permission', 'transcript'])
def test_original_http_generation_survives_dispatch_gap(builtin, client, monkeypatch, transition, operation):
    entered, release = threading.Event(), threading.Event()
    method = 'answer_permission_legacy' if operation == 'permission' else 'submit_transcript_legacy'
    original = getattr(builtin, method)
    bindings = []

    def paused(request):
        bindings.append(expected_builtin_generation())
        entered.set()
        assert release.wait(3)
        return original(request)

    monkeypatch.setattr(builtin, method, paused)
    monkeypatch.setattr(agent_service, 'generate_agent_state_report', lambda *args, **kwargs: pytest.fail('retired request reached model work'))
    endpoint = '/api/agent/permission-answer' if operation == 'permission' else '/api/agent/transcript'
    body = {'transcript': [{'role': 'user', 'content': 'Old session context'}]}
    if operation == 'permission':
        body.update({'request_id': 'old-permission', 'answer': 'yes'})
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, endpoint, json=body)
        assert entered.wait(1)
        try:
            if transition == 'clear':
                assert client.post('/api/agent/reset').status_code == 200
            else:
                assert client.post('/api/config', json={'agent_enabled': 'off'}).status_code == 200
                assert client.post('/api/config', json={'agent_enabled': 'on'}).status_code == 200
            assert client.get('/api/agent/events').json()['events'] == []
        finally:
            release.set()
        response = pending.result(timeout=2)
    assert bindings and isinstance(bindings[0], int)
    assert response.status_code == 200
    assert response.json()['accepted'] is False
    assert response.json()['stale'] is True
    assert client.get('/api/agent/events').json()['events'] == []
    assert agent_service._queued_prompt is None
    assert not agent_service._worker_running
    assert expected_builtin_generation() is None


@pytest.mark.parametrize('transition', ['clear', 'disable-enable'])
def test_permission_enabled_read_cannot_publish_or_queue_after_new_generation(builtin, client, config_module, monkeypatch, transition):
    entered, release = threading.Event(), threading.Event()
    saved = config_module.load_config()

    def paused_read():
        entered.set()
        assert release.wait(3)
        return saved

    monkeypatch.setattr(agent_service, 'load_config', paused_read)
    monkeypatch.setattr(agent_service, 'generate_agent_state_report', lambda *args, **kwargs: pytest.fail('retired permission reached model work'))
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, '/api/agent/permission-answer', json={
            'request_id': 'old-permission', 'answer': 'yes',
            'transcript': [{'role': 'user', 'content': 'Old permission answer'}],
        })
        assert entered.wait(1)
        try:
            if transition == 'clear':
                assert client.post('/api/agent/reset').status_code == 200
            else:
                assert client.post('/api/config', json={'agent_enabled': 'off'}).status_code == 200
                assert client.post('/api/config', json={'agent_enabled': 'on'}).status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=2)
    assert response.json()['accepted'] is False
    assert response.json()['stale'] is True
    assert client.get('/api/agent/events').json()['events'] == []
    assert not agent_service._worker_running


def test_disabled_direct_builtin_permission_does_not_publish_an_answer(builtin, config_module):
    from korina.schemas import PermissionAnswerEvent
    config_module.update_config_values({'agent_enabled': 'off'})
    with pytest.raises(RuntimeError, match='disabled'):
        builtin.answer_permission(PermissionAnswerEvent(request_id='old', answer='yes', transcript='Old answer'))
    assert builtin.poll_events(0) == (0, [])
