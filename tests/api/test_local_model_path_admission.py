"""Malformed local model paths fail before settings or managed services change."""
from __future__ import annotations

import errno
from pathlib import Path

import pytest

from korina import config
from korina.routes import model_roots, providers
from korina.services import provider_manager as manager


def _post(client, operation, model):
    if operation == 'activate':
        return client.post('/api/llm/provider/activate', json={'provider': 'llama.cpp', 'model': model})
    return client.post('/api/llm/llama/refresh', json={'model': model})


def _unchanged_runtime(monkeypatch, tmp_path):
    old = tmp_path / 'old.gguf'
    old.write_bytes(b'fixture')
    config.update_config_values({'llm_provider': 'llama.cpp', 'lm_model': str(old), 'agent_enabled': 'off'})
    config.set_audio_unsupported('llama.cpp', 'http://127.0.0.1:8080/v1', 'old-model', 'unsupported', '')
    before = config.CONFIG_PATH.read_bytes()
    commands = []

    def forbidden(*args, **kwargs):
        commands.append(args)
        pytest.fail('invalid model path reached managed service actions')

    monkeypatch.setattr(manager, '_systemctl', forbidden)
    monkeypatch.setattr(manager, 'start_llama_server', forbidden)
    monkeypatch.setattr(model_roots, 'start_llama_server', forbidden)
    for name in ('stop_llama_server', 'stop_lmstudio', 'stop_lmstudio_server', 'stop_ollama'):
        monkeypatch.setattr(manager, name, forbidden)
    return before, commands


@pytest.mark.parametrize('operation', ['activate', 'refresh'])
@pytest.mark.parametrize('model', ['/tmp/' + 'x' * 300 + '.gguf', '/tmp/invalid\x00.gguf'])
def test_invalid_local_path_returns_bounded_400_without_writes_or_service_actions(
    client, monkeypatch, tmp_path, operation, model,
):
    before, commands = _unchanged_runtime(monkeypatch, tmp_path)
    response = _post(client, operation, model)
    assert response.status_code == 400, response.text
    assert 'Invalid or unreadable local model path' in response.text
    assert len(response.content) < 1024
    assert config.CONFIG_PATH.read_bytes() == before
    assert commands == []


@pytest.mark.parametrize('operation', ['activate', 'refresh'])
@pytest.mark.parametrize('failure', ['inspect', 'resolve'])
def test_candidate_inspection_and_resolution_errors_are_admission_failures(
    client, monkeypatch, tmp_path, operation, failure,
):
    before, commands = _unchanged_runtime(monkeypatch, tmp_path)
    candidate = tmp_path / 'candidate.gguf'
    candidate.write_bytes(b'fixture')
    method = 'is_file' if failure == 'inspect' else 'resolve'
    original = getattr(Path, method)

    def unreadable(path, *args, **kwargs):
        if path == candidate:
            raise OSError(errno.EACCES, 'filesystem diagnostic must not be reflected ' * 100)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, method, unreadable)
    response = _post(client, operation, str(candidate))
    assert response.status_code == 400, response.text
    assert 'Invalid or unreadable local model path' in response.text
    assert 'filesystem diagnostic' not in response.text
    assert len(response.content) < 1024
    assert config.CONFIG_PATH.read_bytes() == before
    assert commands == []


def test_refresh_uses_the_shared_model_name_limit_before_discovery(client, monkeypatch, tmp_path):
    before, commands = _unchanged_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(model_roots, '_resolve_llama_cpp_model_id', lambda *_: pytest.fail('invalid model reached discovery'))
    response = _post(client, 'refresh', '/tmp/' + 'x' * 100_000 + '.gguf')
    assert response.status_code == 422
    assert len(response.content) < 1024
    assert config.CONFIG_PATH.read_bytes() == before
    assert commands == []


def test_local_model_admission_requires_a_file_not_a_directory(client, monkeypatch, tmp_path):
    before, commands = _unchanged_runtime(monkeypatch, tmp_path)
    candidate = tmp_path / 'directory.gguf'
    candidate.mkdir()
    response = _post(client, 'activate', str(candidate))
    assert response.status_code == 400
    assert 'model file not found' in response.text
    assert config.CONFIG_PATH.read_bytes() == before
    assert commands == []


@pytest.mark.parametrize('operation', ['activate', 'refresh'])
def test_actual_startup_oserror_remains_a_500_after_selection_is_saved(client, monkeypatch, tmp_path, operation):
    candidate = tmp_path / 'candidate.gguf'
    candidate.write_bytes(b'fixture')
    config.update_config_values({'llm_provider': 'llama.cpp', 'lm_model': 'old-selection'})
    commands = []

    def failed_start(*args, **kwargs):
        commands.append(args)
        raise OSError('actual service startup failed')

    if operation == 'activate':
        monkeypatch.setattr(providers, 'activate_llm_provider', failed_start)
    else:
        monkeypatch.setattr(model_roots, 'prepare_provider', lambda *args, **kwargs: (str(candidate), {}, []))
        monkeypatch.setattr(model_roots, 'start_llama_server', failed_start)
    response = _post(client, operation, str(candidate))
    assert response.status_code == 500
    assert 'saved' in response.json()['detail'].lower()
    assert 'actual service startup failed' in response.json()['detail']
    assert config.load_config()['lm_model'] == str(candidate)
    assert len(commands) == 1
