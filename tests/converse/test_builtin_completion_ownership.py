"""Built-in channel reports cannot be borrowed from another HTTP/session turn."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from korina.agents import gateway
from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.agents.state import agent_state
from korina.converse import ConverseRequest
from korina.converse import korina_channel
from korina.services import agent_service as service


def turn(text):
    return ConverseRequest(transcript=[{'role': 'user', 'text': text}])


def submit_http(client, text):
    result = client.post('/api/agent/transcript', json={'transcript': [{'role': 'user', 'content': text}]})
    assert result.status_code == 200
    assert result.json()['accepted'] is True
    return result.json()


async def idle():
    for _ in range(300):
        if not service._worker_running:
            return
        await asyncio.sleep(.005)
    pytest.fail('fixture worker did not become idle')


@pytest.fixture
def builtin(monkeypatch, config_module):
    service.shutdown_agent_worker()
    config_module.update_config_values({'agent_enabled': 'on', 'ack_enabled': 'off'})
    adapter = KorinaAgentAdapter()
    monkeypatch.setattr(gateway, '_active', adapter)
    channel = korina_channel.KorinaConverseChannel()
    monkeypatch.setattr(korina_channel, '_POLL_INTERVAL', .001)
    monkeypatch.setattr(korina_channel, '_MAX_POLL_SECONDS', 2)
    try:
        yield adapter, channel
    finally:
        service.shutdown_agent_worker()


@pytest.mark.parametrize('race_initial_cursor', [False, True])
def test_other_http_work_is_a_definite_busy_rejection_and_retry_keeps_its_context(
    builtin, client, monkeypatch, race_initial_cursor,
):
    adapter, channel = builtin
    model_entered, release_model = threading.Event(), threading.Event()
    poll_entered, release_poll = threading.Event(), threading.Event()
    calls = []

    def generate(req, **kwargs):
        text = req.transcript[-1]['content']
        calls.append(text)
        if text == 'HTTP owns worker':
            model_entered.set()
            assert release_model.wait(3)
        return 'Priority: normal\nReport for ' + text

    monkeypatch.setattr(service, 'generate_agent_state_report', generate)
    if race_initial_cursor:
        poll = adapter.poll_events

        def paused_poll(cursor):
            result = poll(cursor)
            if not poll_entered.is_set():
                poll_entered.set()
                assert release_poll.wait(3)
            return result

        monkeypatch.setattr(adapter, 'poll_events', paused_poll)

    async def exercise():
        pending = None
        if race_initial_cursor:
            pending = asyncio.create_task(channel.send(turn('rejected channel turn')))
            assert await asyncio.to_thread(poll_entered.wait, 2)
        submit_http(client, 'HTTP owns worker')
        assert await asyncio.to_thread(model_entered.wait, 2)
        submit_http(client, 'Latest queued HTTP context')
        with agent_state.lock:
            before = (list(agent_state.events), list(agent_state.pending_injections), service._queued_prompt)
        release_poll.set()
        rejected = await pending if pending else await channel.send(turn('rejected channel turn'))
        assert 'submit rejected' in rejected.error and 'busy' in rejected.error
        assert channel._recovery_adapter is None
        with agent_state.lock:
            assert (agent_state.events, agent_state.pending_injections, service._queued_prompt) == before
        release_model.set()
        await idle()
        fresh = await channel.send(turn('channel retry'))
        assert fresh.error is None
        assert fresh.text == 'Priority: normal\nReport for channel retry'
        assert calls == ['HTTP owns worker', 'Latest queued HTTP context', 'channel retry']

    try:
        asyncio.run(exercise())
    finally:
        release_poll.set()
        release_model.set()


@pytest.mark.parametrize('transition', ['reset', 'off/on'])
def test_real_http_session_transition_cancels_old_channel_before_fresh_http_report(
    builtin, client, monkeypatch, transition,
):
    _, channel = builtin
    entered, release = threading.Event(), threading.Event()
    calls = []

    def generate(req, **kwargs):
        text = req.transcript[-1]['content']
        calls.append(text)
        if text == 'old channel session':
            entered.set()
            assert release.wait(3)
        return 'Priority: normal\nReport for ' + text

    monkeypatch.setattr(service, 'generate_agent_state_report', generate)

    async def exercise():
        pending = asyncio.create_task(channel.send(turn('old channel session')))
        assert await asyncio.to_thread(entered.wait, 2)
        if transition == 'reset':
            assert client.post('/api/agent/reset').status_code == 200
        else:
            assert client.post('/api/config', json={'agent_enabled': 'off'}).status_code == 200
            assert client.post('/api/config', json={'agent_enabled': 'on'}).status_code == 200
        submit_http(client, 'fresh HTTP session')
        release.set()
        result = await asyncio.wait_for(pending, 1)
        assert result.error == 'cancelled'
        assert result.text == ''
        assert channel._recovery_adapter is None
        await idle()
        assert service.agent_snapshot()['last_report'].endswith('fresh HTTP session')
        fresh = await channel.send(turn('fresh channel session'))
        assert fresh.error is None
        assert fresh.text.endswith('fresh channel session')
        assert calls == ['old channel session', 'fresh HTTP session', 'fresh channel session']

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_reset_during_initial_cursor_poll_prevents_core_submission(builtin, client, monkeypatch):
    adapter, channel = builtin
    entered, release = threading.Event(), threading.Event()
    poll = adapter.poll_events
    calls = []

    def paused_poll(cursor):
        result = poll(cursor)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(adapter, 'poll_events', paused_poll)
    monkeypatch.setattr(service, 'generate_agent_state_report', lambda *args, **kwargs: calls.append(True) or 'unexpected')

    async def exercise():
        pending = asyncio.create_task(channel.send(turn('before Clear')))
        assert await asyncio.to_thread(entered.wait, 2)
        assert client.post('/api/agent/reset').status_code == 200
        release.set()
        result = await pending
        assert result.error == 'cancelled'
        assert calls == []
        assert service.agent_snapshot()['last_event_id'] == 0
        assert channel._recovery_adapter is None

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_core_cursor_is_captured_at_admission_after_an_intervening_http_report(builtin, client, monkeypatch):
    adapter, channel = builtin
    entered, release = threading.Event(), threading.Event()
    poll = adapter.poll_events
    calls = []

    def paused_poll(cursor):
        result = poll(cursor)
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return result

    def generate(req, **kwargs):
        text = req.transcript[-1]['content']
        calls.append(text)
        return 'Priority: normal\nReport for ' + text

    monkeypatch.setattr(adapter, 'poll_events', paused_poll)
    monkeypatch.setattr(service, 'generate_agent_state_report', generate)

    async def exercise():
        pending = asyncio.create_task(channel.send(turn('owned channel turn')))
        assert await asyncio.to_thread(entered.wait, 2)
        submit_http(client, 'intervening completed HTTP turn')
        await idle()
        release.set()
        result = await pending
        assert result.error is None
        assert result.text == 'Priority: normal\nReport for owned channel turn'
        assert calls == ['intervening completed HTTP turn', 'owned channel turn']

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_suppressed_completion_uses_its_own_report_when_http_followup_already_finished(builtin, client, monkeypatch):
    adapter, channel = builtin
    entered, release = threading.Event(), threading.Event()
    submitted = threading.Event()
    same = 'Priority: normal\nUnchanged report.'

    def generate(req, **kwargs):
        text = req.transcript[-1]['content']
        if text == 'channel duplicate':
            entered.set()
            assert release.wait(3)
        return 'Priority: normal\nLater HTTP report.' if text == 'later HTTP turn' else same

    submit = adapter.submit_turn
    poll = adapter.poll_events

    def tracked_submit(turn):
        submit(turn)
        submitted.set()

    def delayed_poll(cursor):
        if submitted.is_set():
            deadline = time.monotonic() + 3
            while service._worker_running and time.monotonic() < deadline:
                time.sleep(.001)
            assert not service._worker_running
        return poll(cursor)

    monkeypatch.setattr(service, 'generate_agent_state_report', generate)
    monkeypatch.setattr(adapter, 'submit_turn', tracked_submit)
    monkeypatch.setattr(adapter, 'poll_events', delayed_poll)

    async def exercise():
        submit_http(client, 'seed duplicate report')
        await idle()
        pending = asyncio.create_task(channel.send(turn('channel duplicate')))
        assert await asyncio.to_thread(entered.wait, 2)
        submit_http(client, 'later HTTP turn')
        release.set()
        result = await pending
        assert service.agent_snapshot()['last_report'] == 'Priority: normal\nLater HTTP report.'
        assert result.error is None
        assert result.text == same
        suppressed = next(event for event in agent_state.events if event.get('status') == 'duplicate_report_suppressed')
        assert suppressed['report'] == same

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_a_lost_builtin_completion_requires_recovery_instead_of_borrowing_later_http_output(
    builtin, client, monkeypatch,
):
    adapter, channel = builtin
    entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
    poll, submit = adapter.poll_events, adapter.submit_turn

    def generate(req, **kwargs):
        return 'Priority: normal\nReport for ' + req.transcript[-1]['content']

    def tracked_submit(turn):
        submit(turn)
        submitted.set()

    def paused_poll(cursor):
        if submitted.is_set() and not release.is_set():
            entered.set()
            assert release.wait(5)
        return poll(cursor)

    monkeypatch.setattr(adapter, 'poll_events', paused_poll)
    monkeypatch.setattr(adapter, 'submit_turn', tracked_submit)
    monkeypatch.setattr(service, 'generate_agent_state_report', generate)

    async def exercise():
        pending = asyncio.create_task(channel.send(turn('owned channel turn')))
        assert await asyncio.to_thread(entered.wait, 2)
        await idle()
        for number in range(120):
            result = client.post('/api/agent/transcript', json={
                'delivery_mode': 'injection', 'transcript': [{'role': 'user', 'content': f'update {number}'}],
            })
            assert result.json()['accepted'] is True
        submit_http(client, 'later HTTP turn')
        await idle()
        assert agent_state.events[0]['id'] > 1
        release.set()
        result = await pending
        assert result.error == 'Agent event history was truncated'
        assert result.text == ''
        assert 'recovery required' in (await channel.send(turn('not admitted yet'))).error
        await channel.cancel()
        result = await channel.send(turn('fresh after explicit recovery'))
        assert result.error is None
        assert result.text.endswith('fresh after explicit recovery')

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_custom_adapter_and_replacement_ignore_builtin_epoch_and_keep_their_memory(builtin, client, monkeypatch):
    class CustomAdapter(KorinaAgentAdapter):
        id = 'independent-custom'

        def __init__(self, report):
            self.report = report
            self.events = []
            self.entered = threading.Event()
            self.resets = 0

        def submit_turn(self, turn):
            self.entered.set()

        def poll_events(self, cursor):
            return len(self.events), [event for event in self.events if event['id'] > cursor]

        def reset(self):
            self.resets += 1
            self.events.clear()

    first, replacement = CustomAdapter('First custom report.'), CustomAdapter('Replacement report.')
    monkeypatch.setattr(gateway, '_active', first)
    channel = korina_channel.KorinaConverseChannel()

    async def exercise():
        pending = asyncio.create_task(channel.send(turn('custom turn')))
        assert await asyncio.to_thread(first.entered.wait, 2)
        gateway.register_adapter(replacement)
        assert client.post('/api/config', json={'agent_enabled': 'off'}).status_code == 200
        assert client.post('/api/config', json={'agent_enabled': 'on'}).status_code == 200
        first.events.append({'id': 1, 'type': 'state_report', 'report': first.report})
        result = await pending
        assert result.text == first.report and result.error is None
        next_send = asyncio.create_task(channel.send(turn('replacement turn')))
        assert await asyncio.to_thread(replacement.entered.wait, 2)
        replacement.events.append({'id': 1, 'type': 'state_report', 'report': replacement.report})
        result = await next_send
        assert result.text == replacement.report and result.error is None
        assert first.resets == replacement.resets == 0

    asyncio.run(exercise())
