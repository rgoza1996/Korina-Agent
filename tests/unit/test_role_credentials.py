"""Role-specific endpoint changes must not borrow a response provider secret."""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.parametrize('role_base,inherited', [
    ('', True),
    ('https://response.test/v1', True),
    ('https://response.test/v1/chat/completions/', True),
    ('https://audio.test/v1', False),
    ('https://response.test/other', False),
    ('https://response.test.evil/v1', False),
    ('http://response.test/v1', False),
])
def test_stt_response_credentials_stay_at_the_response_api(config_module, role_base, inherited):
    config = {
        'llm_base_url': 'https://response.test/v1/', 'llm_api_key_env': 'RESPONSE_KEY',
        'stt_llm_base_url': role_base, 'stt_llm_api_key_env': '',
    }
    assert config_module.config_stt_llm_api_env(config) == ('RESPONSE_KEY' if inherited else '')
    config['stt_llm_api_key_env'] = 'EXPLICIT_AUDIO_KEY'
    assert config_module.config_stt_llm_api_env(config) == 'EXPLICIT_AUDIO_KEY'


@pytest.mark.parametrize('role_base,inherited', [
    ('', True),
    ('https://response.test/v1', True),
    ('https://response.test/v1/chat/completions/', True),
    ('https://response.test/v1/messages', True),
    ('https://agent.test/v1', False),
    ('https://response.test/other', False),
    ('https://response.test.evil/v1', False),
    ('http://response.test/v1', False),
])
def test_agent_response_credentials_stay_at_the_response_api(config_module, monkeypatch, role_base, inherited):
    monkeypatch.setenv('RESPONSE_KEY', 'response-test-secret')
    config = {
        'llm_base_url': 'https://response.test/v1/', 'llm_api_key_env': 'RESPONSE_KEY',
        'agent_base_url': role_base, 'agent_provider': 'openai-compatible',
    }
    expected = {'Authorization': 'Bearer response-test-secret'} if inherited else {}
    assert config_module.agent_auth_headers(config) == expected
    config.update(agent_api_key='explicit-agent-secret', agent_provider='anthropic')
    assert config_module.agent_auth_headers(config) == {
        'x-api-key': 'explicit-agent-secret', 'anthropic-version': '2023-06-01',
    }


def test_saved_independent_stt_discovery_sends_no_response_authorization(
    monkeypatch, client, config_module, mock_provider_http,
):
    config_module.update_config_values({
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://response.test/v1',
        'llm_api_key_env': 'RESPONSE_KEY', 'stt_llm_provider': 'openai-compatible',
        'stt_llm_base_url': 'https://audio.test/v1', 'stt_llm_api_key_env': '',
    })
    monkeypatch.setenv('RESPONSE_KEY', 'response-test-secret')
    received = {}

    def respond(request):
        received[request.url.host] = request.headers.get('Authorization')
        return httpx.Response(200, json={'data': [{'id': 'model'}]})

    mock_provider_http(respond)
    assert client.get('/api/models').status_code == 200
    assert received == {'response.test': 'Bearer response-test-secret', 'audio.test': None}
