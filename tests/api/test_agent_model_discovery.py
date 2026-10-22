"""Unsaved agent discovery resolves credentials without changing runtime settings."""
from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def saved_discovery(config_module):
    config_module.update_config_values({
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://response.test/v1',
        'llm_api_key_env': 'RESPONSE_KEY', 'lm_model': 'response-model',
        'agent_provider': 'openai-compatible', 'agent_base_url': 'https://saved.test/v1',
        'agent_api_key': 'saved-agent-key', 'agent_model': 'saved-model',
    })
    return config_module.CONFIG_PATH.read_bytes()


def test_candidate_discovery_uses_unsaved_endpoint_without_writes_and_get_stays_compatible(
    client, config_module, saved_discovery, mock_provider_http,
):
    calls = []

    def respond(request):
        calls.append(request)
        model = 'candidate-model' if request.url.host == 'candidate.test' else 'saved-model'
        return httpx.Response(200, json={'data': [{'id': model}]})

    mock_provider_http(respond)
    candidate = client.post('/api/agent/models', json={
        'agent_base_url': 'https://candidate.test/v1', 'agent_api_key': '',
    })
    assert candidate.status_code == 200
    assert candidate.json()['models'] == ['candidate-model']
    assert str(calls[0].url) == 'https://candidate.test/v1/models'
    assert calls[0].headers.get('Authorization') is None
    assert config_module.CONFIG_PATH.read_bytes() == saved_discovery

    saved = client.get('/api/agent/models').json()
    assert {key: value for key, value in saved.items() if key != 'catalog_info'} == {
        'ok': True, 'models': ['saved-model'], 'provider': 'openai-compatible',
        'default': 'saved-model', 'error': None,
    }
    assert saved['catalog_info']['returned'] == 1
    assert str(calls[1].url) == 'https://saved.test/v1/models'
    assert calls[1].headers.get('Authorization') == 'Bearer saved-agent-key'
    assert all(request.method == 'GET' for request in calls)
    assert config_module.CONFIG_PATH.read_bytes() == saved_discovery


@pytest.mark.parametrize('provider,role_url,key,expected', [
    ('openai-compatible', 'https://candidate.test/v1', '', ('Bearer candidate-response-secret', None)),
    ('anthropic', 'https://candidate.test/v1/messages', '', (None, 'candidate-response-secret')),
    ('openai-compatible', 'https://independent.test/v1', 'own-agent-secret', ('Bearer own-agent-secret', None)),
])
def test_candidate_uses_current_response_fallback_or_its_own_key(
    client, monkeypatch, config_module, saved_discovery, mock_provider_http,
    provider, role_url, key, expected,
):
    monkeypatch.setenv('CANDIDATE_RESPONSE_KEY', 'candidate-response-secret')
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [{'id': 'candidate-model'}]})

    mock_provider_http(respond)
    result = client.post('/api/agent/models', json={
        'agent_provider': provider, 'agent_base_url': role_url, 'agent_api_key': key,
        'llm_base_url': 'https://candidate.test/v1', 'llm_api_key_env': 'CANDIDATE_RESPONSE_KEY',
    })
    assert result.json()['provider'] == provider
    assert result.json()['models'] == ['candidate-model']
    assert len(calls) == 1
    assert str(calls[0].url).endswith('/v1/models')
    assert (calls[0].headers.get('Authorization'), calls[0].headers.get('x-api-key')) == expected
    assert 'candidate-response-secret' not in result.text
    assert 'own-agent-secret' not in result.text
    assert config_module.CONFIG_PATH.read_bytes() == saved_discovery


def test_candidate_failure_does_not_reflect_an_upstream_echo_of_its_key(
    client, config_module, saved_discovery, mock_provider_http,
):
    mock_provider_http(lambda request: httpx.Response(401, text='Invalid key: submitted-agent-secret'))
    result = client.post('/api/agent/models', json={'agent_api_key': 'submitted-agent-secret'})
    assert result.status_code == 200
    assert result.json()['ok'] is False
    assert result.json()['error'] == 'Agent model discovery HTTP 401'
    assert 'submitted-agent-secret' not in result.text
    assert config_module.CONFIG_PATH.read_bytes() == saved_discovery


@pytest.mark.parametrize('candidate,expected_url,expected_auth', [
    ({'agent_base_url': 'https://new.test/v1'}, 'https://new.test/v1/models', None),
    ({'agent_base_url': 'https://saved.test/v1/chat/completions/'},
     'https://saved.test/v1/models', 'Bearer saved-agent-key'),
    ({'agent_base_url': '', 'llm_base_url': 'https://new.test/v1',
      'llm_api_key_env': 'CANDIDATE_RESPONSE_KEY'},
     'https://new.test/v1/models', 'Bearer current-response-secret'),
])
def test_omitted_candidate_direct_key_is_scoped_to_the_actual_saved_api_url(
    client, monkeypatch, config_module, saved_discovery, mock_provider_http,
    candidate, expected_url, expected_auth,
):
    monkeypatch.setenv('CANDIDATE_RESPONSE_KEY', 'current-response-secret')
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'data': [{'id': 'candidate-model'}]})

    mock_provider_http(respond)
    result = client.post('/api/agent/models', json=candidate)
    assert result.status_code == 200
    assert result.json()['models'] == ['candidate-model']
    assert len(calls) == 1
    assert str(calls[0].url) == expected_url
    assert calls[0].headers.get('Authorization') == expected_auth
    assert 'saved-agent-key' not in result.text
    assert config_module.CONFIG_PATH.read_bytes() == saved_discovery


@pytest.mark.parametrize('candidate', [
    {'agent_api_key': {'secret': 'do-not-echo'}},
    {'agent_api_key': 'do-not-echo' * 1000},
    {'agent_base_url': None},
    {'agent_provider': 123},
    {'llm_api_key_env': []},
    {'unexpected': 'do-not-echo'},
])
def test_invalid_candidate_is_rejected_without_echoing_input_or_making_requests(
    client, config_module, saved_discovery, mock_provider_http, candidate,
):
    def unexpected(request):
        raise AssertionError('Invalid candidate must not query a provider')

    mock_provider_http(unexpected)
    result = client.post('/api/agent/models', json=candidate)
    assert result.status_code == 422
    assert 'do-not-echo' not in result.text
    assert all('input' not in error for error in result.json()['detail'])
    assert config_module.CONFIG_PATH.read_bytes() == saved_discovery
