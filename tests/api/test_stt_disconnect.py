"""Abandoned HTTP-backed STT releases its provider connection and pool worker."""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import socketserver
import threading
import time
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from starlette.responses import JSONResponse

from korina.routes import stt
from korina.runtime import json_http
from korina.services import multimodal_stt, whisper_service

CASES = [(endpoint, spec, phase, 'disconnect', False)
         for endpoint, spec in [('/api/transcribe', '2.0'), ('/api/transcribe/partial', '2.0'),
                                ('/api/transcribe/stream', '2.0'), ('/api/transcribe/stream', '2.4')]
         for phase in ['headers', 'body']]
CASES += [('/api/transcribe/partial', '2.0', 'body', 'scope', False),
          ('/api/transcribe/stream', '2.4', 'body', 'scope', False),
          ('/api/transcribe/stream', '2.4', 'body', 'task', False),
          ('/api/transcribe', '2.0', 'headers', 'disconnect', True)]


@pytest.mark.parametrize('endpoint,spec_version,phase,cancellation,discover', CASES)
def test_abandoned_http_stt_closes_provider_without_fallback_and_recovers(
    monkeypatch, tmp_path, config_module, endpoint, spec_version, phase, cancellation, discover,
):
    entered, peer_closed, release, old_finished = (threading.Event() for _ in range(4))
    calls, workers, fallback = [], [], []
    captures = tmp_path / 'captures'
    captures.mkdir()
    make_directory = stt.tempfile.TemporaryDirectory

    def temporary_directory(*args, **kwargs):
        if kwargs.get('prefix', '').startswith('korina-stt-'):
            kwargs['dir'] = captures
        return make_directory(*args, **kwargs)

    monkeypatch.setattr(stt.tempfile, 'TemporaryDirectory', temporary_directory)
    for module in (stt, whisper_service):
        monkeypatch.setattr(module, 'convert_to_16k_wav', shutil.copyfile)
        monkeypatch.setattr(module, 'audio_metadata', lambda wav: {
            'duration': 1, 'samples': 16000, 'sample_rate': 16000,
        })

        def forbidden_fallback(*args, **kwargs):
            fallback.append(True)
            raise AssertionError('abandoned HTTP must not start native inference')

        monkeypatch.setattr(module, 'transcribe_wav_segments', forbidden_fallback)

    class Provider(socketserver.BaseRequestHandler):
        def handle(self):
            data = bytearray()
            while b'\r\n\r\n' not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                data.extend(chunk)
            head, body = bytes(data).split(b'\r\n\r\n', 1)
            length = next((int(line.split(b':', 1)[1]) for line in head.split(b'\r\n')
                           if line.lower().startswith(b'content-length:')), 0)
            while len(body) < length:
                chunk = self.request.recv(length - len(body))
                if not chunk:
                    return
                body += chunk
            method = head.split(b' ', 1)[0]
            calls.append(method)
            if len(calls) == 1:
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
            payload = ({'data': [{'id': 'fixture-audio'}]} if method == b'GET' else
                       {'choices': [{'message': {'content': 'Fresh transcript.'}, 'finish_reason': 'stop'}]})
            reply = json.dumps(payload).encode()
            self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                                 + str(len(reply)).encode() + b'\r\nConnection: close\r\n\r\n' + reply)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    provider = Server(('127.0.0.1', 0), Provider)
    provider_thread = threading.Thread(target=provider.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    provider_thread.start()
    base = f'http://127.0.0.1:{provider.server_address[1]}/v1'
    monkeypatch.setattr(config_module, 'LMSTUDIO_MODEL', '')
    config_module.update_config_values({
        'stt_llm_provider': 'openai-compatible', 'stt_llm_base_url': base,
        'stt_llm_model': '' if discover else 'fixture-audio', 'lm_model': '', 'audio_unsupported': {},
    })
    request_json = json_http.request_json

    def tracked_request(*args, **kwargs):
        workers.append(threading.get_ident())
        assert kwargs['is_current']()
        return request_json(*args, **kwargs)

    monkeypatch.setattr(json_http, 'request_json', tracked_request)
    monkeypatch.setattr(multimodal_stt, 'request_json', tracked_request)

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
    app.include_router(stt.router)
    active = {}

    @app.post('/cancel-inflight')
    async def cancel_inflight():
        if cancellation == 'scope':
            active['scope'].cancel()
        else:
            active['task'].cancel()
        return {'ok': True}

    async def observed_app(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        scope = {**scope, 'asgi': {**scope.get('asgi', {}), 'spec_version': spec_version}}
        if b'old=1' not in scope['query_string']:
            return await app(scope, receive, send)
        started = finished = cancelled = False

        async def tracked_send(message):
            nonlocal started, finished
            if message['type'] == 'http.response.start':
                started = True
            if message['type'] == 'http.response.body' and not message.get('more_body'):
                finished = True
            await send(message)

        try:
            if cancellation == 'task':
                active['task'] = asyncio.current_task()
                try:
                    await app(scope, receive, tracked_send)
                except asyncio.CancelledError:
                    cancelled = True
            else:
                with anyio.CancelScope() as owner:
                    active['scope'] = owner
                    await app(scope, receive, tracked_send)
                cancelled = owner.cancel_called
            if cancelled:
                if not started:
                    await JSONResponse({'detail': 'test ASGI request cancelled'}, status_code=499)(scope, receive, send)
                elif not finished:
                    await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        finally:
            old_finished.set()

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(observed_app, log_level='error', ws='none'))
    app_thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    browser = None
    try:
        app_thread.start()
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        browser = socket.create_connection(('127.0.0.1', port), timeout=2)
        body = (b'--audit\r\nContent-Disposition: form-data; name="audio"; filename="old.wav"\r\n'
                b'Content-Type: audio/wav\r\n\r\nfixture\r\n--audit--\r\n')
        browser.sendall(f'POST {endpoint}?backend=llm&old=1 HTTP/1.1\r\nHost: localhost\r\nContent-Type: multipart/form-data; boundary=audit\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
        assert entered.wait(3), 'provider must be connected before retiring its browser owner'
        started = time.monotonic()
        if cancellation == 'disconnect':
            browser.close()
            browser = None
        else:
            assert httpx.post(f'http://127.0.0.1:{port}/cancel-inflight', timeout=2).status_code == 200
        assert peer_closed.wait(1), 'abandoned STT must close its established provider connection'
        assert time.monotonic() - started < 1
        assert old_finished.wait(1), 'retired response must finish serialized generator cleanup'
        assert not list(captures.iterdir()), 'abandoned uploads must be removed before accepting fresh work'
        response = httpx.post(f'http://127.0.0.1:{port}{endpoint}?backend=llm',
                              files={'audio': ('fresh.wav', b'fixture', 'audio/wav')}, timeout=3)
        assert response.status_code == 200, response.text
        assert 'Fresh transcript.' in response.text
        assert fallback == []
        assert config_module.config_audio_unsupported() == {}
        if cancellation != 'task':
            assert len(set(workers)) == 1, 'normal disconnect/scope cancellation recovers the same single pool worker'
        assert calls == ([b'GET', b'GET', b'POST'] if discover else [b'POST', b'POST'])
        assert not list(captures.iterdir())
    finally:
        if browser is not None:
            browser.close()
        release.set()
        server.should_exit = True
        app_thread.join(timeout=5)
        listener.close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=1)
        assert not app_thread.is_alive()
