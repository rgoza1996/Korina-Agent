"""Legacy settings must not break startup or health, or silently widen CORS."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from korina import app_factory


def persist(app_dir, updates):
    config_path = app_dir / 'config.json'
    config_path.write_text(json.dumps({'ack_enabled': 'off', 'agent_enabled': 'off', **updates}))
    return config_path, config_path.read_bytes()


def preflight(client, origin):
    return client.options('/api/converse/channels', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'GET',
    })


@pytest.mark.parametrize('origins', [
    None, 'https://remote.test', 123, {}, False,
    ['https://remote.test', 123], [['https://remote.test']], ['*', False],
])
def test_bad_persisted_origin_shapes_start_with_loopback_cors(app_dir, origins):
    config_path, original_bytes = persist(app_dir, {'converse': {'allowed_origins': origins}})
    with TestClient(app_factory.create_app()) as client:
        assert client.get('/api/converse/channels').status_code == 200
        for origin in ('http://127.0.0.1:8001', 'http://localhost:8001'):
            assert preflight(client, origin).headers['access-control-allow-origin'] == origin
        rejected = preflight(client, 'https://remote.test')
        assert rejected.status_code == 400
        assert 'access-control-allow-origin' not in rejected.headers
    assert config_path.read_bytes() == original_bytes


@pytest.mark.parametrize('converse', [None, 'old-format', 123, []])
def test_bad_persisted_converse_block_uses_loopback_defaults(app_dir, converse):
    config_path, original_bytes = persist(app_dir, {'converse': converse})
    with TestClient(app_factory.create_app()) as client:
        assert client.get('/api/converse/channel').json()['channel'] == 'korina'
        assert preflight(client, 'http://localhost:8001').headers['access-control-allow-origin'] == 'http://localhost:8001'
        assert 'access-control-allow-origin' not in preflight(client, 'https://remote.test').headers
    assert config_path.read_bytes() == original_bytes


def test_explicit_empty_origin_list_does_not_fall_back_to_loopback(app_dir):
    config_path, original_bytes = persist(app_dir, {'converse': {'allowed_origins': []}})
    with TestClient(app_factory.create_app()) as client:
        assert client.get('/api/converse/channels').status_code == 200
        for origin in ('http://127.0.0.1:8001', 'http://localhost:8001', 'https://remote.test'):
            response = preflight(client, origin)
            assert response.status_code == 400
            assert 'access-control-allow-origin' not in response.headers
    assert config_path.read_bytes() == original_bytes


def test_valid_origin_list_is_preserved_without_adding_loopback(app_dir):
    config_path, original_bytes = persist(app_dir, {'converse': {'allowed_origins': ['https://demo.test', 'null']}})
    with TestClient(app_factory.create_app()) as client:
        for origin in ('https://demo.test', 'null'):
            assert preflight(client, origin).headers['access-control-allow-origin'] == origin
        assert 'access-control-allow-origin' not in preflight(client, 'http://localhost:8001').headers
    assert config_path.read_bytes() == original_bytes


def test_explicit_wildcard_is_retained_without_credentials(app_dir):
    config_path, original_bytes = persist(app_dir, {'converse': {'allowed_origins': ['*']}})
    with TestClient(app_factory.create_app()) as client:
        response = preflight(client, 'https://demo.test')
        assert response.headers['access-control-allow-origin'] == '*'
        assert 'access-control-allow-credentials' not in response.headers
    assert config_path.read_bytes() == original_bytes


@pytest.mark.parametrize('port,expected_port', [
    ('bad', 8880), ('NaN', 8880), (float('nan'), 8880),
    (float('inf'), 8880), (-float('inf'), 8880),
    (0, 8880), (-1, 8880), (65536, 8880), (True, 8880),
    (9000.5, 8880), (None, 8880), ([], 8880), ({}, 8880),
    (1, 1), (65535, 65535), (9000, 9000), (' 9001 ', 9001), (9002.0, 9002),
])
def test_health_resolves_legacy_tts_port_without_rewriting(app_dir, monkeypatch, port, expected_port):
    from korina.routes import health

    config_path, original_bytes = persist(app_dir, {'tts_port': port, 'tts_base_url': ''})
    probed = []

    def tts_probe(base_url, **kwargs):
        probed.append(base_url)
        return True, {'ok': True, 'loaded': False}, None

    monkeypatch.setattr(health, '_probe_tts_backend', tts_probe)
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': False, 'loaded': False})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    with TestClient(app_factory.create_app()) as client:
        response = client.get('/api/health')
        assert response.status_code == 200
        expected_url = f'http://127.0.0.1:{expected_port}'
        assert response.json()['tts_base_url'] == expected_url
        assert response.json()['tts']['base_url'] == expected_url
        assert probed == [expected_url]
    assert config_path.read_bytes() == original_bytes


def test_explicit_tts_endpoint_takes_precedence_over_a_bad_saved_port(app_dir):
    from korina.config import config_tts_base_url, load_config

    config_path, original_bytes = persist(app_dir, {
        'tts_port': 'bad', 'tts_base_url': 'https://speech.test/custom/v1/',
    })
    assert config_tts_base_url(load_config()) == 'https://speech.test/custom/v1'
    assert config_path.read_bytes() == original_bytes
