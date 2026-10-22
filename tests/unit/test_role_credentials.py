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
    ('https://response.test/v1/messages', False),
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


@pytest.mark.parametrize('provider,configured_path,api_path,inherited,own_key', [
    ('openai-compatible', '/v1', '/v1', True, ''),
    ('openai-compatible', '/v1/chat/completions/', '/v1', True, ''),
    ('openai-compatible', '/v1/messages', '/v1/messages', False, ''),
    ('anthropic', '/v1', '/v1', True, ''),
    ('anthropic', '/v1/messages/', '/v1', True, ''),
    ('anthropic', '/v1/chat/completions', '/v1/chat/completions', False, ''),
    ('openai-compatible', '/v1/messages', '/v1/messages', False, 'own-agent-key'),
    ('anthropic', '/v1/chat/completions', '/v1/chat/completions', False, 'own-agent-key'),
])
def test_agent_chat_discovery_and_health_use_one_provider_aware_api_base(
    monkeypatch, client, config_module, mock_provider_http,
    provider, configured_path, api_path, inherited, own_key,
):
    from korina.routes import health
    from korina.schemas import AgentStateRequest
    from korina.services import agent_service

    monkeypatch.setenv('RESPONSE_KEY', 'response-secret')
    config = {
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://response.test/v1',
        'llm_api_key_env': 'RESPONSE_KEY', 'lm_model': 'fixture',
        'agent_enabled': 'on', 'agent_provider': provider, 'agent_model': 'fixture',
        'agent_base_url': 'https://response.test' + configured_path,
        'agent_api_key': own_key,
    }
    monkeypatch.setattr(config_module, 'load_config', lambda: config)
    monkeypatch.setattr(agent_service, 'load_config', lambda: config)
    monkeypatch.setattr(health, 'load_config', lambda: config)
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'fixture'}]})
        body = {'content': [{'type': 'text', 'text': 'Agent report'}]} if provider == 'anthropic' else {
            'choices': [{'message': {'content': 'Agent report'}}],
        }
        return httpx.Response(200, json=body)

    mock_provider_http(respond)
    models_url = 'https://response.test' + api_path + '/models'
    chat_url = 'https://response.test' + api_path + (
        '/messages' if provider == 'anthropic' else '/chat/completions'
    )
    assert agent_service.agent_model_choices() == ['fixture']
    assert agent_service.generate_agent_state_report(AgentStateRequest()) == 'Agent report'
    assert [(request.method, str(request.url)) for request in calls] == [
        ('GET', models_url), ('POST', chat_url),
    ]
    key = own_key or ('response-secret' if inherited else '')
    expected_bearer = f'Bearer {key}' if key and provider != 'anthropic' else None
    expected_native = key if key and provider == 'anthropic' else None
    for request in calls:
        assert request.headers.get('Authorization') == expected_bearer
        assert request.headers.get('x-api-key') == expected_native

    calls.clear()
    body = client.get('/api/health').json()
    assert body['agent_base_url'] == config['agent_base_url'].rstrip('/')
    assert body['agent_load']['base_url'] == body['agent_base_url']
    assert body['agent_chat_url'] == chat_url
    assert body['agent_load']['loaded'] is True
    assert any(
        str(request.url) == models_url
        and request.headers.get('Authorization') == expected_bearer
        and request.headers.get('x-api-key') == expected_native
        for request in calls
    )


@pytest.mark.parametrize('provider,role_path', [
    ('openai-compatible', '/v1/messages'),
    ('anthropic', '/v1/chat/completions'),
])
def test_wrong_agent_suffix_cannot_claim_readiness_from_the_response_api(
    monkeypatch, client, config_module, mock_provider_http, provider, role_path,
):
    from korina.routes import health

    monkeypatch.setenv('RESPONSE_KEY', 'response-secret')
    config = {
        'llm_base_url': 'https://response.test/v1', 'llm_api_key_env': 'RESPONSE_KEY',
        'lm_model': 'fixture', 'agent_model': 'fixture', 'agent_enabled': 'on',
        'agent_provider': provider, 'agent_base_url': 'https://response.test' + role_path,
    }
    monkeypatch.setattr(health, 'load_config', lambda: config)
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'fixture'}]})
        return httpx.Response(404, json={'detail': 'No models API at this base'})

    mock_provider_http(respond)
    body = client.get('/api/health').json()
    assert body['response_llm_load']['loaded'] is True
    assert body['agent_load']['ok'] is False
    assert body['readiness']['checks']['agent'] is False
    request = next(item for item in calls if item.url.path == role_path + '/models')
    assert request.headers.get('Authorization') is None
    assert request.headers.get('x-api-key') is None


def test_equivalent_full_agent_endpoint_shares_health_probe_without_changing_display(
    monkeypatch, client, mock_provider_http,
):
    from korina.routes import health

    monkeypatch.setenv('RESPONSE_KEY', 'response-secret')
    config = {
        'llm_base_url': 'https://response.test/v1', 'llm_api_key_env': 'RESPONSE_KEY',
        'lm_model': 'fixture', 'agent_model': 'fixture',
        'agent_base_url': 'https://response.test/v1/chat/completions/',
        'agent_provider': 'openai-compatible',
    }
    monkeypatch.setattr(health, 'load_config', lambda: config)
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [{'id': 'fixture'}]})

    mock_provider_http(respond)
    body = client.get('/api/health').json()
    assert len(calls) == 1
    assert str(calls[0].url) == 'https://response.test/v1/models'
    assert body['agent_load']['base_url'] == 'https://response.test/v1/chat/completions'
    assert body['response_llm_load']['base_url'] == 'https://response.test/v1'
