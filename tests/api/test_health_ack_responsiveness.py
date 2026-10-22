"""An ACK rebuild can delay its own health snapshot without freezing HTTP."""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import uvicorn
from fastapi import FastAPI

from korina.routes import capabilities, health
from korina.runtime.state import AckState
from korina.services import ack_service


def test_waiting_for_ack_transaction_does_not_block_unrelated_http(monkeypatch, tmp_path):
    manifest = tmp_path / 'ack_phrases.json'
    manifest.write_text('{"phrases": []}')
    config = {'ack_enabled': 'off', 'agent_enabled': 'off',
              'llm_provider': 'openai-compatible', 'llm_base_url': 'http://127.0.0.1:9/v1'}
    monkeypatch.setattr(ack_service.state, 'ack', AckState())
    monkeypatch.setattr(ack_service, 'ACK_PHRASES_PATH', manifest)
    monkeypatch.setattr(ack_service, 'ACK_DIR', tmp_path / 'cache')
    monkeypatch.setattr(ack_service, 'load_config', lambda: config)
    monkeypatch.setattr(health, 'load_config', lambda: config)
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': False, 'loaded': False})
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': False})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    transaction_entered, health_waiting, release = threading.Event(), threading.Event(), threading.Event()
    real_status = health.ack_status

    def observed_status(*args, **kwargs):
        health_waiting.set()
        return real_status(*args, **kwargs)

    monkeypatch.setattr(health, 'ack_status', observed_status)

    def hold_transaction():
        with ack_service.ack_rebuild_transaction():
            transaction_entered.set()
            assert release.wait(5)

    owner = threading.Thread(target=hold_transaction)
    owner.start()
    assert transaction_entered.wait(2)
    app = FastAPI()
    app.include_router(health.router)
    app.include_router(capabilities.router)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', ws='none'))
    serving = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    serving.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending_health = pool.submit(httpx.get, f'http://127.0.0.1:{port}/api/health', timeout=4)
            try:
                assert health_waiting.wait(2), 'health must reach the held ACK state'
                assert not pending_health.done()
                response = httpx.get(f'http://127.0.0.1:{port}/api/capabilities', timeout=1)
                assert response.status_code == 200
                assert 'providers' in response.json()
                assert not release.is_set(), 'the unrelated request must finish before releasing ACK work'
            finally:
                release.set()
            completed = pending_health.result(4)
            assert completed.status_code == 200
            assert completed.json()['ack_status']['ok'] is True
            assert completed.json()['ack_count'] == 0
    finally:
        release.set()
        owner.join(2)
        server.should_exit = True
        serving.join(5)
        listener.close()
        assert not serving.is_alive()
        assert not owner.is_alive()
