"""The documented core adapter is sufficient for the public agent routes."""
from __future__ import annotations

import pytest

from korina.agents import AgentAdapter, LegacyAgentHttpAdapter
from korina.schemas import AgentCapabilities


class CoreAdapter:
    id = 'fake'
    capabilities = AgentCapabilities(adapter_id='fake', supports_injections=False)

    def __init__(self):
        self.turns = []
        self.answers = []
        self.events = []
        self.resets = 0

    def submit_turn(self, turn):
        self.turns.append(turn)
        self.events.append({'id': len(self.events) + 1, 'type': 'state_report', 'report': 'Received ' + turn.text})

    def poll_events(self, cursor):
        return len(self.events), [event for event in self.events if event['id'] > cursor]

    def answer_permission(self, answer):
        self.answers.append(answer)

    def reset(self):
        self.events.clear()
        self.resets += 1

    def health_check(self):
        # A lightweight health payload need not carry the events cursor.
        return {'ok': True, 'status': 'idle'}


@pytest.fixture
def adapter(monkeypatch, config_module):
    from korina.agents import gateway

    config = config_module.load_config()
    config_module.save_config(dict(config, agent_enabled='on'))
    monkeypatch.setattr(gateway, '_active', gateway._active)
    fake = CoreAdapter()
    gateway.register_adapter(fake)
    assert isinstance(fake, AgentAdapter)
    assert not isinstance(fake, LegacyAgentHttpAdapter)
    return fake


def test_core_adapter_status_transcript_events_and_reset(adapter, client):
    status = client.get('/api/agent/status')
    assert status.status_code == 200
    assert status.json()['adapter'] == 'fake'
    assert status.json()['busy'] is False

    response = client.post('/api/agent/transcript', json={
        'transcript': [
            {'role': 'user', 'content': 'Earlier user turn'},
            {'role': 'assistant', 'content': 'An earlier response'},
            {'role': 'user', 'content': 'Latest request'},
            {'role': 'assistant', 'content': 'Do not deliver me as user input'},
        ],
        'turn_count': 3,
    })
    assert response.status_code == 200
    assert response.json()['accepted'] is True
    assert [turn.text for turn in adapter.turns] == ['Latest request']
    assert adapter.turns[0].turn_count == 3
    assert adapter.turns[0].session_id == 'legacy-http'

    events = client.get('/api/agent/events?after=0').json()
    assert events['last_event_id'] == 1  # authoritative poll cursor, not health's default
    assert events['events'][0]['report'] == 'Received Latest request'
    assert client.get('/api/agent/events?after=1').json()['events'] == []

    reset = client.post('/api/agent/reset')
    assert reset.status_code == 200
    assert reset.json()['status']['status'] == 'idle'
    assert adapter.resets == 1
    assert client.get('/api/agent/events').json()['events'] == []


def test_core_adapter_permission_uses_native_event_once(adapter, client):
    response = client.post('/api/agent/permission-answer', json={
        'request_id': 'permission-1', 'answer': 'yes',
        'transcript': [{'role': 'user', 'content': 'Please continue'}],
    })
    assert response.status_code == 200
    assert len(adapter.answers) == 1
    assert adapter.answers[0].request_id == 'permission-1'
    assert adapter.answers[0].answer == 'yes'
    assert adapter.answers[0].transcript == 'Please continue'
    assert adapter.turns == []  # the native permission method owns follow-up work


def test_core_adapter_rejects_unsupported_injection_without_submitting(adapter, client):
    response = client.post('/api/agent/transcript', json={
        'transcript': [{'role': 'user', 'content': 'Quiet context'}],
        'delivery_mode': 'injection',
    })
    assert response.status_code == 409
    assert 'injections' in response.json()['detail']
    assert adapter.turns == []


def test_disabled_core_adapter_is_not_called(adapter, config_module, client):
    config_module.save_config(dict(config_module.load_config(), agent_enabled='off'))
    response = client.post('/api/agent/transcript', json={
        'transcript': [{'role': 'user', 'content': 'Do not deliver'}],
    })
    assert response.status_code == 200
    assert response.json()['disabled'] is True
    assert response.json()['accepted'] is False
    assert adapter.turns == []


def test_unsupported_permission_flow_is_explicit(adapter, client):
    adapter.capabilities = AgentCapabilities(adapter_id='fake', supports_permission_flow=False)
    response = client.post('/api/agent/permission-answer', json={'answer': 'yes'})
    assert response.status_code == 409
    assert adapter.answers == []


def test_richer_legacy_adapter_keeps_full_request_and_response_shape(adapter, client):
    from korina.agents import register_adapter

    class LegacyAdapter(CoreAdapter):
        def snapshot(self):
            return {'busy': False, 'status': 'legacy-state', 'custom': 42}

        def submit_transcript_legacy(self, request):
            self.request = request
            return {'ok': True, 'accepted': True, 'queued_as': request.delivery_mode, 'custom': 42}

        def answer_permission_legacy(self, request):
            self.answer_request = request
            return {'ok': True, 'accepted': True, 'legacy_permission': True}

    legacy = LegacyAdapter()
    register_adapter(legacy)
    transcript = [{'role': 'user', 'content': 'One'}, {'role': 'assistant', 'content': 'Two'}]
    response = client.post('/api/agent/transcript', json={
        'transcript': transcript, 'delivery_mode': 'injection', 'reason': 'background', 'turn_count': 7,
    })
    assert response.json() == {'ok': True, 'accepted': True, 'queued_as': 'injection', 'custom': 42}
    assert legacy.request.transcript == transcript
    assert legacy.request.reason == 'background'
    assert legacy.request.turn_count == 7
    assert client.get('/api/agent/status').json()['custom'] == 42
    permission = client.post('/api/agent/permission-answer', json={'answer': 'no', 'transcript': transcript})
    assert permission.json()['legacy_permission'] is True
    assert legacy.answer_request.transcript == transcript
    assert legacy.turns == legacy.answers == []
