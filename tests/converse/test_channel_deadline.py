"""A bounded channel wait must cover the built-in report's HTTP allowance."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.converse import ConverseRequest
from korina.converse import korina_channel
from korina.schemas import AgentCapabilities
from korina.services import agent_service as service


def turn(text):
    return ConverseRequest(transcript=[{'role': 'user', 'text': text}])


@pytest.fixture
def builtin(monkeypatch, config_module):
    service.shutdown_agent_worker()
    config_module.update_config_values({
        'agent_enabled': 'on', 'ack_enabled': 'off', 'agent_provider': 'openai-compatible',
        'agent_base_url': 'https://fixture.invalid/v1', 'agent_model': 'fixture',
        'agent_api_key': '', 'llm_provider': 'openai-compatible', 'llm_api_key_env': '',
    })
    adapter = KorinaAgentAdapter()
    channel = korina_channel.KorinaConverseChannel()
    channel._agent_gateway = lambda: adapter
    controls = SimpleNamespace(now=0.0, hook=lambda: None, polls=0, calls=[])
    controls.entered, controls.release = threading.Event(), threading.Event()
    controls.completed, controls.drained, admitted = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(korina_channel, 'time', SimpleNamespace(monotonic=lambda: controls.now))
    monkeypatch.setattr(korina_channel, '_POLL_INTERVAL', .001)
    ordinary_submit, ordinary_poll = adapter.submit_turn, adapter.poll_events
    ordinary_push, ordinary_drain = service.push_agent_event, service._drain_transcript_jobs

    def submit(request):
        ordinary_submit(request)
        admitted.set()

    def poll(cursor):
        if admitted.is_set():
            controls.polls += 1
            controls.hook()
        return ordinary_poll(cursor)

    def push(event, **kwargs):
        result = ordinary_push(event, **kwargs)
        if result and event.get('type') in {'state_report', 'agent_error'}:
            controls.completed.set()
        return result

    def drain(*args):
        try:
            return ordinary_drain(*args)
        finally:
            controls.drained.set()

    monkeypatch.setattr(adapter, 'submit_turn', submit)
    monkeypatch.setattr(adapter, 'poll_events', poll)
    monkeypatch.setattr(service, 'push_agent_event', push)
    monkeypatch.setattr(service, '_drain_transcript_jobs', drain)
    try:
        yield channel, controls
    finally:
        controls.release.set()
        executor = service._executor
        service.shutdown_agent_worker()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


def provider_fixture(monkeypatch, controls, *, fail_first=False):
    def request(_url, **kwargs):
        assert kwargs['timeout'] == 90, 'the provider keeps its existing HTTP allowance'
        context = json.loads(kwargs['json_body']['messages'][-1]['content'])
        text = context['recent_transcript'][-1]['content']
        controls.calls.append(text)
        if len(controls.calls) == 1:
            controls.entered.set()
            assert controls.release.wait(3), 'test must release fixture provider'
            if fail_first:
                raise TimeoutError('fixture provider timeout')
        return {'choices': [{'message': {'content': 'Priority: normal\nReport for ' + text}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr(service, 'request_json', request)


@pytest.mark.parametrize('outcome,elapsed', [('report', 35.0), ('http-timeout', 90.0)])
def test_slow_builtin_completion_within_transport_allowance_is_not_a_channel_timeout(
    builtin, monkeypatch, outcome, elapsed,
):
    channel, controls = builtin
    provider_fixture(monkeypatch, controls, fail_first=outcome == 'http-timeout')

    def on_poll():
        if controls.polls == 1:
            assert controls.entered.wait(2)
            controls.now = elapsed
        elif controls.polls == 2:
            controls.release.set()
            assert controls.completed.wait(2)

    controls.hook = on_poll

    async def exercise():
        response = await asyncio.wait_for(channel.send(turn('slow turn')), 2)
        if outcome == 'report':
            assert response.error is None
            assert response.text == 'Priority: normal\nReport for slow turn'
        else:
            assert 'fixture provider timeout' in response.error
            assert response.text == ''
        assert channel._recovery_adapter is None, 'a known terminal completion needs no reset'
        assert await asyncio.to_thread(controls.drained.wait, 2)
        fresh = await asyncio.wait_for(channel.send(turn('fresh turn')), 2)
        assert fresh.error is None
        assert fresh.text == 'Priority: normal\nReport for fresh turn'
        assert controls.calls == ['slow turn', 'fresh turn']

    asyncio.run(exercise())


def test_explicit_cancel_still_discards_a_late_report_inside_extended_wait(builtin, monkeypatch):
    channel, controls = builtin
    provider_fixture(monkeypatch, controls)
    waiting = threading.Event()

    def on_poll():
        if controls.polls == 1:
            assert controls.entered.wait(2)
            controls.now = 35.0
        elif controls.polls == 2:
            waiting.set()

    controls.hook = on_poll

    async def exercise():
        pending = asyncio.create_task(channel.send(turn('retired turn')))
        assert await asyncio.to_thread(waiting.wait, 2)
        await channel.cancel()
        cancelled = await asyncio.wait_for(pending, 2)
        assert cancelled.error == 'cancelled' and cancelled.text == ''
        controls.release.set()
        assert await asyncio.to_thread(controls.drained.wait, 2)
        fresh = await asyncio.wait_for(channel.send(turn('fresh turn')), 2)
        assert fresh.error is None
        assert fresh.text == 'Priority: normal\nReport for fresh turn'
        assert not any(event.get('report', '').endswith('retired turn') for event in service.agent_state.events)

    asyncio.run(exercise())


def test_custom_adapter_wait_remains_finite_and_exhaustion_requires_explicit_recovery(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(korina_channel, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(korina_channel, '_POLL_INTERVAL', .001)

    class Adapter:
        id = 'fixture'
        capabilities = AgentCapabilities(adapter_id=id)

        def __init__(self):
            self.submitted = False
            self.emit = False
            self.resets = 0
            self.events = []

        def submit_turn(self, request):
            self.submitted = True
            if self.emit:
                self.events.append({'id': len(self.events) + 1, 'type': 'state_report', 'report': 'Fresh report'})

        def poll_events(self, cursor):
            if self.submitted and not self.emit:
                clock.now = 100.0
            return len(self.events), [event for event in self.events if event['id'] > cursor]

        def answer_permission(self, answer):
            pass

        def reset(self):
            self.resets += 1
            self.submitted = False
            self.events.clear()

        def health_check(self):
            return {'ok': True}

    adapter = Adapter()
    channel = korina_channel.KorinaConverseChannel()
    channel._agent_gateway = lambda: adapter

    async def exercise():
        response = await asyncio.wait_for(channel.send(turn('no report')), 2)
        assert response.error == 'timeout'
        blocked = await asyncio.wait_for(channel.send(turn('blocked')), 2)
        assert 'recovery required' in blocked.error
        assert adapter.resets == 0
        await channel.cancel()
        adapter.emit = True
        response = await asyncio.wait_for(channel.send(turn('fresh')), 2)
        assert response.error is None and response.text == 'Fresh report'
        assert adapter.resets == 1

    asyncio.run(exercise())


def test_deadline_policy_does_not_eagerly_import_agent_service_or_audio(tmp_path):
    source = '''
import importlib.abc
import sys
class NoEngines(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'korina.services.agent_service' or fullname.split('.')[0] in {'numpy', 'soundfile', 'torch', 'faster_whisper'}:
            raise AssertionError('Converse eagerly imported ' + fullname)
sys.meta_path.insert(0, NoEngines())
import korina.converse
assert 'korina.services.agent_service' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', source], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2])},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
