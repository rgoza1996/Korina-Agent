"""Disposable production API for the native candidate-credential boundary test."""
from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time

import uvicorn
from runtime import REPO, browser_runtime

sys.path.insert(0, str(REPO))

with browser_runtime() as runtime:
    config = json.loads((runtime / 'config.json').read_text())
    config.update({
        'llm_base_url': sys.argv[1] + '/saved/v1', 'llm_api_key_env': 'FIXTURE_DISCOVERY_KEY',
        'stt_llm_base_url': sys.argv[1] + '/saved/v1', 'stt_llm_api_key_env': 'FIXTURE_DISCOVERY_KEY',
        'converse': {'allowed_origins': [sys.argv[1]]},
    })
    (runtime / 'config.json').write_text(json.dumps(config))
    os.environ['FIXTURE_DISCOVERY_KEY'] = 'synthetic-discovery-key'
    from korina.app_factory import create_app

    app = create_app()
    requests = []

    @app.get('/test-discovery-requests')
    async def stats():
        return requests

    async def observed(scope, receive, send):
        if scope.get('path') == '/api/models':
            headers = dict(scope['headers'])
            requests.append({
                'method': scope['method'], 'origin': headers.get(b'origin', b'').decode(),
                'intent': headers.get(b'x-korina-intent', b'').decode(),
                'fetch_mode': headers.get(b'sec-fetch-mode', b'').decode(),
            })
        await app(scope, receive, send)

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(observed, log_level='error', ws='none'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, 'should_exit', True))
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        if not server.started:
            raise RuntimeError('Model intent fixture failed to start')
        print(json.dumps({'base_url': f'http://127.0.0.1:{listener.getsockname()[1]}'}), flush=True)
        while thread.is_alive():
            thread.join(.1)
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError('Model intent fixture failed to stop')
