"""Readiness requires the whole browser tree, including imported modules."""
from __future__ import annotations

from pathlib import Path

import pytest

from korina.routes import health


@pytest.fixture
def ready_dependencies(monkeypatch):
    monkeypatch.setattr(health, 'load_config', lambda: {
        'lm_model': 'fixture-model', 'stt_backend': 'llm', 'agent_enabled': 'off',
    })
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': True, 'loaded': True})
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    monkeypatch.setattr(health.shutil, 'which', lambda name: '/fixture/ffmpeg')


def test_missing_imported_module_fails_readiness_and_recovers(client, app_dir, ready_dependencies):
    assert client.get('/api/ready').status_code == 200
    module = app_dir / 'js/audio-probes.js'
    original = module.read_bytes()
    module.unlink()
    assert client.get('/js/app.js').status_code == 200
    assert client.get('/js/audio-probes.js').status_code == 404
    live = client.get('/api/health')
    assert live.status_code == 200
    assert live.json()['readiness']['checks']['frontend'] is False
    response = client.get('/api/ready')
    assert response.status_code == 503
    assert response.json()['checks']['frontend'] is False
    module.write_bytes(original)
    assert client.get('/api/ready').status_code == 200


def test_custom_content_and_missing_optional_assets_keep_frontend_ready(client, app_dir, ready_dependencies):
    (app_dir / 'index.html').write_text('<!doctype html><title>Custom Korina</title>')
    (app_dir / 'js/app.js').write_text('// Custom application implementation\n')
    (app_dir / 'Ack/ack_phrases.json').unlink()
    (app_dir / 'config/config.example.json').unlink()
    assert 'Custom Korina' in client.get('/').text
    response = client.get('/api/ready')
    assert response.status_code == 200
    assert response.json()['checks']['frontend'] is True
    status = client.get('/api/health').json()['ack_status']
    assert status['ok'] is False
    assert status['manifest_count'] is None


@pytest.mark.parametrize('failure', ['directory', 'unreadable'])
def test_non_regular_or_unreadable_module_fails_frontend_check(client, app_dir, ready_dependencies, monkeypatch, failure):
    module = app_dir / 'js/response-body.js'
    if failure == 'directory':
        module.unlink()
        module.mkdir()
    else:
        native_open = Path.open

        def open_file(path, *args, **kwargs):
            if path == module:
                raise PermissionError('Fixture module is unreadable')
            return native_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', open_file)
    response = client.get('/api/ready')
    assert response.status_code == 503
    assert response.json()['checks']['frontend'] is False
