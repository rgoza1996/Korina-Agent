"""Settings validation must finish before any persisted state is changed."""
from __future__ import annotations

import json

import pytest


@pytest.mark.parametrize('key,value', [
    ('tts_port', 'bad'), ('tts_port', '8880'), ('tts_port', True),
    ('tts_port', 0), ('tts_port', 65536), ('tts_port', 8880.5),
    ('speed', True), ('speed', 0), ('speed', 4.1), ('speed', '1.0'),
    ('agent_max_turns', 0), ('agent_max_turns', 65),
    ('agent_max_tokens', -1), ('agent_max_tokens', 8193),
    ('agent_first_delivery_seconds', -1), ('agent_periodic_delivery_turns', 0),
    ('agent_interrupt_cooldown_padding_ms', -1), ('silence_ms', 2_147_483_648),
    ('min_speech_ms', 299), ('partial_window_ms', 399), ('idle_ack_step_ms', 0),
    ('ack_enabled', True), ('llm_reasoning', 'maybe'), ('stt_backend', 'other'),
    ('mode', 'other'), ('agent_busy_delivery_mode', 'other'),
    ('agent_interrupt_min_priority', 'panic'), ('final_stt_mode', 'other'),
    ('llm_base_url', []), ('tts_health_url', {}), ('agent_model', 123),
    ('local_model_roots', '/models'), ('local_model_roots', [123]),
    ('multimodal_stt_model_allowlist', [True]),
    ('converse', []), ('converse', 'korina'),
    ('converse', {'allowed_origins': 'https://demo.test'}),
    ('converse', {'allowed_origins': [False]}),
    ('converse', {'channel': 123}), ('converse', {'channel': ''}),
])
def test_invalid_update_rejects_the_whole_write(client, app_dir, key, value):
    before = client.get('/api/config').json()
    config_path = app_dir / 'config.json'
    original_bytes = config_path.read_bytes()

    response = client.post('/api/config', json={'voice': 'af_bella', key: value})

    assert response.status_code == 422
    assert any(error['loc'][:2] == ['body', key] for error in response.json()['detail'])
    assert config_path.read_bytes() == original_bytes
    assert client.get('/api/config').json() == before


@pytest.mark.parametrize('key', [
    'speed', 'voice', 'tts_port', 'tts_health_url', 'agent_enabled',
    'agent_max_tokens', 'llm_base_url', 'stt_llm_model', 'converse',
])
def test_explicit_null_is_not_an_omitted_control(client, app_dir, key):
    client.get('/api/config')
    original_bytes = (app_dir / 'config.json').read_bytes()
    response = client.post('/api/config', json={key: None})
    assert response.status_code == 422
    assert (app_dir / 'config.json').read_bytes() == original_bytes


@pytest.mark.parametrize('raw', [
    '{"speed": NaN}', '{"speed": Infinity}', '{"speed": -Infinity}',
    '{"tts_port": NaN}', '{"agent_max_tokens": Infinity}',
    '{"voice": NaN}', 'NaN',
])
def test_nonfinite_inputs_return_json_422_without_touching_disk(client, app_dir, raw):
    client.get('/api/config')
    original_bytes = (app_dir / 'config.json').read_bytes()
    response = client.post('/api/config', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json()['detail']
    assert all(set(error) == {'type', 'loc', 'msg'} for error in response.json()['detail'])
    assert (app_dir / 'config.json').read_bytes() == original_bytes


def test_partial_updates_preserve_generic_independent_endpoints_and_models(client):
    settings = {
        'llm_provider': 'vendor/response:v1', 'llm_base_url': 'https://response.test/custom/v1',
        'lm_model': 'org/response:small', 'stt_backend': 'llm',
        'stt_llm_provider': 'vendor/audio:v2', 'stt_llm_base_url': 'https://audio.test/other/v1',
        'stt_llm_model': '/models/Speech Small Q4.gguf', 'agent_provider': 'vendor/agent:v3',
        'agent_base_url': 'https://agent.test/reports/v1', 'agent_model': 'org/reporter:latest',
        'tts_provider': 'custom-speech', 'tts_model': 'org/voice:v1',
        'tts_health_url': 'https://tts.test/custom/readiness', 'speed': 1.5,
    }
    first = client.post('/api/config', json=settings)
    assert first.status_code == 200
    assert all(first.json()[key] == value for key, value in settings.items())
    changed = client.post('/api/config', json={'voice': 'af_bella'})
    assert changed.status_code == 200
    assert all(changed.json()[key] == value for key, value in settings.items())

    response_change = client.post('/api/config', json={'llm_base_url': 'https://response2.test/v1'})
    assert response_change.status_code == 200
    independent = ('stt_llm_provider', 'stt_llm_base_url', 'stt_llm_model', 'agent_base_url', 'agent_model')
    assert all(response_change.json()[key] == settings[key] for key in independent)
    stt_change = client.post('/api/config', json={'stt_llm_model': 'org/audio:new'})
    assert stt_change.status_code == 200
    assert stt_change.json()['llm_base_url'] == 'https://response2.test/v1'
    assert stt_change.json()['llm_provider'] == settings['llm_provider']


def test_nested_converse_patch_preserves_channel_origins_and_existing_extensions(client, app_dir):
    config_path = app_dir / 'config.json'
    saved = client.get('/api/config').json()
    saved['converse'] = {'channel': 'custom', 'allowed_origins': ['https://old.test'], 'legacy_hint': 'keep'}
    config_path.write_text(json.dumps(saved))

    origins = client.post('/api/config', json={'converse': {'allowed_origins': ['https://demo.test']}})
    assert origins.status_code == 200
    assert origins.json()['converse'] == {
        'channel': 'custom', 'allowed_origins': ['https://demo.test'], 'legacy_hint': 'keep',
    }
    channel = client.post('/api/config', json={'converse': {'channel': 'korina'}})
    assert channel.status_code == 200
    assert channel.json()['converse'] == {
        'channel': 'korina', 'allowed_origins': ['https://demo.test'], 'legacy_hint': 'keep',
    }
    empty = client.post('/api/config', json={'converse': {}})
    assert empty.status_code == 200
    assert empty.json()['converse'] == channel.json()['converse']


def test_user_updates_cannot_replace_server_probe_cache(client):
    from korina.config import load_config, set_audio_unsupported

    set_audio_unsupported('demo', 'https://demo.test/v1', 'audio', 'unsupported', 'probe response')
    before = load_config()['audio_unsupported']
    response = client.post('/api/config', json={
        'voice': 'af_bella', 'audio_unsupported': None, 'unknown_key': {'ignored': True},
    })
    assert response.status_code == 200
    assert response.json()['voice'] == 'af_bella'
    assert response.json()['audio_unsupported'] == before
    assert 'unknown_key' not in response.json()


def test_legacy_keys_and_delivery_values_remain_supported(client):
    from korina.config import LEGACY_AGENT_MODE_KEY, LEGACY_DELIVERY_VALUE, LEGACY_INJECTION_MODE_VALUE

    response = client.post('/api/config', json={
        LEGACY_AGENT_MODE_KEY: LEGACY_INJECTION_MODE_VALUE,
        'agent_busy_delivery_mode': LEGACY_DELIVERY_VALUE,
        'agent_compaction_reserve_tokens': 32768,
        'agent_transport': 'legacy-custom-transport',
        'agent_project_trust': 'legacy-custom-trust',
        'agent_retry_enabled': 'off',
    })
    assert response.status_code == 200
    saved = response.json()
    assert saved['agent_injection_mode'] == 'one-at-a-time'
    assert saved['agent_busy_delivery_mode'] == 'injection'
    assert saved['agent_compaction_reserve_tokens'] == 32768
    assert saved['agent_transport'] == 'legacy-custom-transport'
    assert saved['agent_project_trust'] == 'legacy-custom-trust'
    assert saved['agent_retry_enabled'] == 'off'
    again = client.post('/api/config', json={'voice': 'af_bella'})
    assert again.status_code == 200
    assert again.json()['agent_transport'] == saved['agent_transport']


def test_saved_values_are_readable_and_repairable_without_revalidating_the_file(client, app_dir):
    config_path = app_dir / 'config.json'
    client.get('/api/config')
    legacy = {'tts_port': 'old-invalid-port', 'agent_max_turns': None, 'converse': 'old-format'}
    config_path.write_text(json.dumps(legacy))
    before = config_path.read_bytes()

    read = client.get('/api/config')
    assert read.status_code == 200
    assert all(read.json()[key] == value for key, value in legacy.items())
    assert config_path.read_bytes() == before
    partial = client.post('/api/config', json={'voice': 'af_bella'})
    assert partial.status_code == 200
    assert all(partial.json()[key] == value for key, value in legacy.items())
    repaired = client.post('/api/config', json={'tts_port': 9000, 'converse': {'channel': 'korina'}})
    assert repaired.status_code == 200
    assert repaired.json()['tts_port'] == 9000
    assert repaired.json()['converse']['channel'] == 'korina'


def test_update_schema_is_visible_and_get_round_trip_still_works(client):
    spec = client.get('/openapi.json').json()
    request_schema = spec['paths']['/api/config']['post']['requestBody']['content']['application/json']['schema']
    schema = spec['components']['schemas'][request_schema['$ref'].rsplit('/', 1)[-1]]
    assert schema['properties']['tts_port']['minimum'] == 1
    assert schema['properties']['tts_port']['maximum'] == 65535
    assert 'required' not in schema
    before = client.get('/api/config').json()
    posted = client.post('/api/config', json=before)
    assert posted.status_code == 200
    # Explicitly writing the roots list now records the caller's choice,
    # including [] instead of silently restoring environment defaults.
    expected = {**before, 'local_model_roots_explicit': True}
    assert posted.json() == expected
    assert client.get('/api/config').json() == expected
