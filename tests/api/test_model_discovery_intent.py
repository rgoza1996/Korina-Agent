"""Candidate credential forwarding requires browser-preflighted intent."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest


@pytest.mark.parametrize('field', ['llm_api_key_env', 'stt_llm_api_key_env'])
@pytest.mark.parametrize('headers', [
    {}, {'Origin': 'http://untrusted.test'},
    {'X-Korina-Intent': 'other-action'}, {'Sec-Fetch-Mode': 'no-cors'},
])
def test_explicit_credential_override_requires_intent_before_any_discovery(monkeypatch, client, field, headers):
    from korina.routes import models

    def unexpected():
        pytest.fail('Rejected credentialed discovery must not read configuration or admit work')

    monkeypatch.setattr(models, 'load_config', unexpected)
    response = client.get('/api/models', params={field: ' MODEL_API_KEY '}, headers=headers)
    assert response.status_code == 403
    assert response.json() == {'detail': 'Credentialed candidate discovery requires X-Korina-Intent: model-discovery'}
    assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('params', [
    {}, {'llm_api_key_env': '', 'stt_llm_api_key_env': '  '},
    {'llm_base_url': 'https://candidate.test/v1', 'stt_llm_base_url': 'https://audio.test/v1'},
])
def test_noncredential_overrides_and_saved_discovery_keep_get_compatibility(monkeypatch, client, params):
    from korina.routes import models

    calls = []
    monkeypatch.setattr(models, 'llm_models_for', lambda base, env, **kwargs: calls.append((base, env)) or [])
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    assert client.get('/api/models', params=params).status_code == 200
    assert len(calls) == 2
    if 'llm_base_url' in params:
        assert calls == [(params['llm_base_url'], ''), (params['stt_llm_base_url'], '')]


def test_configured_origin_can_preflight_and_discover_both_credentialed_candidates(monkeypatch, config_module):
    from korina.app_factory import create_app
    from korina.routes import models

    config_module.update_config_values({'converse': {'allowed_origins': ['http://trusted.test']}})
    calls = []
    monkeypatch.setattr(models, 'llm_models_for', lambda base, env, **kwargs: calls.append((base, env)) or [])
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    with TestClient(create_app()) as client:
        for origin, status in [('http://trusted.test', 200), ('http://untrusted.test', 400)]:
            response = client.options('/api/models', headers={
                'Origin': origin, 'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'x-korina-intent',
            })
            assert response.status_code == status
            assert response.headers.get('access-control-allow-origin') == (origin if status == 200 else None)
        response = client.get('/api/models', params={
            'llm_base_url': 'https://response.test/v1', 'llm_api_key_env': ' RESPONSE_KEY ',
            'stt_llm_base_url': 'https://audio.test/v1', 'stt_llm_api_key_env': ' AUDIO_KEY ',
        }, headers={'Origin': 'http://trusted.test', 'X-Korina-Intent': 'model-discovery'})
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://trusted.test'
        assert calls == [('https://response.test/v1', 'RESPONSE_KEY'), ('https://audio.test/v1', 'AUDIO_KEY')]
