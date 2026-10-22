"""Abandoned audio-probe batches retire HTTP I/O before another candidate."""
from __future__ import annotations

import asyncio
import json
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

from korina.routes import providers
from korina.services import multimodal_stt


@pytest.mark.parametrize('phase', ['headers', 'body'])
@pytest.mark.parametrize('retirement', ['disconnect', 'scope_cancel', 'task_cancel'])
def test_abandoned_probe_closes_provider_skips_cache_and_recovers_same_worker(monkeypatch, config_module, phase, retirement):
    entered, peer_closed, release, worker_finished = (threading.Event() for _ in range(4))
    requests, workers, audio_paths = [], [], []

    class Provider(socketserver.BaseRequestHandler):
        def handle(self):
            data = bytearray()
            while b'\r\n\r\n' not in data:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                data.extend(chunk)
            head, body = bytes(data).split(b'\r\n\r\n', 1)
            length = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n')
                              if line.lower().startswith(b'content-length:')))
            while len(body) < length:
                body += self.request.recv(length - len(body))
            model = json.loads(body)['model']
            requests.append(model)
            if model == 'old':
                if phase == 'body':
                    self.request.sendall(b'HTTP/1.1 415 Unsupported Media Type\r\nContent-Length: 1024\r\n\r\n{"error":"')
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
            reply = b'{"error":"audio input is not supported"}'
            self.request.sendall(b'HTTP/1.1 415 Unsupported Media Type\r\nContent-Type: application/json\r\nContent-Length: '
                                 + str(len(reply)).encode() + b'\r\nConnection: close\r\n\r\n' + reply)

    class ProviderServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    provider = ProviderServer(('127.0.0.1', 0), Provider)
    provider_thread = threading.Thread(target=provider.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    provider_thread.start()
    base = f'http://127.0.0.1:{provider.server_address[1]}/v1'
    config_module.update_config_values({'audio_unsupported': {}})
    config_module.set_audio_unsupported('openai-compatible', base, 'old', 'original', 'keep this entry')
    cache_before = config_module.config_audio_unsupported()
    transcribe = multimodal_stt.lmstudio_transcribe_wav

    def tracked_transcribe(wav, **kwargs):
        workers.append(threading.get_ident())
        audio_paths.append(wav)
        return transcribe(wav, **kwargs)

    monkeypatch.setattr(multimodal_stt, 'lmstudio_transcribe_wav', tracked_transcribe)
    probe_batch = providers._probe_models

    def tracked_batch(req, **kwargs):
        try:
            return probe_batch(req, **kwargs)
        finally:
            if req.models[0] == 'old':
                worker_finished.set()

    monkeypatch.setattr(providers, '_probe_models', tracked_batch)

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
    app.include_router(providers.router)
    active = {}
    active_receives = max_receives = 0

    @app.post('/cancel-inflight')
    async def cancel_inflight():
        active['task' if retirement == 'task_cancel' else 'scope'].cancel()
        return {'ok': True}

    async def cancellable_app(scope, receive, send):
        nonlocal active_receives, max_receives
        if scope['type'] != 'http' or b'old=1' not in scope['query_string']:
            return await app(scope, receive, send)
        started = complete = False

        async def tracked_send(message):
            nonlocal started, complete
            if message['type'] == 'http.response.start':
                started = True
            if message['type'] == 'http.response.body' and not message.get('more_body'):
                complete = True
            await send(message)

        async def tracked_receive():
            nonlocal active_receives, max_receives
            active_receives += 1
            max_receives = max(max_receives, active_receives)
            try:
                return await receive()
            finally:
                active_receives -= 1

        if retirement == 'disconnect':
            return await app(scope, tracked_receive, tracked_send)
        if retirement == 'task_cancel':
            active['task'] = asyncio.current_task()
            try:
                await app(scope, tracked_receive, tracked_send)
            except asyncio.CancelledError:
                cancelled = True
            else:
                cancelled = False
        else:
            with anyio.CancelScope() as owner:
                active['scope'] = owner
                await app(scope, tracked_receive, tracked_send)
            cancelled = owner.cancel_called
        if cancelled:
            if not started:
                await JSONResponse({'detail': 'test request cancelled'}, status_code=499)(scope, receive, send)
            elif not complete:
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(cancellable_app, log_level='error', ws='none'))
    serving = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    browser = None
    try:
        serving.start()
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        payload = {'provider': 'openai-compatible', 'base_url': base, 'models': ['old', 'never'], 'api_key_env': ''}
        browser = socket.create_connection(('127.0.0.1', port), timeout=2)
        body = json.dumps(payload).encode()
        browser.sendall(b'POST /api/llm/probe-models?old=1 HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '
                        + str(len(body)).encode() + b'\r\n\r\n' + body)
        assert entered.wait(3), 'provider I/O must already be connected'
        if retirement == 'disconnect':
            browser.close()
            browser = None
        else:
            assert httpx.post(f'http://127.0.0.1:{port}/cancel-inflight', timeout=2).status_code == 200
        assert peer_closed.wait(1), 'retirement must close the held provider connection'
        assert worker_finished.wait(1), 'the batch must return without waiting for provider release'
        assert not release.is_set()
        assert requests == ['old'], 'a retired batch must not admit its next model'
        assert config_module.config_audio_unsupported() == cache_before
        assert len(audio_paths) == 1 and not audio_paths[0].exists()
        assert max_receives == 1

        response = httpx.post(f'http://127.0.0.1:{port}/api/llm/probe-models',
                              json={**payload, 'models': ['fresh']}, timeout=3)
        assert response.status_code == 200
        assert response.json()['results']['fresh']['cached'] is True
        assert response.json()['results']['fresh']['status'] == 415
        assert requests == ['old', 'fresh']
        assert workers == [workers[0], workers[0]], 'the existing single pool worker must recover'
        assert not audio_paths[-1].exists()
        cache_after = config_module.config_audio_unsupported()
        assert {**cache_after, **cache_before} == cache_after
        assert len(cache_after) == 2
    finally:
        if browser is not None:
            browser.close()
        release.set()
        server.should_exit = True
        serving.join(5)
        listener.close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(1)
        assert not serving.is_alive()
