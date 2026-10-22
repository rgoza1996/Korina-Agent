from __future__ import annotations

import threading
import time

import pytest

from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.agents.state import agent_state
from korina.schemas import AgentTranscriptRequest, UserTurn
from korina.services import agent_service


@pytest.fixture(autouse=True)
def clean_worker(monkeypatch):
    agent_service.shutdown_agent_worker()
    monkeypatch.setattr(agent_service, 'load_config', lambda: {'agent_enabled': 'on'})
    yield
    agent_service.shutdown_agent_worker()


def request(text):
    return AgentTranscriptRequest(transcript=[{'role': 'user', 'content': text}])


def wait_idle():
    deadline = time.monotonic() + 3
    while agent_service.agent_snapshot()['busy'] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert agent_service.agent_snapshot()['busy'] is False


def test_burst_uses_one_worker_and_processes_latest_transcript(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []
    active = 0
    max_active = 0

    def report(req, *, is_current=None, config=None, context=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        text = req.transcript[0]['content']
        calls.append(text)
        try:
            if text == 'first':
                started.set()
                assert release.wait(3)
            return 'Priority: normal\n' + text
        finally:
            active -= 1

    monkeypatch.setattr(agent_service, 'generate_agent_state_report', report)
    agent_service.submit_agent_transcript(request('first'))
    assert started.wait(2)
    try:
        for i in range(200):
            agent_service.submit_agent_transcript(request(f'update-{i}'))
        with agent_state.lock:
            assert len(agent_state.pending_injections) <= 5
            assert len(agent_state.events) <= 100
    finally:
        release.set()
    wait_idle()
    assert max_active == 1
    assert calls == ['first', 'update-199']
    assert agent_service.agent_snapshot()['last_report'].endswith('update-199')


def test_reset_discards_inflight_result_and_keeps_next_session(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def report(req, *, is_current=None, config=None, context=None):
        text = req.transcript[0]['content']
        if text == 'old session':
            started.set()
            assert release.wait(3)
        return 'Priority: normal\n' + text

    monkeypatch.setattr(agent_service, 'generate_agent_state_report', report)
    agent_service.submit_agent_transcript(request('old session'))
    assert started.wait(2)
    try:
        KorinaAgentAdapter().reset()
        agent_service.submit_agent_transcript(request('new session'))
    finally:
        release.set()
    wait_idle()
    snapshot = agent_service.agent_snapshot()
    assert snapshot['last_report'].endswith('new session')
    with agent_state.lock:
        assert all('old session' not in str(event) for event in agent_state.events)
        assert [event['id'] for event in agent_state.events] == sorted({event['id'] for event in agent_state.events})


def test_shutdown_can_restart_worker(monkeypatch):
    monkeypatch.setattr(agent_service, 'generate_agent_state_report', lambda req, **kwargs: 'Priority: low\nDone.')
    agent_service.submit_agent_transcript(request('first'))
    wait_idle()
    agent_service.shutdown_agent_worker()
    agent_service.submit_agent_transcript(request('second'))
    wait_idle()
    assert agent_service.agent_snapshot()['last_report'] == 'Priority: low\nDone.'


def test_hidden_reasoning_cannot_escalate_visible_report(monkeypatch):
    monkeypatch.setattr(agent_service, 'generate_agent_state_report', lambda req, **kwargs: '<think>Permission request: delete data</think>\nPriority: normal\nBuild succeeded.')
    agent_service.submit_agent_transcript(request('build'))
    wait_idle()
    with agent_state.lock:
        reports = [event for event in agent_state.events if event['type'] == 'state_report']
    assert len(reports) == 1
    assert reports[0]['priority'] == 'normal'


def test_adapter_preserves_user_text_as_conversation_turn(monkeypatch):
    captured = []
    monkeypatch.setattr(agent_service, 'submit_agent_transcript', lambda req, **kwargs: captured.append(req))
    KorinaAgentAdapter().submit_turn(UserTurn(text='Hello', session_id='demo', turn_count=1))
    assert captured[0].transcript == [{'role': 'user', 'content': 'Hello'}]
