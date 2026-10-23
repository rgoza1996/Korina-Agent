"""Failed thread admission cannot strand busy state or publish an answer."""
from __future__ import annotations

import threading
import time

import pytest

from korina.agents.state import agent_state
from korina.schemas import AgentTranscriptRequest
from korina.services import agent_service as service
from korina.services.agent_context import prepare_queued_injection


@pytest.fixture(autouse=True)
def clean_worker(monkeypatch):
    service.shutdown_agent_worker()
    monkeypatch.setattr(service, 'load_config', lambda: {'agent_enabled': 'on'})
    yield
    service.shutdown_agent_worker()


def request(text):
    return AgentTranscriptRequest(transcript=[{'role': 'user', 'content': text}])


def wait_idle():
    deadline = time.monotonic() + 2
    while service._worker_running and time.monotonic() < deadline:
        time.sleep(.01)
    assert not service._worker_running


@pytest.mark.parametrize('permission', [False, True])
@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_submit_failure_rolls_back_only_admission_and_orphan_cannot_take_new_worker(monkeypatch, permission, cleanup_fails):
    class FailedExecutor:
        def __init__(self):
            self.queued = None
            self.shutdown_args = None

        def submit(self, function, *args):
            self.queued = (function, args)
            raise RuntimeError('cannot start new thread')

        def shutdown(self, **kwargs):
            self.shutdown_args = kwargs
            if cleanup_fails:
                raise OSError('cleanup failed')

    failed = FailedExecutor()
    monkeypatch.setattr(service, '_executor', failed)
    with agent_state.lock:
        agent_state.last_report = 'Retained report'
        agent_state.last_error = 'Retained diagnostic'
        agent_state.status = 'idle'
        agent_state.pending_injections.append(prepare_queued_injection(
            AgentTranscriptRequest(transcript=[], reason='retained'), 0,
        ))
        epoch = service._worker_epoch
    before = service.agent_snapshot()
    with pytest.raises(RuntimeError, match='cannot start new thread'):
        if permission:
            service.submit_agent_permission(request('retired'), {'type': 'permission_answer', 'answer': 'yes'})
        else:
            service.submit_agent_transcript(request('retired'))
    assert service.agent_snapshot() == before
    assert service._executor is None
    assert service._worker_running is False
    assert service._queued_prompt is None
    assert service._worker_epoch == epoch + 1
    assert failed.shutdown_args == {'wait': False, 'cancel_futures': True}
    with agent_state.lock:
        assert agent_state.events == []
        assert len(agent_state.pending_injections) == 1

    started, release = threading.Event(), threading.Event()
    calls = []

    def report(req, **kwargs):
        calls.append(req.transcript[0]['content'])
        started.set()
        assert release.wait(2)
        return 'Priority: normal\nFresh report'

    monkeypatch.setattr(service, 'generate_agent_state_report', report)
    assert service.submit_agent_transcript(request('fresh'))['accepted']
    assert started.wait(1)
    try:
        # Even an executor that queued/started work before raising cannot let
        # its retired job process context or drain the replacement's queue.
        function, args = failed.queued
        function(*args)
        assert service._worker_running
        assert calls == ['fresh']
    finally:
        release.set()
    wait_idle()
    assert service.agent_snapshot()['last_report'] == 'Priority: normal\nFresh report'


def test_real_thread_start_failure_is_retryable_without_publishing_permission(monkeypatch):
    original_start = threading.Thread.start
    attempts = 0

    def start(thread):
        nonlocal attempts
        if thread.name.startswith('korina-agent') and attempts == 0:
            attempts += 1
            raise RuntimeError('simulated OS thread exhaustion')
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, 'start', start)
    with pytest.raises(RuntimeError, match='simulated OS thread exhaustion'):
        service.submit_agent_permission(request('retired'), {'type': 'permission_answer', 'answer': 'yes'})
    assert not service._worker_running
    assert service._executor is None
    assert service.agent_snapshot()['busy'] is False
    assert agent_state.events == []
    monkeypatch.setattr(service, 'generate_agent_state_report', lambda req, **kwargs: 'Priority: normal\nFresh retry')
    assert service.submit_agent_transcript(request('fresh'))['accepted']
    wait_idle()
    assert service.agent_snapshot()['last_report'] == 'Priority: normal\nFresh retry'


def test_executor_constructor_failure_leaves_admission_unchanged(monkeypatch):
    before = service.agent_snapshot()

    def failed_constructor(**kwargs):
        raise RuntimeError('executor unavailable')

    monkeypatch.setattr(service, 'ThreadPoolExecutor', failed_constructor)
    with pytest.raises(RuntimeError, match='executor unavailable'):
        service.submit_agent_permission(request('retired'), {'type': 'permission_answer', 'answer': 'yes'})
    assert service.agent_snapshot() == before
    assert not service._worker_running
    assert service._executor is None
    assert agent_state.events == []
