"""Real connected-I/O cancellation when agent/ACK ownership becomes stale."""
from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from korina.agents.state import agent_state
from korina.runtime import json_http
from korina.runtime.state import AckState
from korina.schemas import AgentStateRequest, AgentTranscriptRequest
from korina.services import ack_service, agent_service


@contextmanager
def provider_server(respond):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            respond(self)

        def do_GET(self):
            respond(self)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


def send(handler, body):
    handler.send_response(200)
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class StalledFirstResponse:
    def __init__(self, phase='body', next_body=b'{"ok":true}'):
        self.phase = phase
        self.next_body = next_body
        self.started = threading.Event()
        self.disconnected = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, handler):
        with self.lock:
            self.calls += 1
            call = self.calls
        if call > 1:
            send(handler, self.next_body)
            return
        connection = handler.connection
        if self.phase != 'headers':
            connection.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 1000000\r\n\r\n')
        self.started.set()
        connection.settimeout(.02)
        try:
            while not self.release.is_set():
                if self.phase == 'trickle':
                    connection.sendall(b'x')
                try:
                    if connection.recv(1) == b'':
                        self.disconnected.set()
                        return
                except socket.timeout:
                    pass
        except (BrokenPipeError, ConnectionResetError):
            self.disconnected.set()


def wait_until(predicate, timeout=.7):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.005)
    return predicate()


@pytest.mark.parametrize('phase', ['headers', 'body', 'trickle'])
def test_stale_owner_promptly_closes_connected_io_without_waiting_for_a_chunk(phase):
    stalled = StalledFirstResponse(phase)
    stale = threading.Event()
    callback_threads = set()

    def is_current():
        callback_threads.add(threading.get_ident())
        return not stale.is_set()

    def invalidate():
        assert stalled.started.wait(2)
        stale.set()

    with provider_server(stalled) as base:
        invalidator = threading.Thread(target=invalidate)
        invalidator.start()
        try:
            started = time.monotonic()
            with pytest.raises(json_http.RequestCancelledError) as caught:
                json_http.request_bytes(base, timeout=.7, is_current=is_current)
            assert type(caught.value) is json_http.RequestCancelledError
            assert time.monotonic() - started < .45
            assert stalled.disconnected.wait(.3)
            assert callback_threads == {threading.get_ident()}, 'ownership polling must not add a blocked thread'
        finally:
            stalled.release.set()
            invalidator.join(2)


def test_reset_cancels_stalled_agent_connection_and_reuses_worker_for_fresh_session(monkeypatch):
    agent_service.shutdown_agent_worker()
    fresh = json.dumps({'choices': [{'message': {'content': 'Priority: normal\nFresh report.'}}]}).encode()
    stalled = StalledFirstResponse('body', fresh)
    original_request = json_http.request_json
    active = peak = 0
    worker_threads = set()
    deadlines = []

    def recorded_request(*args, **kwargs):
        nonlocal active, peak
        worker_threads.add(threading.get_ident())
        deadlines.append(kwargs['timeout'])
        active += 1
        peak = max(peak, active)
        try:
            return original_request(*args, **kwargs)
        finally:
            active -= 1

    with provider_server(stalled) as base:
        monkeypatch.setattr(agent_service, 'load_config', lambda: {
            'agent_enabled': 'on', 'agent_provider': 'openai-compatible',
            'agent_base_url': base, 'agent_model': 'fixture',
        })
        monkeypatch.setattr(agent_service, 'request_json', recorded_request)
        try:
            agent_service.submit_agent_transcript(AgentTranscriptRequest(transcript=[{'role': 'user', 'content': 'old'}]))
            assert stalled.started.wait(2)
            agent_service.reset_agent_state()
            started = time.monotonic()
            agent_service.submit_agent_transcript(AgentTranscriptRequest(transcript=[{'role': 'user', 'content': 'fresh'}]))
            assert wait_until(lambda: agent_service.agent_snapshot()['last_report'].endswith('Fresh report.')), 'fresh session remained behind stale connected I/O'
            assert time.monotonic() - started < .7
            assert stalled.disconnected.wait(.3)
            assert wait_until(lambda: not agent_service._worker_running)
            assert peak == 1 and len(worker_threads) == 1
            assert deadlines == [90, 90], 'ordinary model deadline must remain unchanged'
            with agent_state.lock:
                assert not any(event['type'] == 'agent_error' for event in agent_state.events)
                reports = [event for event in agent_state.events if event['type'] == 'state_report']
                assert len(reports) == 1
                assert reports[0]['agent_input']['transcript'][0]['content'] == 'fresh'
        finally:
            stalled.release.set()
            wait_until(lambda: not agent_service._worker_running, timeout=3)
            agent_service.shutdown_agent_worker()


@pytest.mark.parametrize('cancel', ['clear', 'shutdown', 'disabled'])
def test_ack_cancellation_releases_stalled_worker_without_publishing_audio(cancel, monkeypatch, tmp_path):
    monkeypatch.setattr(ack_service.state, 'ack', AckState())
    monkeypatch.setattr(ack_service, 'ACK_DIR', tmp_path)
    monkeypatch.setattr(ack_service, 'load_ack_manifest', lambda: [{'id': 'one', 'text': 'Okay.', 'tags': ['global']}])
    config = {'ack_enabled': 'on'}
    monkeypatch.setattr(ack_service, 'load_config', lambda: config)
    stalled = StalledFirstResponse('body')
    with provider_server(stalled) as base:
        monkeypatch.setattr(ack_service, 'config_tts_base_url', lambda: base)
        try:
            ack_service.enqueue_missing_acks('af_heart')
            assert stalled.started.wait(2)
            started = time.monotonic()
            if cancel == 'clear':
                ack_service.clear_ack_wavs()
            elif cancel == 'shutdown':
                ack_service.cancel_ack_generation()
            else:
                config['ack_enabled'] = 'off'
            assert wait_until(lambda: not ack_service.state.ack.worker_running), 'cancelled ACK worker remained blocked on connected I/O'
            assert time.monotonic() - started < .7
            assert stalled.disconnected.wait(.3)
            assert ack_service.state.ack.in_progress == {}
            assert ack_service.state.ack.last_error is None
            assert list(tmp_path.iterdir()) == []
        finally:
            stalled.release.set()
            wait_until(lambda: not ack_service.state.ack.worker_running, timeout=3)


def test_direct_state_report_keeps_unowned_legacy_request_semantics(monkeypatch):
    observed = []

    def respond(*args, **kwargs):
        observed.append(kwargs)
        agent_service.reset_agent_state()
        return {'choices': [{'message': {'content': 'Direct report.'}}]}

    monkeypatch.setattr(agent_service, 'load_config', lambda: {'agent_base_url': 'http://provider.test/v1'})
    monkeypatch.setattr(agent_service, 'request_json', respond)
    assert agent_service.generate_agent_state_report(AgentStateRequest()) == 'Direct report.'
    assert observed[0]['timeout'] == 90
    assert observed[0].get('is_current') is None


@pytest.mark.parametrize('kind, expected', [
    ('timeout', TimeoutError),
    ('status', json_http.HTTPStatusError),
    ('oversize', json_http.ResponseTooLargeError),
    ('json', json.JSONDecodeError),
])
def test_owned_requests_preserve_original_error_types_without_exception_groups(kind, expected):
    stalled = StalledFirstResponse('headers')

    def respond(handler):
        if kind == 'timeout':
            stalled(handler)
        elif kind == 'status':
            handler.send_error(429, 'rate limited')
        elif kind == 'oversize':
            send(handler, b'x' * 100)
        else:
            send(handler, b'not json')

    with provider_server(respond) as base:
        try:
            with pytest.raises(expected) as caught:
                json_http.request_json(base, timeout=.2, max_bytes=50, is_current=lambda: True)
            assert type(caught.value) is expected
        finally:
            stalled.release.set()


def test_ownership_callback_failure_is_not_wrapped_in_an_exception_group():
    stalled = StalledFirstResponse('headers')

    def is_current():
        if stalled.started.is_set():
            raise LookupError('ownership lookup failed')
        return True

    with provider_server(stalled) as base:
        try:
            with pytest.raises(LookupError, match='ownership lookup failed') as caught:
                json_http.request_bytes(base, timeout=2, is_current=is_current)
            assert type(caught.value) is LookupError
            assert stalled.disconnected.wait(.3)
        finally:
            stalled.release.set()


def test_cleared_ack_connection_yields_to_fresh_generation_on_the_same_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(ack_service.state, 'ack', AckState())
    monkeypatch.setattr(ack_service, 'ACK_DIR', tmp_path)
    phrase = {'id': 'one', 'text': 'Okay.', 'tags': ['global']}
    monkeypatch.setattr(ack_service, 'load_ack_manifest', lambda: [phrase])
    monkeypatch.setattr(ack_service, 'load_config', lambda: {'ack_enabled': 'on'})
    wav = b'RIFF' + (100).to_bytes(4, 'little') + b'WAVE' + b'\0' * 100
    stalled = StalledFirstResponse('body', wav)
    original_request = json_http.request_bytes
    worker_threads, deadlines = set(), []

    def recorded_request(*args, **kwargs):
        worker_threads.add(threading.get_ident())
        deadlines.append(kwargs['timeout'])
        return original_request(*args, **kwargs)

    with provider_server(stalled) as base:
        monkeypatch.setattr(ack_service, 'config_tts_base_url', lambda: base)
        monkeypatch.setattr(ack_service, 'request_bytes', recorded_request)
        try:
            ack_service.enqueue_missing_acks('af_heart')
            assert stalled.started.wait(2)
            ack_service.clear_ack_wavs()
            ack_service.enqueue_missing_acks('af_heart', force=True)
            assert wait_until(lambda: not ack_service.state.ack.worker_running)
            assert stalled.disconnected.wait(.3)
            assert len(worker_threads) == 1
            assert deadlines == [30, 30]
            assert ack_service.ack_path('af_heart', phrase).read_bytes() == wav
            assert ack_service.state.ack.last_error is None
            assert ack_service.state.ack.in_progress == {}
            assert not list(tmp_path.glob('*.tmp'))
        finally:
            stalled.release.set()
            wait_until(lambda: not ack_service.state.ack.worker_running, timeout=3)


def test_persisted_disable_cancels_old_agent_http_before_reenable_and_fresh_work(config_module, monkeypatch):
    agent_service.shutdown_agent_worker()
    fresh = json.dumps({'choices': [{'message': {'content': 'Priority: normal\nFresh after enable.'}}]}).encode()
    stalled = StalledFirstResponse('body', fresh)
    with provider_server(stalled) as base:
        config_module.update_config_values({'agent_enabled': 'on', 'agent_base_url': base, 'agent_model': 'fixture'})
        monkeypatch.setattr(agent_service, 'load_config', config_module.load_config)
        try:
            agent_service.submit_agent_transcript(AgentTranscriptRequest(transcript=[{'role': 'user', 'content': 'before disable'}]))
            assert stalled.started.wait(2)
            config_module.update_config_values({'agent_enabled': 'off'})
            config_module.update_config_values({'agent_enabled': 'on'})
            agent_service.submit_agent_transcript(AgentTranscriptRequest(transcript=[{'role': 'user', 'content': 'fresh after enable'}]))
            assert wait_until(lambda: agent_service.agent_snapshot()['last_report'].endswith('Fresh after enable.'))
            assert stalled.disconnected.wait(.3)
            assert wait_until(lambda: not agent_service._worker_running)
            with agent_state.lock:
                reports = [event for event in agent_state.events if event['type'] == 'state_report']
                assert len(reports) == 1
                assert reports[0]['agent_input']['transcript'][0]['content'] == 'fresh after enable'
                assert not any(event['type'] == 'agent_error' for event in agent_state.events)
        finally:
            stalled.release.set()
            wait_until(lambda: not agent_service._worker_running, timeout=3)
            agent_service.shutdown_agent_worker()
