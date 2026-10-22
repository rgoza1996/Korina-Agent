"""The standalone read-only gate rejects malformed/deceptive readiness."""
from __future__ import annotations

import copy
import http.client
import importlib.util
import io
import json
import socketserver
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'check_stack.py'
spec = importlib.util.spec_from_file_location('korina_stack_smoke', SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

PROVIDER = {'label': 'Fixture', 'editable_base_url': True, 'manageable': False, 'is_local': False}
CAPABILITIES = {'providers': {'fixture': PROVIDER}, 'agent_providers': {'fixture': PROVIDER}}
CHECKS = {'frontend': True, 'response_llm': True, 'stt': True, 'tts': True, 'agent': True}


class Reply(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers, self.read_sizes = status, headers or {}, []

    def read(self, size=-1):
        assert size > 0, 'HTTP smoke must never read an unbounded body'
        self.read_sizes.append(size)
        return super().read(size)


@pytest.fixture
def stack(monkeypatch):
    bodies = {
        '/': (200, b'<html>Korina</html>'), '/styles.css': (200, b'body {}'),
        '/js/app.js': (200, b'export {};'), '/api/capabilities': (200, copy.deepcopy(CAPABILITIES)),
        '/api/health': (200, {'ok': True}), '/api/ready': (200, {'ready': True, 'checks': CHECKS.copy()}),
    }
    replies, urls = [], []

    def open_url(url, timeout):
        assert timeout == 15
        urls.append(url)
        status, body = bodies[urlsplit(url).path]
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        response = Reply(raw, status)
        replies.append(response)
        if status >= 400:
            raise urllib.error.HTTPError(url, status, 'fixture', {}, response)
        return response

    monkeypatch.setattr(smoke, '_open_url', open_url)
    return bodies, replies, urls


def run_gate(**options):
    return smoke.check_stack('http://fixture.test', **options)


def test_valid_older_optional_fields_and_read_only_requests_remain_supported(stack):
    assert run_gate()['ok'] is True
    assert all(reply.closed for reply in stack[1])
    assert all(0 < size <= smoke.READ_CHUNK_BYTES for reply in stack[1] for size in reply.read_sizes)
    assert [urlsplit(url).path for url in stack[2]] == list(stack[0])


@pytest.mark.parametrize('capabilities', [None, [], {}, {'providers': {}, 'agent_providers': {}},
    {'providers': {'x': None}, 'agent_providers': {'x': PROVIDER}},
    {'providers': {'x': {'label': 'Wrong'}}, 'agent_providers': {'x': PROVIDER}},
])
def test_invalid_capability_registry_cannot_pass_even_degraded(stack, capabilities):
    stack[0]['/api/capabilities'] = (200, capabilities)
    assert run_gate(allow_degraded=True)['ok'] is False


@pytest.mark.parametrize('ready,status', [
    ({'ready': True}, 200), ({'ready': False}, 503), ({'ready': False, 'checks': {}}, 503),
    ({'ready': False, 'checks': {**CHECKS, 'tts': 'false'}}, 503),
    ({'ready': True, 'checks': {**CHECKS, 'tts': False}}, 200),
    ({'ready': False, 'checks': CHECKS}, 503),
    ({'ready': True, 'checks': CHECKS}, 503),
    ({'ready': False, 'checks': {**CHECKS, 'tts': False}}, 200),
])
def test_incomplete_or_inconsistent_readiness_never_passes(stack, ready, status):
    stack[0]['/api/ready'] = (status, ready)
    assert run_gate(allow_degraded=True)['ok'] is False


@pytest.mark.parametrize('failure', ['frontend', 'audio_limits', 'future_infrastructure'])
def test_degraded_mode_cannot_hide_infrastructure_failure(stack, failure):
    stack[0]['/api/ready'] = (503, {'ready': False, 'checks': {**CHECKS, failure: False}})
    assert run_gate(allow_degraded=True)['ok'] is False


@pytest.mark.parametrize('failure', ['response_llm', 'stt', 'tts', 'agent'])
def test_degraded_mode_only_relaxes_known_model_dependencies(stack, failure):
    stack[0]['/api/ready'] = (503, {'ready': False, 'checks': {**CHECKS, failure: False}})
    assert run_gate()['ok'] is False
    assert run_gate(allow_degraded=True)['ok'] is True


@pytest.mark.parametrize('health', [
    {'ok': True, 'audio_limits_error': 'invalid environment'},
    {'ok': True, 'audio_limits': None},
    {'ok': True, 'audio_limits': {'max_duration_seconds': 0, 'max_upload_bytes': 500}},
    {'ok': True, 'audio_limits': {'max_duration_seconds': 10 ** 500, 'max_upload_bytes': 500}},
    {'ok': True, 'readiness': {'ready': False, 'checks': {**CHECKS, 'frontend': False}}},
    {'ok': True, 'readiness': {'ready': False, 'checks': {**CHECKS, 'audio_limits': False}}},
])
def test_health_diagnostics_cannot_be_hidden_by_later_ready_response(stack, health):
    stack[0]['/api/health'] = (200, health)
    assert run_gate(allow_degraded=True)['ok'] is False


@pytest.mark.parametrize('status', [200, 503])
def test_success_and_http_error_reads_stop_at_limit_and_close(stack, monkeypatch, status):
    monkeypatch.setattr(smoke, 'MAX_RESPONSE_BYTES', 64)
    stack[0]['/api/health'] = (status, b'x' * 1024)
    result = run_gate(allow_degraded=True)
    health = next(check for check in result['checks'] if check['path'] == '/api/health')
    assert health['ok'] is False and 'exceeds 64 bytes' in health['error']
    assert stack[1][4].read_sizes == [65]
    assert stack[1][4].closed


def test_declared_oversize_is_rejected_before_reading():
    reply = Reply(b'', headers={'Content-Length': str(smoke.MAX_RESPONSE_BYTES + 1)})
    with pytest.raises(ValueError, match='exceeds'):
        smoke._read_bounded(reply)
    assert reply.read_sizes == []


def test_truncated_response_cannot_pass_as_complete_json():
    reply = Reply(b'{"ok":true}', headers={'Content-Length': '100'})
    with pytest.raises(ValueError, match='declared Content-Length'):
        smoke._read_bounded(reply)


def test_malformed_http_is_reported_instead_of_crashing(monkeypatch):
    def broken_response(*args, **kwargs):
        raise http.client.BadStatusLine('not an HTTP response')

    monkeypatch.setattr(smoke, '_open_url', broken_response)
    result = run_gate()
    assert result['ok'] is False
    assert len(result['checks']) == 6
    assert all('not an HTTP response' in check['error'] for check in result['checks'])


@pytest.mark.parametrize('raw', [b'{"ok":NaN}', b'{"ok":Infinity}', b'{"ok":1e999}', b'[' * 10000 + b'0' + b']' * 10000])
def test_invalid_json_is_reported_without_crashing_gate(stack, raw):
    stack[0]['/api/health'] = (200, raw)
    assert run_gate(allow_degraded=True)['ok'] is False


def test_current_backend_schema_passes_degraded_smoke(client, monkeypatch):
    def open_url(url, timeout):
        response = client.get(urlsplit(url).path)
        reply = Reply(response.content, response.status_code, dict(response.headers))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(url, response.status_code, 'backend', reply.headers, reply)
        return reply

    monkeypatch.setattr(smoke, '_open_url', open_url)
    assert run_gate(allow_degraded=True)['ok'] is True


def test_redirect_body_is_bounded_without_following_target(monkeypatch):
    monkeypatch.setattr(smoke, 'MAX_RESPONSE_BYTES', 64)
    paths = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request = self.request.recv(4096)
            paths.append(request.split(b' ')[1])
            self.request.sendall(b'HTTP/1.1 302 Redirect\r\nLocation: /target\r\nContent-Length: 1000000\r\n\r\n')

    with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        try:
            result = smoke.check_stack(f'http://127.0.0.1:{server.server_address[1]}')
        finally:
            server.shutdown()
            thread.join(timeout=1)
    assert result['ok'] is False
    assert b'/target' not in paths
    assert all('exceeds' in check['error'] for check in result['checks'])


def test_standalone_cli_needs_only_standard_library():
    result = subprocess.run([sys.executable, '-S', str(SCRIPT), '--help'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert '--allow-degraded' in result.stdout
