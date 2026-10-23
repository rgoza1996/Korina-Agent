"""Untrusted provider health must not break liveness or invent readiness."""
from __future__ import annotations

import httpx
import pytest

from korina.model_ids import MAX_MODEL_IDS, MAX_MODEL_ID_BYTES
from korina.routes import health


@pytest.mark.parametrize('payload', [
    {'status': []}, {'status': {'bad': True}},
    {'status': 'ok', 'loaded': 'false'}, {'ok': 'true'},
    {'ok': True, 'cuda_available': 'false'}, {'ok': True, 'device': []},
    {'ok': False, 'error': {'private': 'provider-marker'}},
    {'ok': True, 'device': '\ud800'}, {'unrelated': 'not a health document'},
])
def test_malformed_tts_health_preserves_liveness_and_fails_readiness(client, monkeypatch, payload):
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': True, 'loaded': True})
    monkeypatch.setattr(health, '_probe_tts_backend', lambda *args, **kwargs: (True, payload, None))
    response = client.get('/api/health')
    assert response.status_code == 200
    result = response.json()
    assert result['tts']['ok'] is False
    assert result['tts']['loaded'] is False
    assert result['tts']['cuda_available'] is False
    assert result['tts']['device'] is None
    assert result['tts']['error'].startswith('Invalid TTS health response:')
    assert len(result['tts']['error']) < 128
    assert 'provider-marker' not in response.text
    assert result['readiness']['checks']['tts'] is False
    ready = client.get('/api/ready')
    assert ready.status_code == 503
    assert ready.json()['checks']['tts'] is False


@pytest.mark.parametrize('payload', [
    {'ok': True, 'runtime_available': True, 'loaded': False},
    {'status': 'healthy', 'loaded': True, 'device': 'cpu'},
    {'status': 'ok', 'loaded': False, 'device': None},
])
def test_supported_lazy_and_loaded_health_signals_remain_ready(monkeypatch, payload):
    monkeypatch.setattr(health, '_probe_tts_backend', lambda *args, **kwargs: (True, payload, None))
    result = health._aggregate_tts({'tts_provider': 'kokoro'})
    assert result['ok'] is True
    assert result['loaded'] is payload['loaded']
    assert result['error'] is None


def test_tts_failure_keeps_bounded_error_and_can_recover(monkeypatch):
    payload = {'ok': False, 'error': 'native load failed ' + 'x' * 100_000}
    monkeypatch.setattr(health, '_probe_tts_backend', lambda *args, **kwargs: (True, payload, None))
    failed = health._aggregate_tts({'tts_provider': 'kokoro'})
    assert failed['ok'] is False
    assert failed['error'].startswith('native load failed ')
    assert len(failed['error']) == 1024
    payload.clear()
    payload.update({'ok': True, 'loaded': False})
    assert health._aggregate_tts({'tts_provider': 'kokoro'})['ok'] is True


def test_health_model_list_is_bounded_and_keeps_matching_alias_beyond_display_limit(mock_provider_http):
    ids = [f'fixture-{i:05}' for i in range(2000)] + ['/models/selected.gguf']
    mock_provider_http(lambda request: httpx.Response(200, json={'data': [{'id': mid} for mid in ids]}))
    result = health._probe_llm_models('https://fixture.test/v1', 'selected.gguf')
    assert result['ok'] and result['loaded']
    assert result['loaded_model'] == '/models/selected.gguf'
    assert result['loaded_model'] in result['loaded_models']
    assert len(result['loaded_models']) == MAX_MODEL_IDS
    assert result['models_info']['identifier_bytes'] <= MAX_MODEL_ID_BYTES
    assert result['models_info']['skipped_invalid'] == 0
    assert result['models_info']['truncated'] is True


def test_different_explicit_gguf_paths_do_not_make_the_selected_model_ready(mock_provider_http):
    mock_provider_http(lambda request: httpx.Response(200, json={'data': [{'id': '/other/model.gguf'}]}))
    assert health._probe_llm_models('https://fixture.test/v1', '/chosen/model.gguf')['loaded'] is False
    assert health._probe_llm_models('https://fixture.test/v1', '/other/model.gguf')['loaded'] is True
    assert health._probe_llm_models('https://fixture.test/v1', 'model.gguf')['loaded'] is True


def test_invalid_model_ids_do_not_break_health_json_or_report_loaded(mock_provider_http):
    # Use serialized content because HTTPX's convenience encoder rejects the surrogate first.
    payload = b'{"data":[{"id":"\\ud800"},{"id":false},{"id":"good"}]}'
    mock_provider_http(lambda request: httpx.Response(200, content=payload))
    result = health._probe_llm_models('https://fixture.test/v1', 'good')
    assert result['loaded'] is True
    assert result['loaded_models'] == ['good']
    assert result['models_info']['skipped_invalid'] == 2
