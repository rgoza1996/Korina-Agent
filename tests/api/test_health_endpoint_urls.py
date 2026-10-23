"""Diagnostic links must name the same resolved URLs that health actually probes."""
from __future__ import annotations

import httpx
import pytest

from korina.routes import health


@pytest.mark.parametrize('response_provider,agent_provider,response_models,agent_models', [
    ('openai-compatible', 'anthropic', 'https://response.fixture/v1/models', 'https://agent.fixture/v1/models'),
    ('meta', 'meta', 'https://api.meta.ai/v1/models', 'https://api.meta.ai/v1/models'),
])
def test_health_exposes_the_actual_models_urls_without_replacing_display_bases(
    client, monkeypatch, mock_provider_http, response_provider, agent_provider, response_models, agent_models,
):
    config = {
        'llm_provider': response_provider, 'llm_base_url': 'https://response.fixture/v1/chat/completions',
        'llm_api_key_env': 'KORINA_ENDPOINTS_TEST_KEY', 'lm_model': 'fixture-model',
        'stt_llm_provider': 'openai-compatible', 'stt_llm_base_url': 'https://stt.fixture/audio/v2/chat/completions',
        'stt_llm_api_key_env': 'KORINA_ENDPOINTS_TEST_KEY', 'stt_llm_model': 'fixture-model',
        'agent_provider': agent_provider, 'agent_base_url': 'https://agent.fixture/v1/messages',
        'agent_api_key': 'fixture-agent-key', 'agent_model': 'fixture-model',
        'tts_provider': 'kokoro', 'tts_base_url': 'https://tts.fixture',
    }
    monkeypatch.setenv('KORINA_ENDPOINTS_TEST_KEY', 'fixture-response-key')
    monkeypatch.setattr(health, 'load_config', lambda: config)
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    seen = []

    def provider(request):
        seen.append((str(request.url), dict(request.headers)))
        if request.url.host == 'tts.fixture':
            return httpx.Response(200, json={'ok': True, 'loaded': False})
        return httpx.Response(200, json={'data': [{'id': 'fixture-model'}]})

    mock_provider_http(provider)
    response = client.get('/api/health')
    assert response.status_code == 200
    body = response.json()
    expected = {response_models, 'https://stt.fixture/audio/v2/models', agent_models}
    assert {body[key]['models_url'] for key in ('response_llm_load', 'multimodal_stt_load', 'agent_load')} == expected
    assert {url for url, _ in seen if not url.startswith('https://tts.fixture/')} == expected
    assert body['response_llm_base_url'] == (
        'https://api.meta.ai/v1' if response_provider == 'meta' else config['llm_base_url']
    )
    assert body['agent_base_url'] == (
        'https://api.meta.ai/v1' if agent_provider == 'meta' else config['agent_base_url']
    )
    assert body['multimodal_stt_base_url'] == 'https://stt.fixture/audio/v2'
    assert all(body[key]['loaded'] for key in ('response_llm_load', 'multimodal_stt_load', 'agent_load'))
    assert any(headers.get('authorization') == 'Bearer fixture-response-key' for _, headers in seen)
    agent_auth = 'x-api-key' if agent_provider == 'anthropic' else 'authorization'
    expected_agent_key = 'fixture-agent-key' if agent_provider == 'anthropic' else 'Bearer fixture-agent-key'
    assert any(headers.get(agent_auth) == expected_agent_key for _, headers in seen)


@pytest.mark.parametrize('provider,configured,url,status', [
    ('kokoro', '', 'https://tts.fixture/health', 200),
    ('openai-compatible', 'https://tts.fixture/status/ready', 'https://tts.fixture/status/ready', 200),
    ('openai-compatible', 'https://tts.fixture/status/ready', 'https://tts.fixture/status/ready', 503),
    ('openai-compatible', '', None, None),
])
def test_tts_health_reports_only_the_actual_probe_url(mock_provider_http, provider, configured, url, status):
    seen = []

    def provider_http(request):
        seen.append(str(request.url))
        return httpx.Response(status, json={'ok': True, 'loaded': False})

    mock_provider_http(provider_http)
    result = health._aggregate_tts({
        'tts_provider': provider, 'tts_base_url': 'https://tts.fixture', 'tts_health_url': configured,
    })
    assert result['health_url'] == url
    assert seen == ([url] if url else [])
    assert result['base_url'] == 'https://tts.fixture'
    if url is None:
        assert result['status'] == 'unverified' and result['probe_supported'] is False
    else:
        assert result['ok'] is (status == 200)
        if status != 200:
            assert result['error'] == f'HTTP {status} from {url}'
