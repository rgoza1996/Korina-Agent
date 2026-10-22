"""Exercise real sockets: inactivity timeouts alone cannot pass these tests."""
from __future__ import annotations

import json
import socketserver
import threading
import time
from contextlib import contextmanager

import pytest

from korina.runtime import json_http


@contextmanager
def server_for(respond):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            request = bytearray()
            while b'\r\n\r\n' not in request:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                request.extend(chunk)
            try:
                respond(self.request, bytes(request))
            except (BrokenPipeError, ConnectionResetError):
                pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def send_response(connection, body, *, status=200, extra_headers=b''):
    connection.sendall(
        f'HTTP/1.1 {status} Status\r\nContent-Length: {len(body)}\r\n'.encode()
        + extra_headers + b'Connection: close\r\n\r\n' + body
    )


@pytest.mark.parametrize('phase', ['headers', 'body', 'chunk-header'])
def test_total_deadline_cancels_trickling_response_and_closes_socket(phase):
    disconnected = threading.Event()

    def respond(connection, request):
        try:
            if phase == 'headers':
                prefix, trickle = b'HTTP/1.1 200 OK\r\nX-Slow: ', b'x' * 100
            elif phase == 'chunk-header':
                prefix = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'
                trickle = b'0' * 100 + b'1\r\nx\r\n0\r\n\r\n'
            else:
                prefix = b'HTTP/1.1 200 OK\r\nContent-Length: 102\r\n\r\n'
                trickle = b'"' + b'x' * 100 + b'"'
            connection.sendall(prefix)
            for byte in trickle:
                connection.sendall(bytes([byte]))
                time.sleep(.01)
        finally:
            disconnected.set()

    with server_for(respond) as base:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match='timeout'):
            json_http.request_json(base, timeout=.15)
        assert time.monotonic() - started < .45
        assert disconnected.wait(.35), 'cancelled request must close its connected socket'


@pytest.mark.parametrize('declared', [True, False])
def test_success_response_limit_applies_with_or_without_content_length(declared):
    def respond(connection, request):
        if declared:
            send_response(connection, b'"' + b'x' * 2048 + b'"')
        else:
            connection.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n' + b'x' * 2048)

    with server_for(respond) as base:
        with pytest.raises(json_http.ResponseTooLargeError, match='1024 bytes'):
            json_http.request_json(base, timeout=1, max_bytes=1024)


def test_http_error_preserves_status_and_bounded_detail():
    with server_for(lambda connection, request: send_response(
        connection, b'unsupported input_audio ' + b'x' * (json_http.MAX_ERROR_BYTES * 3), status=422,
    )) as base:
        with pytest.raises(json_http.HTTPStatusError) as caught:
            json_http.request_json(base, timeout=1)
    assert caught.value.status_code == 422
    assert caught.value.detail.startswith('unsupported input_audio ')
    assert caught.value.detail.endswith(' [truncated]')
    assert len(caught.value.detail) == json_http.MAX_ERROR_BYTES + len(' [truncated]')


def test_identity_encoding_and_exact_json_auth_are_preserved():
    received = []

    def respond(connection, request):
        received.append(request)
        send_response(connection, b'{"loaded":true}')

    with server_for(respond) as base:
        assert json_http.request_json(
            base, method='POST', timeout=1,
            headers={'Authorization': 'Bearer exact-secret', 'X-Api-Key': 'provider-key'},
            json_body={'model': 'demo'},
        ) == {'loaded': True}
    assert b'Authorization: Bearer exact-secret\r\n' in received[0]
    assert b'X-Api-Key: provider-key\r\n' in received[0]
    assert b'Accept-Encoding: identity\r\n' in received[0]


def test_cross_origin_redirect_does_not_forward_bearer_credentials():
    received = []

    def target(connection, request):
        received.append(request)
        send_response(connection, b'{"ok":true}')

    with server_for(target) as target_base:
        def redirect(connection, request):
            send_response(connection, b'', status=302, extra_headers=f'Location: {target_base}/models\r\n'.encode())

        with server_for(redirect) as source:
            assert json_http.request_json(source, timeout=1, headers={
                'Authorization': 'Bearer secret', 'X-Api-Key': 'anthropic-secret', 'Cookie': 'private-session',
            }) == {'ok': True}
    assert b'Authorization:' not in received[0]
    assert b'X-Api-Key:' not in received[0]
    assert b'Cookie:' not in received[0]


def test_three_hop_redirect_cannot_restore_old_origin_cookie_but_keeps_new_cookie():
    received = []

    def target(connection, request):
        received.append(request)
        if request.startswith(b'GET /first '):
            send_response(connection, b'', status=302, extra_headers=(
                b'Location: /second\r\nSet-Cookie: target-session=public-fixture; Path=/\r\n'
            ))
        else:
            send_response(connection, b'{"ok":true}')

    with server_for(target) as target_base:
        def source(connection, request):
            send_response(connection, b'', status=302, extra_headers=(
                f'Location: {target_base}/first\r\n'.encode()
                + b'Set-Cookie: provider-session=private-fixture; Path=/\r\n'
            ))

        with server_for(source) as source_base:
            assert json_http.request_json(source_base, timeout=1) == {'ok': True}
    assert len(received) == 2
    assert all(b'private-fixture' not in request for request in received)
    assert b'Cookie:' not in received[0]
    assert b'Cookie: target-session=public-fixture\r\n' in received[1]


def test_same_origin_redirect_keeps_its_session_cookie():
    received = []

    def respond(connection, request):
        received.append(request)
        if request.startswith(b'GET /first '):
            send_response(connection, b'', status=302, extra_headers=(
                b'Location: /second\r\nSet-Cookie: provider-session=private-fixture; Path=/\r\n'
            ))
        else:
            send_response(connection, b'{"ok":true}')

    with server_for(respond) as base:
        assert json_http.request_json(base + '/first', timeout=1) == {'ok': True}
    assert b'Cookie: provider-session=private-fixture\r\n' in received[1]


def test_redirect_body_is_closed_without_unbounded_reading():
    def respond(connection, request):
        if request.startswith(b'GET /target '):
            send_response(connection, b'{"ok":true}')
        else:
            connection.sendall(
                b'HTTP/1.1 302 Redirect\r\nLocation: /target\r\n'
                b'Content-Length: 1000000000\r\n\r\n'
            )
            # No body ever arrives. Following the redirect must not read it.
            connection.recv(1)

    with server_for(respond) as base:
        assert json_http.request_json(base, timeout=.5) == {'ok': True}


def test_redirects_share_one_total_deadline():
    def respond(connection, request):
        time.sleep(.06)
        send_response(connection, b'', status=302, extra_headers=b'Location: /again\r\n')

    with server_for(respond) as base:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            json_http.request_json(base, timeout=.15)
        assert time.monotonic() - started < .4


def test_unexpected_compression_fails_before_unbounded_decompression():
    with server_for(lambda connection, request: send_response(
        connection, b'not decoded', extra_headers=b'Content-Encoding: gzip\r\n',
    )) as base:
        with pytest.raises(ValueError, match='Content-Encoding'):
            json_http.request_json(base, timeout=1)


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan')])
def test_invalid_deadlines_fail_before_network(timeout):
    with pytest.raises(ValueError, match='timeout'):
        json_http.request_json('http://127.0.0.1:1', timeout=timeout)


@pytest.mark.parametrize('url', ['file:///tmp/provider.json', 'not a URL', 'http://host:bad-port'])
def test_invalid_provider_urls_are_value_errors(url):
    with pytest.raises(ValueError, match=r'http\(s\) URL'):
        json_http.request_json(url, timeout=1)


def test_binary_response_and_invalid_json_have_explicit_results():
    with server_for(lambda connection, request: send_response(connection, b'RIFF audio')) as base:
        assert json_http.request_bytes(base, timeout=1) == b'RIFF audio'
        with pytest.raises(json.JSONDecodeError):
            json_http.request_json(base, timeout=1)


@pytest.mark.parametrize('invalid_body,error', [
    (b'[' * 10000 + b'0' + b']' * 10000, 'nesting depth'),
    (b'{"loaded":NaN}', 'invalid numeric constant'),
    (b'{"loaded":Infinity}', 'invalid numeric constant'),
    (b'{"loaded":-Infinity}', 'invalid numeric constant'),
    (b'{"loaded":1e999}', 'non-finite number'),
])
def test_invalid_json_values_degrade_real_health_and_readiness(client, monkeypatch, invalid_body, error):
    from korina.routes import health

    with server_for(lambda connection, request: send_response(connection, invalid_body)) as base:
        with pytest.raises(ValueError, match=error):
            json_http.request_json(base, timeout=1)
        llm = health._probe_llm_models(base + '/v1', 'demo', timeout=1)
        tts_ok, _, tts_error = health._probe_tts_backend(base, timeout=1)
        assert llm['ok'] is False and error in llm['error']
        assert tts_ok is False and error in tts_error
        monkeypatch.setattr(health, 'load_config', lambda: {
            'llm_provider': 'openai-compatible', 'llm_base_url': base + '/v1',
            'lm_model': 'demo', 'agent_enabled': 'off', 'tts_base_url': base,
            'tts_provider': 'kokoro',
        })
        health_response = client.get('/api/health')
        assert health_response.status_code == 200
        assert health_response.json()['readiness']['ready'] is False
        assert client.get('/api/ready').status_code == 503


def test_normal_json_numbers_are_preserved():
    with server_for(lambda connection, request: send_response(
        connection, b'{"seconds":1.25,"count":2,"small":-2e-10,"loaded":true}',
    )) as base:
        assert json_http.request_json(base, timeout=1) == {
            'seconds': 1.25, 'count': 2, 'small': -2e-10, 'loaded': True,
        }


def test_cancelled_generation_does_not_open_network():
    with pytest.raises(json_http.RequestCancelledError):
        json_http.request_bytes('http://127.0.0.1:1', timeout=1, is_current=lambda: False)
