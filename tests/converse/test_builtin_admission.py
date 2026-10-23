"""Definite built-in rejections never poison a channel's later submissions."""
from __future__ import annotations

import asyncio
import threading

import pytest

from korina.agents import gateway
from korina.agents.base import AgentAdmissionRejected
from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.agents.state import bind_builtin_admission, agent_state
from korina.converse import ConverseRequest
from korina.converse import korina_channel
from korina.schemas import PermissionAnswerEvent, UserTurn
from korina.services import agent_service


def request(text):
    return ConverseRequest(transcript=[{'role': 'user', 'text': text}])


@pytest.fixture
def builtin(monkeypatch, config_module):
    agent_service.shutdown_agent_worker()
    config_module.update_config_values({'agent_enabled': 'on', 'ack_enabled': 'off'})
    adapter = KorinaAgentAdapter()
    monkeypatch.setattr(gateway, '_active', adapter)
    channel = korina_channel.KorinaConverseChannel()
    channel._agent_gateway = lambda: adapter
    monkeypatch.setattr(korina_channel, '_POLL_INTERVAL', .001)
    monkeypatch.setattr(korina_channel, '_MAX_POLL_SECONDS', 1)
    model_calls = []

    def generate(req, **kwargs):
        model_calls.append(req.transcript[-1]['content'])
        return f'Priority: normal\nFresh: {model_calls[-1]}'

    monkeypatch.setattr(agent_service, 'generate_agent_state_report', generate)
    resets = []
    actual_reset = adapter.reset

    def reset():
        resets.append(True)
        actual_reset()

    monkeypatch.setattr(adapter, 'reset', reset)
    try:
        yield adapter, channel, model_calls, resets
    finally:
        agent_service.shutdown_agent_worker()


def test_disabled_send_can_retry_after_real_config_enable_without_cancel(builtin, client):
    _, channel, model_calls, resets = builtin

    async def exercise():
        assert client.post('/api/config', json={'agent_enabled': 'off'}).status_code == 200
        rejected = await channel.send(request('while disabled'))
        assert 'submit rejected' in rejected.error and 'disabled' in rejected.error
        assert model_calls == []
        assert channel._recovery_adapter is None
        assert agent_service.agent_snapshot()['last_event_id'] == 0
        assert client.post('/api/config', json={'agent_enabled': 'on'}).status_code == 200
        fresh = await channel.send(request('after enable'))
        assert fresh.error is None
        assert fresh.text == 'Priority: normal\nFresh: after enable'
        assert model_calls == ['after enable']
        assert resets == [], 'a definite rejection must never reset adapter memory'

    asyncio.run(exercise())


@pytest.mark.parametrize('transition', ['reset', 'off/on'])
def test_session_transition_cancels_a_stale_core_admission_without_waiting_for_a_report(builtin, client, monkeypatch, transition):
    _, channel, model_calls, resets = builtin
    entered, release = threading.Event(), threading.Event()
    actual_load = agent_service.load_config
    actual_submit = agent_service.submit_agent_transcript
    decisions = []

    def paused_load():
        config = actual_load()
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return config

    def submit(req, **kwargs):
        result = actual_submit(req, **kwargs)
        decisions.append(result)
        return result

    monkeypatch.setattr(agent_service, 'load_config', paused_load)
    monkeypatch.setattr(agent_service, 'submit_agent_transcript', submit)

    async def exercise():
        pending = asyncio.create_task(channel.send(request('old turn')))
        assert await asyncio.to_thread(entered.wait, 2)
        if transition == 'reset':
            assert client.post('/api/agent/reset').status_code == 200
        else:
            assert client.post('/api/config', json={'agent_enabled': 'off'}).status_code == 200
            assert client.post('/api/config', json={'agent_enabled': 'on'}).status_code == 200
        release.set()
        rejected = await pending
        assert rejected.error == 'cancelled'
        assert decisions[0]['accepted'] is False and decisions[0]['stale'] is True
        assert model_calls == []
        assert channel._recovery_adapter is None
        assert agent_service.agent_snapshot()['last_event_id'] == 0
        fresh = await channel.send(request('fresh after Clear'))
        assert fresh.error is None
        assert fresh.text == 'Priority: normal\nFresh: fresh after Clear'
        assert model_calls == ['fresh after Clear']
        assert resets == ([True] if transition == 'reset' else []), 'no extra adapter reset may follow the transition'

    try:
        asyncio.run(exercise())
    finally:
        release.set()


@pytest.mark.parametrize('operation', ['turn', 'permission'])
def test_core_rejections_use_a_shared_definite_exception(operation, builtin, config_module):
    adapter, _, model_calls, resets = builtin

    def invoke():
        if operation == 'turn':
            adapter.submit_turn(UserTurn(text='not admitted', session_id='fixture', turn_count=0))
        else:
            adapter.answer_permission(PermissionAnswerEvent(request_id='fixture', answer='yes', transcript='not admitted'))

    config_module.update_config_values({'agent_enabled': 'off'})
    with pytest.raises(AgentAdmissionRejected, match='disabled'):
        invoke()
    config_module.update_config_values({'agent_enabled': 'on'})
    with bind_builtin_admission(agent_state.generation - 1):
        with pytest.raises(AgentAdmissionRejected, match='session changed'):
            invoke()
    assert model_calls == []
    assert resets == []
    assert agent_service.agent_snapshot()['last_event_id'] == 0


def test_unknown_custom_submission_exception_still_requires_explicit_recovery(builtin):
    _, _, _, _ = builtin

    class UncertainAdapter(KorinaAgentAdapter):
        id = 'custom-uncertain'

        def __init__(self):
            self.submissions = 0
            self.resets = 0

        def submit_turn(self, turn):
            self.submissions += 1
            raise RuntimeError('The custom backend may already have accepted this')

        def reset(self):
            self.resets += 1

    adapter = UncertainAdapter()
    channel = korina_channel.KorinaConverseChannel()
    channel._agent_gateway = lambda: adapter

    async def exercise():
        failed = await channel.send(request('uncertain'))
        assert 'submit failed' in failed.error
        assert channel._recovery_adapter is adapter
        assert 'recovery required' in (await channel.send(request('must stay blocked'))).error
        assert adapter.submissions == 1
        assert adapter.resets == 0

    asyncio.run(exercise())
