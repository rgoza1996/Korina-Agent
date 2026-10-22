"""Native-browser fixture: production chat route and a held localhost provider."""
from __future__ import annotations

import json
import signal
import socket
import socketserver
import sys
import threading
import time
from contextlib import asynccontextmanager

import anyio
import uvicorn
from runtime import REPO, browser_runtime

sys.path.insert(0, str(REPO))
phase = sys.argv[1]
entered = threading.Event()
peer_closed = threading.Event()
release = threading.Event()
workers: list[int] = []


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
        payload = json.loads(body) if length else {}
        if payload.get('messages', [{}])[-1].get('content') == 'Old turn.':
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
        result = ({'choices': [{'message': {'content': 'Fresh reply.'}}]} if length
                  else {'data': [{'id': 'fixture-model'}]})
        reply = json.dumps(result).encode()
        self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                             + str(len(reply)).encode() + b'\r\nConnection: close\r\n\r\n' + reply)


class ProviderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


with browser_runtime() as runtime:
    provider = ProviderServer(('127.0.0.1', 0), Provider)
    provider_thread = threading.Thread(target=provider.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    provider_thread.start()
    config = json.loads((runtime / 'config.json').read_text())
    config.update({
        'llm_base_url': f'http://127.0.0.1:{provider.server_address[1]}/v1',
        'lm_model': 'fixture-model',
    })
    (runtime / 'config.json').write_text(json.dumps(config))
    from korina.app_factory import create_app
    from korina.services import response_llm

    request_json = response_llm.request_json

    def tracked_request(*args, **kwargs):
        workers.append(threading.get_ident())
        return request_json(*args, **kwargs)

    response_llm.request_json = tracked_request
    app = create_app()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        try:
            async with original_lifespan(application):
                yield
        finally:
            limiter.total_tokens = previous

    app.router.lifespan_context = lifespan

    @app.get('/test-chat-owner')
    async def stats():
        return {'entered': entered.is_set(), 'peer_closed': peer_closed.is_set(), 'workers': workers}

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', ws='none'))
    server_thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    signal.signal(signal.SIGTERM, lambda *args: setattr(server, 'should_exit', True))
    try:
        server_thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        if not server.started:
            raise RuntimeError('chat ownership fixture failed to start')
        print(json.dumps({'base_url': f'http://127.0.0.1:{port}'}), flush=True)
        while server_thread.is_alive():
            server_thread.join(.1)
    finally:
        release.set()
        server.should_exit = True
        server_thread.join(5)
        provider.shutdown()
        provider.server_close()
        provider_thread.join(1)
        listener.close()
