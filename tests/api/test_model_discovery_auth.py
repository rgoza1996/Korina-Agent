"""Unsaved discovery candidates must never borrow another endpoint's auth."""
from __future__ import annotations

import httpx

import pytest


@pytest.mark.parametrize('params,expected', [
    ({}, ('Bearer response-test-token', 'Bearer audio-test-token')),
    ({'llm_base_url': 'https://candidate.test/v1'}, (None, 'Bearer audio-test-token')),
    ({'llm_base_url': 'https://candidate.test/v1', 'stt_llm_base_url': 'https://candidate-audio.test/v1'}, (None, None)),
    ({'llm_api_key_env': '', 'stt_llm_api_key_env': ''}, (None, None)),
    ({'llm_base_url': 'https://candidate.test/v1', 'llm_api_key_env': '',
      'stt_llm_base_url': 'https://candidate-audio.test/v1', 'stt_llm_api_key_env': ''}, (None, None)),
    ({'llm_base_url': 'https://saved.test/v1/chat/completions/',
      'stt_llm_base_url': 'https://saved-audio.test/v1/chat/completions/'}, ('Bearer response-test-token', 'Bearer audio-test-token')),
    ({'llm_base_url': 'https://candidate.test/v1', 'llm_api_key_env': 'EXPLICIT_KEY',
      'stt_llm_base_url': 'https://candidate-audio.test/v1', 'stt_llm_api_key_env': 'EXPLICIT_KEY'}, ('Bearer explicit-test-token', 'Bearer explicit-test-token')),
])
def test_discovery_auth_is_scoped_to_its_endpoint(monkeypatch, client, config_module, params, expected, mock_provider_http):
    from korina.routes import models
    from korina.services import model_catalog

    config_module.update_config_values({
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://saved.test/v1',
        'llm_api_key_env': 'RESPONSE_KEY', 'stt_llm_provider': 'openai-compatible',
        'stt_llm_base_url': 'https://saved-audio.test/v1', 'stt_llm_api_key_env': 'AUDIO_KEY',
    })
    monkeypatch.setenv('RESPONSE_KEY', 'response-test-token')
    monkeypatch.setenv('AUDIO_KEY', 'audio-test-token')
    monkeypatch.setenv('EXPLICIT_KEY', 'explicit-test-token')
    calls = []

    def request_handler(request):
        calls.append((str(request.url), request.headers.get('Authorization')))
        return httpx.Response(200, json={'data': []})

    mock_provider_http(request_handler)
    monkeypatch.setattr(models, 'llm_models_for', model_catalog.llm_models_for)
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    response = client.get('/api/models', params=params)
    assert response.status_code == 200
    assert len(calls) == 2
    assert tuple(auth for _, auth in calls) == expected
    assert all(url.endswith('/models') for url, _ in calls)


def test_default_stt_discovery_still_inherits_saved_response_auth(monkeypatch, client, config_module):
    from korina.routes import models

    config_module.update_config_values({
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://saved.test/v1',
        'llm_api_key_env': 'RESPONSE_KEY', 'stt_llm_provider': '',
        'stt_llm_base_url': '', 'stt_llm_api_key_env': '',
    })
    calls = []
    monkeypatch.setattr(models, 'llm_models_for', lambda base, env, **kwargs: calls.append((base, env)) or [])
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    assert client.get('/api/models').status_code == 200
    assert calls == [('https://saved.test/v1', 'RESPONSE_KEY')] * 2
