"""Uncorrelated late reports cannot complete a send after timeout/cancellation."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from korina.converse import ConverseRequest
from korina.converse import korina_channel
from korina.schemas import AgentCapabilities


class Adapter:
    id = 'recovery-test'
    capabilities = AgentCapabilities(adapter_id=id)

    def __init__(self):
        self.events = []
        self.submitted = []
        self.reset_count = 0
        self.fail_reset = False
        self.emit = False
        self.submitted_event = threading.Event()

    def publish(self, report):
        self.events.append({'id': len(self.events) + 1, 'type': 'state_report', 'report': report})

    def submit_turn(self, turn):
        self.submitted.append(turn.text)
        self.submitted_event.set()
        if self.emit:
            self.publish('Fresh: ' + turn.text)

    def poll_events(self, cursor):
        return len(self.events), [event for event in self.events if event['id'] > cursor]

    def answer_permission(self, answer):
        pass

    def reset(self):
        self.reset_count += 1
        if self.fail_reset:
            raise RuntimeError('reset failed')
        self.events.clear()

    def health_check(self):
        return {'ok': True}


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(korina_channel, '_MAX_POLL_SECONDS', .02)
    monkeypatch.setattr(korina_channel, '_POLL_INTERVAL', .001)


def request(text):
    return ConverseRequest(transcript=[{'role': 'user', 'text': text}])


def channel(adapter):
    result = korina_channel.KorinaConverseChannel()
    result._agent_gateway = lambda: adapter
    return result


@pytest.mark.parametrize('falsey_adapter', [False, True])
def test_timeout_requires_successful_reset_of_captured_adapter_even_after_replacement(falsey_adapter):
    class FalseyAdapter(Adapter):
        def __bool__(self):
            return False

    original, replacement = (FalseyAdapter() if falsey_adapter else Adapter()), Adapter()
    active = [original]
    ch = channel(original)
    ch._agent_gateway = lambda: active[0]

    async def exercise():
        assert (await ch.send(request('first'))).error == 'timeout'
        original.publish('Late first report')
        active[0] = replacement
        rejected = await ch.send(request('second'))
        assert 'call cancel() successfully' in rejected.error
        assert replacement.submitted == []
        assert original.reset_count == 0, 'timeouts never implicitly reset custom state'
        original.fail_reset = True
        with pytest.raises(RuntimeError, match='reset failed'):
            await ch.cancel()
        assert 'recovery required' in (await ch.send(request('still blocked'))).error
        original.fail_reset = False
        await ch.cancel()
        assert original.reset_count == 2
        assert replacement.reset_count == 0
        replacement.emit = True
        fresh = await ch.send(request('fresh'))
        assert fresh.error is None
        assert fresh.text == 'Fresh: fresh'
        assert original.submitted == ['first']
        assert replacement.submitted == ['fresh']

    asyncio.run(exercise())


def test_caller_cancelled_error_blocks_reuse_and_discards_late_blocking_poll(monkeypatch):
    adapter = Adapter()
    ch = channel(adapter)
    started, release = threading.Event(), threading.Event()
    ordinary_poll = adapter.poll_events
    first_poll = True

    def paused_poll(cursor):
        nonlocal first_poll
        if adapter.submitted and first_poll:
            first_poll = False
            started.set()
            assert release.wait(2)
            return 99, [{'id': 99, 'type': 'state_report', 'report': 'Stale blocking poll'}]
        return ordinary_poll(cursor)

    monkeypatch.setattr(adapter, 'poll_events', paused_poll)

    async def exercise():
        task = asyncio.create_task(ch.send(request('old')))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert 'recovery required' in (await ch.send(request('blocked'))).error
        assert adapter.submitted == ['old']
        await ch.cancel()
        adapter.emit = True
        fresh = await ch.send(request('fresh'))
        assert fresh.text == 'Fresh: fresh'
        assert fresh.error is None
        release.set()
        await asyncio.sleep(.01)
        assert fresh.text != 'Stale blocking poll'

    try:
        asyncio.run(exercise())
    finally:
        release.set()


def test_cancelling_a_queued_send_before_admission_does_not_poison_active_or_next_send(monkeypatch):
    adapter = Adapter()
    ch = channel(adapter)
    # This case exercises queue cancellation, not the timeout policy. Keep
    # only the channel deadline clock still so scheduler load cannot retire
    # the active request before the queued caller starts waiting.
    monkeypatch.setattr(korina_channel, 'time', SimpleNamespace(monotonic=lambda: 0.0))

    async def exercise():
        first = asyncio.create_task(ch.send(request('first')))
        assert await asyncio.to_thread(adapter.submitted_event.wait, 1)
        queued_started = asyncio.Event()

        async def send_queued():
            queued_started.set()
            return await ch.send(request('queued'))

        queued = asyncio.create_task(send_queued())
        await asyncio.wait_for(queued_started.wait(), 2)
        assert not first.done()
        assert not queued.done()
        assert adapter.submitted == ['first']
        assert queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(queued, 2)
        adapter.publish('Fresh: first')
        assert (await asyncio.wait_for(first, 2)).text == 'Fresh: first'
        adapter.emit = True
        assert (await asyncio.wait_for(ch.send(request('third')), 2)).text == 'Fresh: third'
        assert adapter.submitted == ['first', 'third']
        assert adapter.reset_count == 0

    asyncio.run(exercise())


def test_poll_failure_after_submission_requires_explicit_recovery(monkeypatch):
    adapter = Adapter()
    ch = channel(adapter)
    original_poll = adapter.poll_events

    def failed_poll(cursor):
        if adapter.submitted:
            raise OSError('poll unavailable')
        return original_poll(cursor)

    monkeypatch.setattr(adapter, 'poll_events', failed_poll)

    async def exercise():
        first = await ch.send(request('old'))
        assert first.error == 'poll_events failed: poll unavailable'
        assert 'recovery required' in (await ch.send(request('blocked'))).error
        monkeypatch.setattr(adapter, 'poll_events', original_poll)
        await ch.cancel()
        adapter.emit = True
        assert (await ch.send(request('fresh'))).text == 'Fresh: fresh'

    asyncio.run(exercise())


@pytest.mark.parametrize('completion', [
    {'type': 'agent_status', 'status': 'empty_report_suppressed'},
    {'type': 'agent_error', 'message': 'Model unavailable'},
    {'type': 'agent_error', 'message': 'timeout'},
    {'type': 'error', 'message': 'poll_events failed: backend rejected the request'},
])
def test_completed_empty_or_error_event_does_not_require_recovery(completion):
    adapter = Adapter()
    ch = channel(adapter)

    def submit(turn):
        adapter.submitted.append(turn.text)
        event = completion if turn.text == 'first' else {'type': 'state_report', 'report': 'Fresh second'}
        adapter.events.append({'id': len(adapter.events) + 1, **event})

    adapter.submit_turn = submit

    async def exercise():
        first = await ch.send(request('first'))
        assert first.text == ''
        assert first.error == completion.get('message')
        assert (await ch.send(request('second'))).text == 'Fresh second'
        assert adapter.reset_count == 0

    asyncio.run(exercise())


def test_builtin_timeout_does_not_deliver_prior_worker_report_to_next_turn(config_module, monkeypatch):
    from korina.agents import gateway
    from korina.agents.korina_adapter import KorinaAgentAdapter
    from korina.services import agent_service
    config_module.update_config_values({'agent_enabled': 'on'})
    agent_service.shutdown_agent_worker()
    monkeypatch.setattr(gateway, '_active', KorinaAgentAdapter())
    ch = korina_channel.KorinaConverseChannel()
    started, release, drained = threading.Event(), threading.Event(), threading.Event()
    calls = []
    drain = agent_service._drain_transcript_jobs

    def track_drain(*args):
        try:
            return drain(*args)
        finally:
            drained.set()

    def report(req, **kwargs):
        text = req.transcript[-1]['content']
        calls.append(text)
        if text == 'first':
            started.set()
            assert release.wait(2)
        return 'Priority: normal\nReport for ' + text

    monkeypatch.setattr(agent_service, 'generate_agent_state_report', report)
    monkeypatch.setattr(agent_service, '_drain_transcript_jobs', track_drain)

    async def exercise():
        assert (await ch.send(request('first'))).error == 'timeout'
        assert started.is_set()
        assert 'recovery required' in (await ch.send(request('second'))).error
        assert agent_service._queued_prompt is None
        await ch.cancel()
        rejected = await ch.send(request('still draining'))
        assert 'submit rejected' in rejected.error and 'busy' in rejected.error
        assert calls == ['first']
        assert agent_service._queued_prompt is None
        release.set()
        assert await asyncio.to_thread(drained.wait, 2), 'the retired worker must finish before retry'
        assert agent_service._worker_running is False
        monkeypatch.setattr(korina_channel, '_MAX_POLL_SECONDS', 1)
        response = await ch.send(request('fresh'))
        assert response.text == 'Priority: normal\nReport for fresh'
        assert response.error is None
        assert calls == ['first', 'fresh']

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        agent_service.shutdown_agent_worker()
