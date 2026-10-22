"""ACK HTTP validation and disabled/rebuild policy."""
from __future__ import annotations

import time

import httpx
import pytest

from korina.runtime.state import AckState
from korina.services import ack_service


@pytest.fixture
def ack_cache(monkeypatch, tmp_path):
    config = {'ack_enabled': 'off'}
    cache = tmp_path / 'cache'
    cache.mkdir()
    phrase = {'id': 'hello', 'text': 'Okay.', 'tags': ['global']}
    monkeypatch.setattr(ack_service.state, 'ack', AckState())
    monkeypatch.setattr(ack_service, 'ACK_DIR', cache)
    monkeypatch.setattr(ack_service, 'load_ack_manifest', lambda: [phrase])
    monkeypatch.setattr(ack_service, 'load_config', lambda: config)
    return config, cache, ack_service.enqueue_missing_acks


def test_get_disabled_catalog_does_not_queue_generation(ack_cache, client, monkeypatch):
    from korina.routes import acks
    monkeypatch.setattr(acks, 'enqueue_missing_acks', ack_cache[2])
    response = client.get('/api/acks?voice=af_heart')
    assert response.status_code == 200
    assert response.json()['status']['enabled'] is False
    assert response.json()['status']['missing_count'] == 1
    assert ack_service.state.ack.queue == []
    assert ack_service.state.ack.worker_running is False


def test_explicit_rebuild_generates_even_when_playback_is_disabled(ack_cache, client, monkeypatch, mock_provider_http):
    from korina.routes import acks
    monkeypatch.setattr(acks, 'enqueue_missing_acks', ack_cache[2])
    data = b'RIFF' + (100).to_bytes(4, 'little') + b'WAVE' + b'\0' * 100
    mock_provider_http(lambda request: httpx.Response(200, content=data))
    response = client.post('/api/acks/rebuild', json={'voice': 'af_heart', 'clear': True})
    assert response.status_code == 200
    assert response.json()['ok'] is True
    deadline = time.monotonic() + 3
    while ack_service.state.ack.worker_running and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not ack_service.state.ack.worker_running
    assert len(list(ack_cache[1].glob('*.wav'))) == 1
    assert client.get('/api/acks/status').json()['generated_count'] == 1


@pytest.mark.parametrize('url', ['/api/acks', '/api/acks/status'])
def test_voice_query_is_bounded(url, client):
    assert client.get(url, params={'voice': 'v' * 65}).status_code == 422


def test_tag_query_is_bounded(client):
    assert client.get('/api/acks', params={'tag': 't' * 65}).status_code == 422


@pytest.mark.parametrize('payload', [
    {'voice': 123}, {'voice': 'v' * 65}, {'tag': 't' * 65}, {'voice': '   '},
    {'clear': []}, {'require_saved_voice': 'true'}, {'extra': 'not part of the contract'}, [],
])
def test_rebuild_rejects_malformed_or_unbounded_fields(payload, client):
    assert client.post('/api/acks/rebuild', json=payload).status_code == 422
