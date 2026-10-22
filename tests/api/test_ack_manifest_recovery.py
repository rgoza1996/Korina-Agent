"""Optional ACK catalog errors do not take down the voice application's API."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
import pytest

from korina.runtime.state import AckState
from korina.services import ack_service


@pytest.fixture
def manifest_runtime(monkeypatch, tmp_path):
    manifest = tmp_path / 'ack_phrases.json'
    manifest.write_text('{')
    cache = tmp_path / 'cache'
    cache.mkdir()
    monkeypatch.setattr(ack_service, 'ACK_PHRASES_PATH', manifest)
    monkeypatch.setattr(ack_service, 'ACK_DIR', cache)
    monkeypatch.setattr(ack_service.state, 'ack', AckState())
    from korina.routes import health
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': False, 'loaded': False, 'error': 'fixture'})
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': False, 'status': 'offline'})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    return manifest, cache


def test_bad_catalog_routes_fail_actionably_without_touching_queue_or_cache(manifest_runtime, client):
    manifest, cache = manifest_runtime
    cached = cache / 'existing.wav'
    cached.write_bytes(b'preserve existing audio')
    queue = [('old_voice', 'old_phrase', 'Keep this work.', False)]
    ack_service.state.ack.queue[:] = queue
    ack_service.state.ack.generation = 7
    for route in ('/api/acks', '/api/acks/status'):
        response = client.get(route)
        assert response.status_code == 503
        assert 'Repair Ack/ack_phrases.json and retry' in response.json()['detail']
    response = client.post('/api/acks/rebuild', json={'voice': 'af_bella', 'clear': True})
    assert response.status_code == 503
    assert cached.read_bytes() == b'preserve existing audio'
    assert ack_service.state.ack.queue == queue
    assert ack_service.state.ack.generation == 7
    assert ack_service.state.ack.current_voice == 'af_heart'
    assert not ack_service.state.ack.worker_running
    assert manifest.read_text() == '{'


def test_health_reports_unknown_ack_counts_and_recovers_after_repair(manifest_runtime, client):
    manifest, _ = manifest_runtime
    failed = client.get('/api/health')
    assert failed.status_code == 200
    payload = failed.json()
    assert payload['ok'] is True
    assert payload['ack_count'] is None
    assert payload['ack_status']['ok'] is False
    assert payload['ack_status']['manifest_count'] is None
    assert payload['ack_status']['generated_count'] is None
    assert payload['ack_status']['missing_count'] is None
    assert 'Repair Ack/ack_phrases.json' in payload['ack_status']['error']
    assert 'ack' not in payload['readiness']['checks']
    manifest.write_text(json.dumps({'phrases': [{'id': 'okay', 'text': 'Okay.', 'tags': ['global']}]}))
    recovered = client.get('/api/health').json()
    assert recovered['ack_status']['ok'] is True
    assert recovered['ack_status']['manifest_count'] == 1
    assert recovered['ack_status']['generated_count'] == 0
    assert recovered['ack_status']['missing_count'] == 1
    assert client.get('/api/acks/status').status_code == 200


@pytest.mark.parametrize('contents', ['{', '{"phrases":[{"text":"Okay.","tags":7}]}'])
def test_enabled_optional_ack_failure_does_not_abort_lifespan(manifest_runtime, monkeypatch, contents):
    from korina import app_factory
    import korina.config as config
    manifest, _ = manifest_runtime
    manifest.write_text(contents)
    saved = config.load_config()
    saved['ack_enabled'] = 'on'
    monkeypatch.setattr(config, 'load_config', lambda: saved)
    with TestClient(app_factory.create_app()) as client:
        assert client.get('/api/capabilities').status_code == 200
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/acks/status').status_code == 503
    assert not ack_service.state.ack.queue
    assert not ack_service.state.ack.worker_running
