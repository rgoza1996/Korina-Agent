"""ACK rebuild ownership survives HTTP aborts and delayed request admission."""
from __future__ import annotations

import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korina.routes import acks
from korina.runtime.state import AckState
from korina.services import ack_service

WAV = b'RIFF' + (100).to_bytes(4, 'little') + b'WAVE' + b'\0' * 100


@pytest.fixture
def rebuild_runtime(monkeypatch, tmp_path):
    cache = tmp_path / 'cache'
    cache.mkdir()
    manifest = tmp_path / 'ack_phrases.json'
    manifest.write_text(json.dumps({'phrases': [{'id': 'okay', 'text': 'Original text.', 'tags': ['global']}]}))
    config = {'voice': 'af_heart', 'ack_enabled': 'on'}
    owner = AckState()
    monkeypatch.setattr(ack_service.state, 'ack', owner)
    monkeypatch.setattr(ack_service, 'ACK_DIR', cache)
    monkeypatch.setattr(ack_service, 'ACK_PHRASES_PATH', manifest)
    monkeypatch.setattr(ack_service, 'load_config', lambda: dict(config))
    published = []

    def publish(voice, tag=None, *, force=False, manifest=None):
        # Native TTS is outside this contract. Retain the real route,
        # transaction, cache clear, filename and status implementations.
        for phrase in manifest:
            ack_service.ack_path(voice, phrase).write_bytes(WAV)
            published.append((voice, phrase['text']))
        return 0

    monkeypatch.setattr(acks, 'enqueue_missing_acks', publish)
    app = FastAPI()
    app.include_router(acks.router)
    return config, owner, cache, manifest, published, app


@contextmanager
def serve(app):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', ws='none'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        yield listener.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive()


def test_socket_abort_does_not_allow_new_clear_to_overtake_active_transaction(rebuild_runtime, monkeypatch):
    config, owner, cache, _, published, app = rebuild_runtime
    entered, release, latest_received = threading.Event(), threading.Event(), threading.Event()
    real_clear = acks.clear_ack_wavs
    clears = []

    def held_clear():
        index = len(clears) + 1
        clears.append(index)
        if index == 1:
            entered.set()
            assert release.wait(5)
        return real_clear()

    monkeypatch.setattr(acks, 'clear_ack_wavs', held_clear)
    received = 0

    async def tracked_app(scope, receive, send):
        nonlocal received
        if scope['type'] == 'http' and scope['path'] == '/api/acks/rebuild':
            received += 1
            if received == 2:
                latest_received.set()
        await app(scope, receive, send)

    with serve(tracked_app) as port, ThreadPoolExecutor(max_workers=1) as pool:
        browser = socket.create_connection(('127.0.0.1', port), timeout=2)
        try:
            payload = json.dumps({'voice': 'af_heart', 'clear': True, 'require_saved_voice': True}).encode()
            browser.sendall(b'POST /api/acks/rebuild HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '
                            + str(len(payload)).encode() + b'\r\n\r\n' + payload)
            assert entered.wait(3)
            browser.close()  # A client deadline closes the same socket.
            config['voice'] = 'af_bella'
            latest = pool.submit(httpx.post, f'http://127.0.0.1:{port}/api/acks/rebuild',
                                 json={'voice': 'af_bella', 'clear': True, 'require_saved_voice': True}, timeout=4)
            assert latest_received.wait(2)
            assert clears == [1], 'the next clear must wait while an aborted request still owns mutation'
            assert not latest.done()
            release.set()
            response = latest.result(4)
            assert response.status_code == 200, response.text
            assert response.json()['voice'] == 'af_bella'
            assert published == [('af_heart', 'Original text.'), ('af_bella', 'Original text.')]
            assert owner.current_voice == 'af_bella'
            assert sorted(path.name for path in cache.glob('*.wav')) == [
                ack_service.ack_filename('af_bella', ack_service.load_ack_manifest()[0])]
            # An old request first reaching the handler now cannot clear the
            # newer saved voice, even though its admission would be later.
            rejected = httpx.post(f'http://127.0.0.1:{port}/api/acks/rebuild',
                                  json={'voice': 'af_heart', 'clear': True, 'require_saved_voice': True}, timeout=2)
            assert rejected.status_code == 409
            assert clears == [1, 2]
            assert owner.current_voice == 'af_bella'
        finally:
            release.set()
            browser.close()


def test_new_admission_supersedes_older_waiting_transaction(rebuild_runtime):
    _, owner, _, _, _, _ = rebuild_runtime
    mutations = []

    def transaction(label):
        with ack_service.ack_rebuild_transaction():
            mutations.append(label)

    owner.rebuild_lock.acquire()
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            older = pool.submit(transaction, 'old')
            deadline = time.monotonic() + 2
            while owner.rebuild_generation < 1 and time.monotonic() < deadline:
                time.sleep(.005)
            assert owner.rebuild_generation == 1
            newer = pool.submit(transaction, 'new')
            deadline = time.monotonic() + 2
            while owner.rebuild_generation < 2 and time.monotonic() < deadline:
                time.sleep(.005)
            assert owner.rebuild_generation == 2
        finally:
            owner.rebuild_lock.release()
        with pytest.raises(ack_service.AckRebuildConflictError, match='superseded'):
            older.result(2)
        newer.result(2)
    assert mutations == ['new']


def test_saved_voice_precondition_rejects_without_cache_queue_or_admission_mutation(rebuild_runtime):
    config, owner, cache, _, _, app = rebuild_runtime
    config['voice'] = 'af_bella'
    cached = cache / 'preserved.wav'
    cached.write_bytes(WAV)
    owner.queue.append(('af_bella', 'old', 'Keep this work.', False))
    with TestClient(app) as client:
        response = client.post('/api/acks/rebuild', json={
            'voice': 'af_heart', 'clear': True, 'require_saved_voice': True})
    assert response.status_code == 409
    assert 'Saved voice changed' in response.json()['detail']
    assert cached.read_bytes() == WAV
    assert owner.queue == [('af_bella', 'old', 'Keep this work.', False)]
    assert owner.generation == owner.rebuild_generation == 0


def test_explicit_rebuild_without_precondition_can_target_unsaved_voice(rebuild_runtime):
    config, owner, _, _, published, app = rebuild_runtime
    config['voice'] = 'af_bella'
    with TestClient(app) as client:
        response = client.post('/api/acks/rebuild', json={'voice': 'af_heart', 'clear': True})
    assert response.status_code == 200
    assert published == [('af_heart', 'Original text.')]
    assert owner.current_voice == 'af_heart'
    assert config['voice'] == 'af_bella'


def test_saved_voice_is_rechecked_after_waiting_for_an_active_transaction(rebuild_runtime):
    config, owner, cache, _, published, app = rebuild_runtime
    cached = cache / 'preserved.wav'
    cached.write_bytes(WAV)
    owner.rebuild_lock.acquire()
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/acks/rebuild', json={
            'voice': 'af_heart', 'clear': True, 'require_saved_voice': True})
        try:
            deadline = time.monotonic() + 2
            while owner.rebuild_generation < 1 and time.monotonic() < deadline:
                time.sleep(.005)
            assert owner.rebuild_generation == 1
            config['voice'] = 'af_bella'
        finally:
            owner.rebuild_lock.release()
        assert future.result(2).status_code == 409
    assert cached.read_bytes() == WAV
    assert published == []
    assert owner.generation == 0


def test_waiting_rebuild_uses_its_validated_manifest_snapshot(rebuild_runtime, monkeypatch):
    _, _, _, manifest, published, app = rebuild_runtime
    entered, release = threading.Event(), threading.Event()
    real_transaction = acks.ack_rebuild_transaction

    @contextmanager
    def held_transaction(**kwargs):
        entered.set()
        assert release.wait(3)
        with real_transaction(**kwargs):
            yield

    monkeypatch.setattr(acks, 'ack_rebuild_transaction', held_transaction)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/api/acks/rebuild', json={'voice': 'af_heart', 'clear': True})
        try:
            assert entered.wait(2)
            manifest.write_text(json.dumps({'phrases': [{'id': 'okay', 'text': 'Edited text.', 'tags': ['global']}]}))
        finally:
            release.set()
        assert future.result(2).status_code == 200
    assert published == [('af_heart', 'Original text.')]
