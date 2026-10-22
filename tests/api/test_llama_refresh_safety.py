"""Refresh shares activation preflight without tearing down unrelated providers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from korina import config
from korina.routes import model_roots
from korina.services import provider_manager as manager
from korina.services import provider_processes as processes


@pytest.fixture
def refresh_runtime(monkeypatch, tmp_path):
    old, selected = tmp_path / 'old.gguf', tmp_path / 'selected.gguf'
    old.write_text('fixture')
    selected.write_text('fixture')
    executable = tmp_path / 'llama-server'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    unit = tmp_path / 'demo.service'
    unit.write_text('original unit')
    calls = []
    monkeypatch.setattr(manager, 'LLAMA_SERVER_BIN', executable)
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', unit)
    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/bin/' + name)
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([]))
    monkeypatch.setattr(manager, '_systemctl', lambda *args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(manager, 'wait_for_llama_server_ready', lambda: None)
    for name in ('stop_llama_server', 'stop_lmstudio', 'stop_lmstudio_server', 'stop_ollama'):
        monkeypatch.setattr(manager, name, lambda name=name: calls.append(('unexpected stop', name)))
    config.update_config_values({
        'llm_provider': 'llama.cpp', 'llm_base_url': 'http://127.0.0.1:8080/v1',
        'lm_model': str(selected), 'stt_backend': 'whisper', 'agent_enabled': 'off',
    })
    return SimpleNamespace(old=old, selected=selected, executable=executable, unit=unit, calls=calls)


def running(monkeypatch, runtime, port):
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([
        (42, runtime.executable, [str(runtime.executable), '-m', str(runtime.selected)]),
    ]))
    monkeypatch.setattr(processes, 'listens_on', lambda pid, listening_port: pid == 42 and listening_port == port)


def active_role(role, port, model):
    if role == 'stt':
        return {'stt_backend': 'llm', 'stt_llm_base_url': f'http://localhost:{port}/v1', 'stt_llm_model': str(model)}
    return {'agent_enabled': 'on', 'agent_base_url': f'http://localhost:{port}/v1', 'agent_model': str(model)}


@pytest.mark.parametrize('role,port,different_model', [
    ('stt', 8080, False), ('agent', 9000, False),
    ('stt', 8080, True), ('agent', 9000, True),
])
def test_refresh_cannot_restart_a_running_service_required_by_an_active_role(
    monkeypatch, client, refresh_runtime, role, port, different_model,
):
    runtime = refresh_runtime
    config.update_config_values(active_role(role, port, runtime.old if different_model else runtime.selected))
    running(monkeypatch, runtime, port)
    before = config.CONFIG_PATH.read_bytes()
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 409
    assert 'active STT or Agent' in response.json()['detail'] or 'uses another model' in response.json()['detail']
    assert runtime.calls == []
    assert runtime.unit.read_text() == 'original unit'
    assert config.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize('shared_but_down', [False, True])
def test_refresh_can_restart_an_unshared_or_down_service_without_other_provider_actions(
    monkeypatch, client, refresh_runtime, shared_but_down,
):
    runtime = refresh_runtime
    if shared_but_down:
        config.update_config_values(active_role('stt', 8080, runtime.selected))
    else:
        running(monkeypatch, runtime, 8080)
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 200, response.text
    assert response.json()['restarted'] is True
    assert response.json()['model'] == str(runtime.selected)
    assert ('restart', 'demo.service') in runtime.calls
    assert not any(call[0] == 'unexpected stop' for call in runtime.calls)
    assert str(runtime.selected) in runtime.unit.read_text()


@pytest.mark.parametrize('missing', ['model', 'binary', 'systemctl', 'unit'])
def test_refresh_preflight_failure_never_rewrites_unit_or_saves_settings(
    monkeypatch, client, refresh_runtime, missing,
):
    runtime = refresh_runtime
    if missing == 'model':
        runtime.selected.unlink()
    elif missing == 'binary':
        runtime.executable.unlink()
    elif missing == 'systemctl':
        monkeypatch.setattr(manager.shutil, 'which', lambda name: None if name == 'systemctl' else '/bin/' + name)
    else:
        monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', runtime.unit.with_suffix('.txt'))
    config.set_audio_unsupported('llama.cpp', 'http://127.0.0.1:8080/v1', 'bad', 'unsupported', '')
    before = config.CONFIG_PATH.read_bytes()
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 500
    assert 'preflight failed' in response.json()['detail']
    assert runtime.calls == []
    assert runtime.unit.read_text() == 'original unit'
    assert config.CONFIG_PATH.read_bytes() == before


def test_refresh_rechecks_roles_preserved_by_the_atomic_save(monkeypatch, client, refresh_runtime):
    runtime = refresh_runtime
    running(monkeypatch, runtime, 9000)
    prepare = config.prepare_llama_refresh

    def enable_agent(*args):
        config.update_config_values(active_role('agent', 9000, runtime.selected))
        return prepare(*args)

    monkeypatch.setattr(config, 'prepare_llama_refresh', enable_agent)
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 409
    assert 'saved, but restart was blocked' in response.json()['detail']
    assert config.load_config()['agent_enabled'] == 'on'
    assert runtime.calls == []
    assert runtime.unit.read_text() == 'original unit'


def test_refresh_keeps_atomic_selection_check_after_preflight(monkeypatch, client, refresh_runtime):
    runtime = refresh_runtime
    prepare = model_roots.prepare_provider

    def change_selection(*args, **kwargs):
        result = prepare(*args, **kwargs)
        config.update_config_values({'llm_provider': 'openai-compatible'})
        return result

    monkeypatch.setattr(model_roots, 'prepare_provider', change_selection)
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 409
    assert 'settings changed' in response.json()['detail']
    assert config.load_config()['llm_provider'] == 'openai-compatible'
    assert runtime.calls == []
    assert runtime.unit.read_text() == 'original unit'
