"""Role settings cannot enter the destructive window of provider operations."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from korina import config
from korina.routes import model_roots, providers
from korina.services.provider_manager import provider_activation_lock


@pytest.mark.parametrize('change', [
    {'agent_enabled': 'on'},
    {'agent_base_url': 'http://127.0.0.1:9000/v1'},
    {'stt_backend': 'llm', 'stt_llm_base_url': 'http://127.0.0.1:8080/v1', 'stt_llm_model': 'demo'},
])
def test_held_refresh_blocks_role_changes_but_allows_unrelated_full_form_saves(monkeypatch, client, change):
    config.update_config_values({
        'llm_provider': 'llama.cpp', 'lm_model': '/models/demo.gguf',
        'agent_enabled': 'off', 'stt_backend': 'whisper',
    })
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(model_roots, '_resolve_llama_cpp_model_id', lambda model: model)
    monkeypatch.setattr(model_roots, 'discover_local_gguf_models', lambda: [])
    monkeypatch.setattr(model_roots, 'prepare_provider', lambda provider, cfg, model, **kwargs: (model, {}, []))

    def start(*args, **kwargs):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(model_roots, 'start_llama_server', start)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://korina.test') as http:
            refresh = asyncio.create_task(http.post('/api/llm/llama/refresh'))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                before = config.CONFIG_PATH.read_bytes()
                blocked = await http.post('/api/config', json=change)
                assert blocked.status_code == 409
                assert 'activation or refresh is busy' in blocked.json()['detail']
                assert config.CONFIG_PATH.read_bytes() == before
                assert (await http.post('/api/config', json={'voice': 'af_bella'})).status_code == 200
                full_form = {**config.load_config(), 'speed': 1.25}
                assert (await http.post('/api/config', json=full_form)).status_code == 200
                assert config.load_config()['voice'] == 'af_bella'
                assert config.load_config()['speed'] == 1.25
                assert not provider_activation_lock.acquire(blocking=False)
                assert (await http.get('/api/capabilities')).status_code == 200
            finally:
                release.set()
                completed = await refresh
            assert completed.status_code == 200
            assert completed.json()['restarted'] is True
            after = await http.post('/api/config', json=change)
            assert after.status_code == 200
            assert all(config.load_config()[key] == value for key, value in change.items())

    asyncio.run(exercise())


@pytest.mark.parametrize('field,value', [
    ('llm_provider', 'ollama'), ('llm_base_url', 'https://new-response.test/v1'),
    ('lm_model', 'new-response'), ('llm_reasoning', 'on'), ('llm_api_key_env', 'NEW_RESPONSE_KEY'),
    ('stt_backend', 'llm'), ('stt_llm_provider', 'ollama'),
    ('stt_llm_base_url', 'https://new-audio.test/v1'), ('stt_llm_model', 'new-audio'),
    ('stt_llm_api_key_env', 'NEW_AUDIO_KEY'),
    ('agent_enabled', 'on'), ('agent_provider', 'anthropic'),
    ('agent_base_url', 'https://new-agent.test/v1'), ('agent_model', 'new-agent'),
    ('agent_api_key', 'new-direct-key'),
])
def test_busy_lifecycle_rejects_every_implemented_role_change_without_writing(client, field, value):
    config.update_config_values({'llm_provider': 'openai-compatible', 'agent_enabled': 'off'})
    before = config.CONFIG_PATH.read_bytes()
    assert provider_activation_lock.acquire(blocking=False)
    try:
        response = client.post('/api/config', json={field: value, 'voice': 'af_bella'})
        assert response.status_code == 409, response.text
        if field == 'agent_api_key':
            assert value not in response.text
        assert config.CONFIG_PATH.read_bytes() == before
    finally:
        provider_activation_lock.release()


def test_busy_lifecycle_accepts_equivalent_inherited_settings_and_unrelated_changes(client):
    config.update_config_values({'llm_provider': 'openai-compatible', 'lm_model': 'demo', 'agent_enabled': 'off'})
    saved = config.load_config()
    config.save_config({**saved, 'agent_base_url': '', 'agent_model': '',
                        'stt_backend': 'llm', 'stt_llm_provider': '', 'stt_llm_base_url': '', 'stt_llm_model': ''})
    assert provider_activation_lock.acquire(blocking=False)
    try:
        response = client.post('/api/config', json={**config.load_config(), 'voice': 'af_bella'})
        assert response.status_code == 200, response.text
        assert response.json()['voice'] == 'af_bella'
        assert config.config_stt_llm_model(response.json()) == 'demo'
        assert response.json()['agent_model'] == 'demo'
    finally:
        provider_activation_lock.release()


@pytest.mark.parametrize('operation', ['activation', 'refresh'])
@pytest.mark.parametrize('previous_env,next_env', [('', 'MODEL_API_KEY'), ('MODEL_API_KEY', '')])
def test_held_lifecycle_accepts_equivalent_meta_environment_names(
    monkeypatch, client, operation, previous_env, next_env,
):
    monkeypatch.setenv('MODEL_API_KEY', 'synthetic-fixture-only')
    saved = config.update_config_values({
        'llm_provider': 'meta', 'llm_api_key_env': previous_env, 'lm_model': 'fixture-model',
        'stt_backend': 'llm', 'stt_llm_provider': '', 'stt_llm_base_url': '', 'stt_llm_model': '',
        'stt_llm_api_key_env': '', 'agent_enabled': 'off', 'agent_provider': 'openai-compatible',
        'agent_base_url': 'https://api.meta.ai/v1', 'agent_api_key': '',
    })
    assert config.config_llm_api_env(saved) == config.config_stt_llm_api_env(saved) == 'MODEL_API_KEY'
    original_headers = config.agent_auth_headers(saved)
    assert original_headers == {'Authorization': 'Bearer synthetic-fixture-only'}
    entered, release = threading.Event(), threading.Event()

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {'provider': 'meta'} if operation == 'activation' else []

    if operation == 'activation':
        monkeypatch.setattr(providers, 'activate_llm_provider', hold)
        endpoint, arguments = '/api/llm/provider/activate', {'json': {'provider': 'meta', 'model': 'fixture-model'}}
    else:
        monkeypatch.setattr(model_roots, '_resolve_llama_cpp_model_id', lambda model: model)
        monkeypatch.setattr(model_roots, 'discover_local_gguf_models', hold)
        endpoint, arguments = '/api/llm/llama/refresh', {}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://korina.test') as http:
            pending = asyncio.create_task(http.post(endpoint, **arguments))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await http.post('/api/config', json={'llm_api_key_env': next_env, 'voice': 'af_bella'})
                assert response.status_code == 200, response.text
                current = response.json()
                assert current['llm_api_key_env'] == next_env
                assert current['voice'] == 'af_bella'
                assert config.config_llm_api_env(current) == config.config_stt_llm_api_env(current) == 'MODEL_API_KEY'
                assert config.agent_auth_headers(current) == original_headers
                assert not provider_activation_lock.acquire(blocking=False)
            finally:
                release.set()
                assert (await pending).status_code == 200
            assert config.load_config()['llm_api_key_env'] == next_env

    asyncio.run(exercise())


@pytest.mark.parametrize('provider,base,previous_env,next_env', [
    ('meta', 'https://api.meta.ai/v1', '', 'CUSTOM_ENV'),
    ('meta', 'https://api.meta.ai/v1', 'CUSTOM_ENV', ''),
    ('openai-compatible', 'https://api.meta.ai/v1', '', 'MODEL_API_KEY'),
    ('openai-compatible', 'https://api.meta.ai/v1', 'MODEL_API_KEY', ''),
    ('openai-compatible', 'https://custom.test/v1', '', 'MODEL_API_KEY'),
])
def test_busy_lifecycle_rejects_different_effective_credential_names(
    client, provider, base, previous_env, next_env,
):
    saved = config.update_config_values({
        'llm_provider': provider, 'llm_base_url': base, 'llm_api_key_env': previous_env,
        'agent_enabled': 'off',
    })
    candidate = {**saved, 'llm_api_key_env': next_env}
    assert config.config_llm_api_env(saved) != config.config_llm_api_env(candidate)
    before = config.CONFIG_PATH.read_bytes()
    assert provider_activation_lock.acquire(blocking=False)
    try:
        response = client.post('/api/config', json={'llm_api_key_env': next_env, 'voice': 'af_bella'})
        assert response.status_code == 409
        assert config.CONFIG_PATH.read_bytes() == before
    finally:
        provider_activation_lock.release()


def test_busy_comparison_uses_latest_settings_not_a_stale_form(client):
    original = config.update_config_values({'llm_provider': 'openai-compatible', 'agent_enabled': 'off'})
    assert provider_activation_lock.acquire(blocking=False)
    try:
        config.update_config_values({'agent_base_url': 'https://new-agent.test/v1'})
        before = config.CONFIG_PATH.read_bytes()
        response = client.post('/api/config', json={**original, 'voice': 'af_bella'})
        assert response.status_code == 409
        assert config.CONFIG_PATH.read_bytes() == before
        assert config.load_config()['agent_base_url'] == 'https://new-agent.test/v1'
    finally:
        provider_activation_lock.release()


def test_failed_settings_save_releases_lifecycle_ownership(monkeypatch, client):
    before = config.CONFIG_PATH.read_bytes()

    def fail(config):
        raise OSError('disk full')

    monkeypatch.setattr(config, 'save_config', fail)
    with pytest.raises(OSError, match='disk full'):
        client.post('/api/config', json={'voice': 'af_bella'})
    assert config.CONFIG_PATH.read_bytes() == before
    assert provider_activation_lock.acquire(blocking=False)
    provider_activation_lock.release()
