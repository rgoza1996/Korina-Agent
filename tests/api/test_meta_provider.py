"""Explicit Meta profile contracts, with all provider I/O kept in fixtures."""
from __future__ import annotations

import json

import httpx
import pytest

from korina.schemas import AgentStateRequest
from korina.services import agent_service, provider_manager
from korina.util.presets import provider_default_api_key_env

META_BASE = 'https://api.meta.ai/v1'


def select_meta(config_module, **changes):
    return config_module.update_config_values({
        'llm_provider': 'meta', 'llm_base_url': 'https://ignored.test/v1',
        'llm_api_key_env': '', 'lm_model': 'team-enabled-fixture',
        'stt_backend': 'whisper', 'agent_enabled': 'off', **changes,
    })


def test_meta_is_a_fixed_remote_profile_for_response_and_agent(client):
    response = client.get('/api/capabilities')
    assert response.status_code == 200
    registry = response.json()
    for section in ('providers', 'agent_providers'):
        meta = registry[section]['meta']
        assert meta['default_base_url'] == META_BASE
        assert meta['default_api_key_env'] == 'MODEL_API_KEY'
        assert meta['editable_base_url'] is False
        assert meta['manageable'] is False
        assert meta['is_local'] is False
        assert meta['model_sources'] == ['endpoint_loaded']


def test_meta_profile_normalizes_its_base_and_preserves_independent_roles(config_module):
    saved = select_meta(config_module, agent_provider='openai-compatible',
                        agent_base_url='https://independent.test/v1', agent_model='independent')
    assert saved['llm_base_url'] == META_BASE
    assert saved['agent_base_url'] == 'https://independent.test/v1'
    saved = config_module.update_config_values({'agent_provider': 'meta', 'agent_base_url': 'https://ignored.test/v1'})
    assert saved['agent_base_url'] == META_BASE
    assert config_module.agent_chat_url(saved) == f'{META_BASE}/chat/completions'
    assert config_module.agent_models_url(saved) == f'{META_BASE}/models'


@pytest.mark.parametrize('provider,base,expected', [
    ('meta', META_BASE, 'MODEL_API_KEY'),
    ('meta', f'{META_BASE}/chat/completions/', 'MODEL_API_KEY'),
    ('meta', 'https://independent.test/v1', ''),
    ('openai-compatible', META_BASE, ''),
    ('unknown', META_BASE, ''),
])
def test_meta_env_default_requires_the_explicit_profile_and_canonical_base(provider, base, expected):
    assert provider_default_api_key_env(provider, base) == expected


def test_meta_auth_uses_server_env_and_preserves_explicit_overrides(monkeypatch, config_module):
    monkeypatch.setenv('MODEL_API_KEY', 'meta-fixture-key')
    monkeypatch.setenv('CUSTOM_META_KEY', 'explicit-fixture-key')
    saved = select_meta(config_module, agent_provider='meta', agent_base_url=META_BASE)
    assert config_module.config_llm_api_env(saved) == 'MODEL_API_KEY'
    assert config_module.agent_auth_headers(saved) == {'Authorization': 'Bearer meta-fixture-key'}
    saved = config_module.update_config_values({'llm_api_key_env': 'CUSTOM_META_KEY'})
    assert config_module.config_llm_api_env(saved) == 'CUSTOM_META_KEY'
    assert config_module.agent_auth_headers(saved) == {'Authorization': 'Bearer explicit-fixture-key'}
    saved = config_module.update_config_values({'agent_api_key': 'direct-fixture-key'})
    assert config_module.agent_auth_headers(saved) == {'Authorization': 'Bearer direct-fixture-key'}
    saved = config_module.update_config_values({
        'agent_provider': 'openai-compatible', 'agent_base_url': 'https://independent.test/v1', 'agent_api_key': '',
    })
    assert config_module.agent_auth_headers(saved) == {}


def test_missing_meta_environment_key_does_not_create_credentials(monkeypatch, config_module):
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    saved = select_meta(config_module, agent_provider='meta')
    assert config_module.agent_auth_headers(saved) == {}


def test_meta_models_come_from_authenticated_discovery(monkeypatch, client, config_module, mock_provider_http):
    from korina.routes import models

    select_meta(config_module)
    monkeypatch.setenv('MODEL_API_KEY', 'meta-fixture-key')
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    calls = []

    def reply(request):
        calls.append(request)
        assert str(request.url) == f'{META_BASE}/models'
        assert request.headers['Authorization'] == 'Bearer meta-fixture-key'
        return httpx.Response(200, json={'data': [{'id': 'team-enabled-fixture'}, {'id': 'another-team-fixture'}]})

    mock_provider_http(reply)
    response = client.get('/api/models')
    assert response.status_code == 200
    result = response.json()
    assert set(result['llm_models']) == {'team-enabled-fixture', 'another-team-fixture'}
    assert result['llm_error'] is None
    assert len(calls) == 2  # response and inherited STT discovery share auth, not a model list
    assert all(not cap['supports_audio_input'] for cap in result['llm_models_capabilities'].values())


@pytest.mark.parametrize('params', [
    {'llm_base_url': 'https://candidate.test/v1'},
    {'llm_api_key_env': ''},
])
def test_unsaved_discovery_cannot_borrow_meta_default(monkeypatch, client, config_module, mock_provider_http, params):
    from korina.routes import models

    select_meta(config_module)
    monkeypatch.setenv('MODEL_API_KEY', 'meta-fixture-key')
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={'data': []})

    mock_provider_http(reply)
    assert client.get('/api/models', params=params).status_code == 200
    assert calls[0].headers.get('Authorization') is None
    assert calls[1].headers['Authorization'] == 'Bearer meta-fixture-key'


def test_meta_chat_sends_remembered_context_and_current_turn_once(monkeypatch, client, config_module, mock_provider_http):
    select_meta(config_module)
    monkeypatch.setenv('MODEL_API_KEY', 'meta-fixture-key')
    calls = []

    def reply(request):
        calls.append(request)
        assert str(request.url) == f'{META_BASE}/chat/completions'
        assert request.headers['Authorization'] == 'Bearer meta-fixture-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Your appointment is Tuesday.'}}]})

    mock_provider_http(reply)
    previous = [
        {'role': 'user', 'content': 'Remember my appointment is Tuesday.'},
        {'role': 'assistant', 'content': 'I will keep that in this conversation.'},
    ]
    response = client.post('/api/chat', json={
        'message': 'When is my appointment?', 'history': previous,
        'system': 'Use the remembered conversation facts.', 'max_tokens': 256, 'reasoning': 'off',
    })
    assert response.status_code == 200
    assert response.json()['reply'] == 'Your appointment is Tuesday.'
    payload = json.loads(calls[0].content)
    assert set(payload) == {'model', 'messages', 'temperature', 'max_tokens', 'stream'}
    assert payload['model'] == 'team-enabled-fixture'
    assert payload['max_tokens'] == 256
    assert payload['stream'] is False
    assert payload['messages'] == [
        {'role': 'system', 'content': 'Use the remembered conversation facts.'},
        *previous, {'role': 'user', 'content': 'When is my appointment?'},
    ]


def test_generic_meta_hostname_keeps_generic_reasoning_and_no_implicit_key(monkeypatch, client, config_module, mock_provider_http):
    config_module.update_config_values({
        'llm_provider': 'openai-compatible', 'llm_base_url': META_BASE, 'llm_api_key_env': '',
    })
    monkeypatch.setenv('MODEL_API_KEY', 'must-not-be-borrowed')
    calls = []

    def reply(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ready.'}}]})

    mock_provider_http(reply)
    assert client.post('/api/chat', json={'message': 'Hello', 'reasoning': 'on'}).status_code == 200
    assert calls[0].headers.get('Authorization') is None
    assert json.loads(calls[0].content)['reasoning'] == 'on'


def test_independent_meta_agent_uses_existing_chat_and_discovery(monkeypatch, client, config_module, mock_provider_http):
    config_module.update_config_values({
        'agent_enabled': 'on', 'agent_provider': 'meta', 'agent_api_key': '', 'agent_model': 'agent-team-fixture',
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://independent.test/v1', 'llm_api_key_env': '',
    })
    monkeypatch.setenv('MODEL_API_KEY', 'meta-fixture-key')
    calls = []

    def reply(request):
        calls.append(request)
        assert request.headers['Authorization'] == 'Bearer meta-fixture-key'
        if request.method == 'GET':
            assert str(request.url) == f'{META_BASE}/models'
            return httpx.Response(200, json={'data': [{'id': 'agent-team-fixture'}]})
        assert str(request.url) == f'{META_BASE}/chat/completions'
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Priority: normal\nAppointment: Tuesday.'}}]})

    mock_provider_http(reply)
    result = client.get('/api/agent/models')
    assert result.status_code == 200
    assert result.json()['models'] == ['agent-team-fixture']
    report = agent_service.generate_agent_state_report(AgentStateRequest(transcript=[
        {'role': 'user', 'content': 'My appointment is Tuesday.'},
    ]))
    assert report == 'Priority: normal\nAppointment: Tuesday.'
    payload = json.loads(calls[1].content)
    assert set(payload) == {'model', 'messages', 'temperature', 'max_tokens', 'stream'}
    assert payload['model'] == 'agent-team-fixture'
    assert json.loads(payload['messages'][1]['content'])['recent_transcript'] == [
        {'role': 'user', 'content': 'My appointment is Tuesday.'},
    ]


def test_meta_health_uses_the_same_scoped_credentials(monkeypatch, client, config_module):
    from korina.routes import health

    select_meta(config_module, agent_provider='meta', agent_model='team-enabled-fixture')
    monkeypatch.setenv('MODEL_API_KEY', 'meta-fixture-key')
    calls = []

    def probe(base, model, **kwargs):
        calls.append((base, model, kwargs))
        return {'ok': True, 'loaded': True, 'error': None}

    monkeypatch.setattr(health, '_probe_llm_models', probe)
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    response = client.get('/api/health')
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0] == (META_BASE, 'team-enabled-fixture', {
        'headers': {'Authorization': 'Bearer meta-fixture-key'}, 'models_url': f'{META_BASE}/models',
    })
    assert response.json()['response_llm_chat_url'] == f'{META_BASE}/chat/completions'


def test_meta_activation_never_starts_a_local_provider(monkeypatch, client, config_module):
    calls = []
    monkeypatch.setattr(provider_manager.provider_processes, 'user_processes', lambda: iter(()))

    def stop():
        calls.append('stop')
        return provider_manager.LifecycleActions()

    def forbidden_start(*args, **kwargs):
        pytest.fail('A remote Meta profile must not start a local provider')

    for name in ('stop_llama_server', 'stop_lmstudio_server', 'stop_ollama', '_kill_stray_llama_servers'):
        monkeypatch.setattr(provider_manager, name, lambda *args, **kwargs: stop())
    for name in ('start_llama_server', 'start_lmstudio_server', 'start_ollama'):
        monkeypatch.setattr(provider_manager, name, forbidden_start)
    response = client.post('/api/llm/provider/activate', json={'provider': 'meta', 'model': 'team-enabled-fixture'})
    assert response.status_code == 200
    result = response.json()
    assert result['saved']['llm_provider'] == 'meta'
    assert result['saved']['llm_base_url'] == META_BASE
    assert result['saved']['lm_model'] == 'team-enabled-fixture'
    assert result['activation']['started'] == []
    assert calls  # existing role-aware teardown policy still ran
