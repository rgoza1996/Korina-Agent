"""Real browser/provider sockets exercise owned chat I/O and pool recovery."""
from __future__ import annotations

import json
import socket
import socketserver
import threading
import time
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from korina.routes import chat
from korina.runtime.json_http import HTTPStatusError, RequestCancelledError
from korina.schemas import ChatRequest
from korina.services import response_llm


@pytest.mark.parametrize('phase', ['headers', 'body', 'outer-cancel'])
def test_browser_disconnect_closes_provider_and_same_pool_worker_recovers(monkeypatch, phase, asgi_server):
    entered, peer_closed, release = threading.Event(), threading.Event(), threading.Event()

    class Provider(socketserver.BaseRequestHandler):
        def handle(self):
            data = bytearray()
            while b'\r\n\r\n' not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                data.extend(chunk)
            head, body = bytes(data).split(b'\r\n\r\n', 1)
            length = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n') if line.lower().startswith(b'content-length:')))
            while len(body) < length:
                body += self.request.recv(length - len(body))
            payload = json.loads(body)
            if payload['messages'][-1]['content'] == 'cancel old turn':
                if phase == 'body':
                    self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 1024\r\n\r\n{')
                entered.set()
                self.request.settimeout(.02)
                while not release.is_set():
                    try:
                        if not self.request.recv(1):
                            peer_closed.set()
                            return
                    except socket.timeout:
                        pass
                    except ConnectionResetError:
                        peer_closed.set()
                        return
                return
            reply = json.dumps({'choices': [{'message': {'content': 'Recovered. Ready.'}}]}).encode()
            self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                                 + str(len(reply)).encode() + b'\r\nConnection: close\r\n\r\n' + reply)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    provider = Server(('127.0.0.1', 0), Provider)
    provider_thread = threading.Thread(target=provider.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    provider_thread.start()
    config = {'llm_base_url': f'http://127.0.0.1:{provider.server_address[1]}/v1', 'lm_model': 'fixture-model'}
    monkeypatch.setattr(response_llm, 'load_config', lambda: config)
    monkeypatch.setattr(chat, 'load_config', lambda: config)
    request_json = response_llm.request_json
    workers = []

    def tracked_request(*args, **kwargs):
        workers.append(threading.get_ident())
        return request_json(*args, **kwargs)

    monkeypatch.setattr(response_llm, 'request_json', tracked_request)

    @asynccontextmanager
    async def lifespan(app):
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        try:
            yield
        finally:
            limiter.total_tokens = previous

    app = FastAPI(lifespan=lifespan)
    app.include_router(chat.router)
    active = {}

    @app.post('/cancel-inflight')
    async def cancel_inflight():
        active['scope'].cancel()
        return {'ok': True}

    async def cancellable_app(scope, receive, send):
        if phase != 'outer-cancel' or scope['type'] != 'http' or scope['path'] != '/api/chat':
            return await app(scope, receive, send)
        started = finished = False

        async def tracked_send(message):
            nonlocal started, finished
            if message['type'] == 'http.response.start':
                started = True
            if message['type'] == 'http.response.body' and not message.get('more_body'):
                finished = True
            await send(message)

        with anyio.CancelScope() as owner:
            active['scope'] = owner
            await app(scope, receive, tracked_send)
        if owner.cancel_called:
            if not started:
                await JSONResponse({'detail': 'test ASGI request cancelled'}, status_code=499)(scope, receive, send)
            elif not finished:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

    browser = None
    try:
        with asgi_server(cancellable_app) as app_port:
            try:
                browser = socket.create_connection(('127.0.0.1', app_port), timeout=2)
                body = b'{"message":"cancel old turn"}'
                browser.sendall(b'POST /api/chat HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '
                                + str(len(body)).encode() + b'\r\n\r\n' + body)
                assert entered.wait(3), 'provider must be connected before closing the browser'
                started = time.monotonic()
                if phase == 'outer-cancel':
                    cancelled = httpx.post(f'http://127.0.0.1:{app_port}/cancel-inflight', timeout=2)
                    assert cancelled.status_code == 200
                else:
                    browser.close()
                    browser = None
                assert peer_closed.wait(1), 'abandoned chat must close its connected provider socket'
                assert time.monotonic() - started < 1
                response = httpx.post(f'http://127.0.0.1:{app_port}/api/chat', json={'message': 'fresh turn'}, timeout=3)
                assert response.status_code == 200
                assert response.json()['reply'] == 'Recovered.\nReady.'
                assert response.json()['model'] == 'fixture-model'
                assert workers == [workers[0], workers[0]], 'fresh chat must recover the existing single pool worker'
            finally:
                if browser is not None:
                    browser.close()
                release.set()
    finally:
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=1)


def test_chat_validation_and_provider_error_envelopes_are_preserved(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router)
    calls = []

    def reject(*args, **kwargs):
        calls.append(True)
        raise HTTPStatusError(422, 'unsupported input')

    monkeypatch.setattr(response_llm, 'request_json', reject)
    with TestClient(app) as client:
        assert client.post('/api/chat', json={'message': '  '}).status_code == 400
        assert client.post('/api/chat', json={}).status_code == 422
        failed = client.post('/api/chat', json={'message': 'hello'})
    assert calls == [True]
    assert failed.status_code == 500
    assert failed.json() == {'detail': 'Response LLM HTTP 422: unsupported input'}


def test_direct_response_service_preserves_cancellation_exception(monkeypatch):
    failure = RequestCancelledError('provider request was cancelled')

    def cancelled(*args, **kwargs):
        assert not kwargs['is_current']()
        raise failure

    monkeypatch.setattr(response_llm, 'request_json', cancelled)
    with pytest.raises(RequestCancelledError) as caught:
        response_llm.response_llm_chat(ChatRequest(message='hello'), is_current=lambda: False)
    assert caught.value is failure
