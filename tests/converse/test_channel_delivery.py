"""Default channel completion must belong to the newly submitted turn."""
from __future__ import annotations

import asyncio
import threading

from korina.converse import ConverseRequest
from korina.converse.korina_channel import KorinaConverseChannel


class Adapter:
    def __init__(self):
        self.events = [{'id': 1, 'type': 'state_report', 'report': 'Old report'}]
        self.last_report = 'Old report'
        self.reset_count = 0

    def poll_events(self, cursor):
        return (self.events[-1]['id'] if self.events else 0, [event for event in self.events if event['id'] > cursor])

    def submit_turn(self, turn):
        self.last_report = 'Fresh: ' + turn.text
        self.events.append({'id': self.events[-1]['id'] + 1, 'type': 'state_report', 'report': self.last_report})

    def snapshot(self):
        return {'last_report': self.last_report, 'status': 'idle'}

    def reset(self):
        self.reset_count += 1


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
