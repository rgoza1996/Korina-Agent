"""Default channel completion must belong to the newly submitted turn."""
from __future__ import annotations

import asyncio
import threading

import pytest

from korina.agents import AgentAdapter, LegacyAgentHttpAdapter
from korina.converse import ConverseRequest
from korina.converse.korina_channel import KorinaConverseChannel
from korina.schemas import AgentCapabilities


class Adapter:
    id = 'fake'
    capabilities = AgentCapabilities(adapter_id='fake')

    def __init__(self):
        self.events = [{'id': 1, 'type': 'state_report', 'report': 'Old report'}]
        self.last_report = 'Old report'
        self.reset_count = 0
        self.submitted = []

    def poll_events(self, cursor):
        return (self.events[-1]['id'] if self.events else 0, [event for event in self.events if event['id'] > cursor])

    def submit_turn(self, turn):
        self.submitted.append(turn.text)
        self.last_report = 'Fresh: ' + turn.text
        self.events.append({'id': self.events[-1]['id'] + 1, 'type': 'state_report', 'report': self.last_report})

    def snapshot(self):
        return {'last_report': self.last_report, 'status': 'idle'}

    def reset(self):
        self.reset_count += 1

    def health_check(self):
        return {'ok': True, **self.snapshot()}

    def answer_permission(self, answer):
        pass


class PausedCall:
    """Hold a synchronous adapter call across a cancellation or replacement."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def return_after_release(self, result, *, fail=False):
        self.started.set()
        assert self.release.wait(2), 'test must release the adapter call'
        if fail:
            raise RuntimeError('late adapter failure')
        return result


async def cancel_paused_send(ch, task, paused):
    try:
        assert await asyncio.to_thread(paused.started.wait, 2)
        await ch.cancel()
    finally:
        paused.release.set()
    response = await asyncio.wait_for(task, timeout=2)
    assert response.error == 'cancelled'
    assert response.text == ''
    return response


def channel(adapter):
    result = KorinaConverseChannel()
    result._agent_gateway = lambda: adapter
    return result


def test_each_send_returns_fresh_report_from_adapter_events():
    ch = channel(Adapter())

    async def exercise():
        first = await ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'first'}]))
        second = await ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'second'}]))
        assert first.text == 'Fresh: first'
        assert second.text == 'Fresh: second'
        assert first.error is second.error is None

    asyncio.run(exercise())


def test_model_error_completes_wait_without_timeout():
    adapter = Adapter()
    adapter.submit_turn = lambda turn: adapter.events.append({'id': 2, 'type': 'agent_error', 'message': 'Model unavailable'})
    response = asyncio.run(channel(adapter).send(ConverseRequest(transcript=[{'role': 'user', 'text': 'hello'}])))
    assert response.error == 'Model unavailable'
    assert response.text == ''


def test_cancel_stops_wait_and_resets_adapter():
    adapter = Adapter()
    submitted = threading.Event()
    adapter.submit_turn = lambda turn: submitted.set()
    ch = channel(adapter)

    async def exercise():
        task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'hello'}])))
        assert await asyncio.to_thread(submitted.wait, 2)
        await ch.cancel()
        response = await asyncio.wait_for(task, timeout=2)
        assert response.error == 'cancelled'
        assert adapter.reset_count == 1

    asyncio.run(exercise())


def test_duplicate_suppression_can_complete_with_a_core_only_adapter():
    class CoreOnlyAdapter:
        id = 'core-only'
        capabilities = AgentCapabilities(adapter_id='core-only')

        def __init__(self):
            self.events = []
            self.report = ''
            self.submitted = []

        def submit_turn(self, turn):
            self.submitted.append(turn.text)
            self.report = 'Current report: ' + turn.text
            self.events.append({'id': 1, 'type': 'agent_status', 'status': 'duplicate_report_suppressed'})

        def poll_events(self, cursor):
            return len(self.events), [event for event in self.events if event['id'] > cursor]

        def answer_permission(self, answer):
            pass

        def reset(self):
            self.events.clear()

        def health_check(self):
            return {'ok': True, 'status': 'idle', 'last_report': self.report}

    adapter = CoreOnlyAdapter()
    assert isinstance(adapter, AgentAdapter)
    assert not isinstance(adapter, LegacyAgentHttpAdapter)
    response = asyncio.run(channel(adapter).send(ConverseRequest(
        transcript=[{'role': 'user', 'text': 'fresh turn'}],
    )))
    assert response.error is None
    assert response.text == 'Current report: fresh turn'
    assert response.metadata['next_cursor'] == 1
    assert adapter.submitted == ['fresh turn']


@pytest.mark.parametrize('fail', [False, True])
def test_cancel_during_report_poll_discards_late_result_or_error(fail):
    adapter = Adapter()
    paused = PausedCall()
    original_poll = adapter.poll_events

    def delayed_poll(cursor):
        result = original_poll(cursor)
        if adapter.submitted:
            return paused.return_after_release(result, fail=fail)
        return result

    adapter.poll_events = delayed_poll
    ch = channel(adapter)

    async def exercise():
        task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'hello'}])))
        await cancel_paused_send(ch, task, paused)
        assert adapter.reset_count == 1

    asyncio.run(exercise())


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('fail', [False, True])
def test_cancel_during_duplicate_snapshot_discards_late_result_or_error(legacy, fail):
    adapter = Adapter()
    paused = PausedCall()

    def delayed_snapshot():
        return paused.return_after_release({'ok': True, 'last_report': 'Late duplicate'}, fail=fail)

    def suppress_report(turn):
        adapter.events.append({'id': 2, 'type': 'agent_status', 'status': 'duplicate_report_suppressed'})

    adapter.submit_turn = suppress_report
    adapter.health_check = delayed_snapshot
    adapter.snapshot = delayed_snapshot
    if legacy:
        adapter.submit_transcript_legacy = lambda request: {}
        adapter.answer_permission_legacy = lambda request: {}
    assert isinstance(adapter, AgentAdapter)
    assert isinstance(adapter, LegacyAgentHttpAdapter) is legacy
    ch = channel(adapter)

    async def exercise():
        task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'hello'}])))
        await cancel_paused_send(ch, task, paused)

    asyncio.run(exercise())


def test_cancel_during_initial_poll_does_not_submit_a_turn_after_reset():
    adapter = Adapter()
    paused = PausedCall()
    original_poll = adapter.poll_events
    adapter.poll_events = lambda cursor: paused.return_after_release(original_poll(cursor))
    ch = channel(adapter)

    async def exercise():
        task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'hello'}])))
        await cancel_paused_send(ch, task, paused)
        assert adapter.submitted == []
        assert adapter.reset_count == 1

    asyncio.run(exercise())


def test_adapter_replacement_applies_to_the_next_send():
    first = Adapter()
    replacement = Adapter()
    active = [first]
    ch = channel(first)
    ch._agent_gateway = lambda: active[0]
    original_submit = first.submit_turn

    def submit_and_replace(turn):
        original_submit(turn)
        active[0] = replacement

    first.submit_turn = submit_and_replace
    original_poll = replacement.poll_events

    def wrong_adapter_polled(cursor):
        raise AssertionError('wrong adapter polled')

    replacement.poll_events = wrong_adapter_polled

    async def exercise():
        first_response = await ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'first'}]))
        assert first_response.error is None
        assert first_response.text == 'Fresh: first'
        assert replacement.submitted == []
        replacement.poll_events = original_poll
        second_response = await ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'second'}]))
        assert second_response.error is None
        assert second_response.text == 'Fresh: second'
        assert first.submitted == ['first']
        assert replacement.submitted == ['second']

    asyncio.run(exercise())


def test_cancel_after_adapter_replacement_resets_the_inflight_adapter():
    first = Adapter()
    replacement = Adapter()
    active = [first]
    paused = PausedCall()
    original_poll = first.poll_events

    def delayed_poll(cursor):
        result = original_poll(cursor)
        return paused.return_after_release(result) if first.submitted else result

    first.poll_events = delayed_poll
    ch = channel(first)
    ch._agent_gateway = lambda: active[0]

    async def exercise():
        task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'hello'}])))
        try:
            assert await asyncio.to_thread(paused.started.wait, 2)
            active[0] = replacement
            await ch.cancel()
        finally:
            paused.release.set()
        response = await asyncio.wait_for(task, timeout=2)
        assert response.error == 'cancelled'
        assert response.text == ''
        assert first.reset_count == 1
        assert replacement.reset_count == 0

    asyncio.run(exercise())


@pytest.mark.parametrize('cancel_reset_caller', [False, True])
def test_new_send_waits_until_synchronous_reset_has_finished(cancel_reset_caller):
    adapter = Adapter()
    paused = PausedCall()
    submitted = threading.Event()
    original_submit = adapter.submit_turn
    adapter.reset = lambda: paused.return_after_release(None)

    def tracked_submit(turn):
        submitted.set()
        original_submit(turn)

    adapter.submit_turn = tracked_submit
    ch = channel(adapter)

    async def exercise():
        cancel_task = asyncio.create_task(ch.cancel())
        send_task = None
        try:
            assert await asyncio.to_thread(paused.started.wait, 2)
            if cancel_reset_caller:
                cancel_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cancel_task
            send_task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'new session'}])))
            assert not await asyncio.to_thread(submitted.wait, 0.1)
        finally:
            paused.release.set()
        if not cancel_reset_caller:
            await asyncio.wait_for(cancel_task, timeout=2)
        response = await asyncio.wait_for(send_task, timeout=2)
        assert response.error is None
        assert response.text == 'Fresh: new session'
        assert adapter.submitted == ['new session']

    asyncio.run(exercise())


def test_cancel_resets_after_a_slow_submission_finishes():
    adapter = Adapter()
    paused = PausedCall()
    reset_started = threading.Event()
    operations = []

    def slow_submit(turn):
        paused.return_after_release(None)
        operations.append('submitted old turn')

    def reset():
        reset_started.set()
        operations.append('reset')

    adapter.submit_turn = slow_submit
    adapter.reset = reset
    ch = channel(adapter)

    async def exercise():
        task = asyncio.create_task(ch.send(ConverseRequest(transcript=[{'role': 'user', 'text': 'old turn'}])))
        assert await asyncio.to_thread(paused.started.wait, 2)
        cancelling = asyncio.create_task(ch.cancel())
        try:
            await asyncio.sleep(0)
            assert not await asyncio.to_thread(reset_started.wait, 0.05)
        finally:
            paused.release.set()
        await asyncio.wait_for(cancelling, timeout=2)
        response = await asyncio.wait_for(task, timeout=2)
        assert response.error == 'cancelled'
        assert response.text == ''
        assert operations == ['submitted old turn', 'reset']

    asyncio.run(exercise())
