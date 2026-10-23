from __future__ import annotations

import time
import httpx

from korina.routes import health


def test_health_probes_shared_roles_once_and_parallel_with_tts(client, monkeypatch):
    calls = []
    monkeypatch.setattr(health, 'load_config', lambda: {'lm_model': 'demo', 'agent_enabled': 'off'})

    def probe(base, expected, timeout=1.5, headers=None, models_url=None):
        calls.append(base)
        time.sleep(0.1)
        return {'ok': True, 'loaded': True, 'loaded_models': [expected], 'error': None}

    def tts(config):
        time.sleep(0.1)
        return {'ok': True, 'loaded': False}

    monkeypatch.setattr(health, '_probe_llm_models', probe)
    monkeypatch.setattr(health, '_aggregate_tts', tts)
    started = time.monotonic()
    body = client.get('/api/health').json()
    assert len(calls) == 1
    assert time.monotonic() - started < 0.8
    assert body['ok'] is True
    assert body['readiness']['checks']['agent'] is True


def test_readiness_distinguishes_reachable_backend_from_voice_stack(client, monkeypatch):
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': False, 'loaded': False, 'error': 'offline'})
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': False, 'loaded': False})
    assert client.get('/api/health').status_code == 200
    result = client.get('/api/ready')
    assert result.status_code == 503
    assert result.json()['ready'] is False
    assert result.json()['checks']['response_llm'] is False


def test_probe_uses_configured_base_path_auth_and_exact_model(mock_provider_http):
    requests = []

    def request_handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'demo-other'}, {'id': '/models/demo.gguf'}]})

    mock_provider_http(request_handler)
    result = health._probe_llm_models('https://provider.test/custom/v2', 'demo', headers={'Authorization': 'Bearer example'})
    assert result['loaded'] is False
    assert str(requests[0].url) == 'https://provider.test/custom/v2/models'
    assert requests[0].headers.get('Authorization') == 'Bearer example'
    assert health._probe_llm_models('https://provider.test/v1', 'demo.gguf')['loaded'] is True
    health._probe_llm_models('https://provider.test/v1/chat/completions', 'demo')
    assert str(requests[-1].url) == 'https://provider.test/v1/models'


def test_bad_provider_urls_and_nonobject_tts_are_degraded(mock_provider_http):
    assert health._probe_llm_models('not a URL', 'demo')['ok'] is False
    assert health._probe_tts_backend('not a URL')[0] is False
    mock_provider_http(lambda request: httpx.Response(200, json=[]))
    ok, _, error = health._probe_tts_backend('https://tts.test')
    assert not ok and error


def test_authenticated_roles_do_not_share_probes_with_different_credentials(client, monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'converse-key')
    monkeypatch.setattr(health, 'load_config', lambda: {
        'lm_model': 'demo', 'agent_model': 'demo', 'llm_api_key_env': 'TEST_LLM_KEY',
        'agent_api_key': 'agent-key', 'agent_provider': 'openai-compatible',
    })
    headers_seen = []

    def probe(base, model, timeout=1.5, headers=None, models_url=None):
        headers_seen.append(headers)
        return {'ok': True, 'loaded': True}

    monkeypatch.setattr(health, '_probe_llm_models', probe)
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    assert client.get('/api/health').status_code == 200
    assert {'Authorization': 'Bearer converse-key'} in headers_seen
    assert {'Authorization': 'Bearer agent-key'} in headers_seen


def test_nonstandard_tts_health_is_unverified_instead_of_offline(monkeypatch):
    def should_not_probe(*args, **kwargs):
        raise AssertionError('Provider has no standard health endpoint')

    monkeypatch.setattr(health, '_probe_tts_backend', should_not_probe)
    result = health._aggregate_tts({'tts_provider': 'openai-compatible', 'tts_base_url': 'https://tts.test/v1'})
    assert result['status'] == 'unverified'
    assert result['probe_supported'] is False


def test_tts_http_success_with_runtime_failure_is_not_ready(monkeypatch):
    monkeypatch.setattr(health, '_probe_tts_backend', lambda *args, **kwargs: (
        True, {'ok': False, 'error': 'kokoro is not installed'}, None,
    ))
    result = health._aggregate_tts({'tts_provider': 'kokoro'})
    assert result['ok'] is False
    assert result['error'] == 'kokoro is not installed'
