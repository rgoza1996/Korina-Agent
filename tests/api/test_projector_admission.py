"""Ambiguous local projectors fail before settings or managed services change."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from korina import config
from korina.services import model_catalog, provider_manager as manager


@pytest.fixture
def projector_runtime(monkeypatch, tmp_path):
    selected = tmp_path / 'selected' / 'Model.GGUF'
    selected.parent.mkdir()
    selected.write_bytes(b'fixture')
    old = tmp_path / 'old.gguf'
    old.write_bytes(b'fixture')
    binary = tmp_path / 'llama-server'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o755)
    unit = tmp_path / 'existing.service'
    unit.write_text('original unit')
    actions = []
    monkeypatch.setattr(manager, 'LLAMA_SERVER_BIN', binary)
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', unit)
    monkeypatch.setattr(manager.shutil, 'which', lambda name: '/fixture/' + name)
    monkeypatch.setattr(manager.provider_processes, 'user_processes', lambda: iter([]))
    monkeypatch.setattr(manager, '_systemctl', lambda *args, **kwargs:
                        actions.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(manager, 'wait_for_llama_server_ready', lambda: None)
    for name in ('stop_llama_server', 'stop_lmstudio', 'stop_lmstudio_server', 'stop_ollama'):
        monkeypatch.setattr(manager, name, lambda name=name:
                            actions.append(name) or manager.LifecycleActions())
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [selected.parent])
    config.update_config_values({
        'llm_provider': 'llama.cpp', 'lm_model': str(old),
        'agent_enabled': 'off', 'stt_backend': 'whisper',
    })
    config.set_audio_unsupported('llama.cpp', 'http://127.0.0.1:8080/v1', 'old', 'unsupported', '')
    return SimpleNamespace(selected=selected, old=old, unit=unit, actions=actions)


def add_projectors(runtime):
    files = [runtime.selected.parent / name for name in ('A-MMPrOj.GgUf', 'Z-mmproj.gguf')]
    for path in files:
        path.write_bytes(b'fixture')
    return files


def test_multiple_real_projectors_are_not_chosen_alphabetically(projector_runtime):
    files = add_projectors(projector_runtime)
    # Before this guard the finder silently returned files[0].
    assert files[0].name < files[1].name
    with pytest.raises(ValueError, match='separate folder'):
        model_catalog.find_mmproj_for_model(str(projector_runtime.selected))


@pytest.mark.parametrize('single', [False, True])
def test_zero_or_single_real_projector_preserves_unit_and_path_behavior(projector_runtime, single):
    runtime = projector_runtime
    (runtime.selected.parent / 'directory-mmproj.gguf').mkdir()
    (runtime.selected.parent / 'broken-mmproj.GGUF').symlink_to(runtime.selected.parent / 'missing')
    sidecar = runtime.selected.parent / 'Chosen-MMPrOj.GgUf'
    if single:
        sidecar.write_bytes(b'fixture')
    assert model_catalog.find_mmproj_for_model(str(runtime.selected)) == (str(sidecar) if single else '')
    manager.write_llama_server_unit(str(runtime.selected), {})
    text = runtime.unit.read_text()
    assert f'-m {runtime.selected}' in text
    assert (f'--mmproj {sidecar}' in text) if single else ('--mmproj' not in text)


@pytest.mark.parametrize('operation', ['activate', 'refresh'])
def test_ambiguous_projector_api_preserves_selection_cache_unit_and_services(
    client, projector_runtime, operation,
):
    runtime = projector_runtime
    add_projectors(runtime)
    before = config.CONFIG_PATH.read_bytes()
    endpoint = '/api/llm/provider/activate' if operation == 'activate' else '/api/llm/llama/refresh'
    payload = {'model': str(runtime.selected)}
    if operation == 'activate':
        payload['provider'] = 'llama.cpp'
    response = client.post(endpoint, json=payload)
    assert response.status_code == 400, response.text
    assert 'separate folder' in response.text
    assert config.CONFIG_PATH.read_bytes() == before
    assert runtime.unit.read_text() == 'original unit'
    assert runtime.actions == []


def test_direct_activation_rejects_ambiguity_before_teardown(projector_runtime):
    runtime = projector_runtime
    add_projectors(runtime)
    with pytest.raises(ValueError, match='separate folder'):
        manager.activate_llm_provider('llama.cpp', config.load_config(), model=str(runtime.selected))
    assert runtime.unit.read_text() == 'original unit'
    assert runtime.actions == []


@pytest.mark.parametrize('saved_selection', [True, False])
def test_omitted_activation_model_checks_effective_projector_before_saving(
    client, projector_runtime, saved_selection,
):
    runtime = projector_runtime
    add_projectors(runtime)
    config.update_config_values({'lm_model': str(runtime.selected) if saved_selection else ''})
    before = config.CONFIG_PATH.read_bytes()
    response = client.post('/api/llm/provider/activate', json={'provider': 'llama.cpp'})
    assert response.status_code == 400, response.text
    assert 'separate folder' in response.text
    assert config.CONFIG_PATH.read_bytes() == before
    assert runtime.unit.read_text() == 'original unit'
    assert runtime.actions == []


def test_relative_activation_checks_the_resolved_root_candidate_before_saving(
    client, projector_runtime, monkeypatch,
):
    runtime = projector_runtime
    add_projectors(runtime)
    # Existing relative-path admission can see a CWD file, while the startup
    # resolver prefers the matching basename in the configured model roots.
    monkeypatch.chdir(runtime.selected.parent.parent)
    (runtime.selected.parent.parent / runtime.selected.name).write_bytes(b'fixture')
    before = config.CONFIG_PATH.read_bytes()
    response = client.post('/api/llm/provider/activate', json={
        'provider': 'llama.cpp', 'model': runtime.selected.name,
    })
    assert response.status_code == 400, response.text
    assert 'separate folder' in response.text
    assert config.CONFIG_PATH.read_bytes() == before
    assert runtime.unit.read_text() == 'original unit'
    assert runtime.actions == []


def test_model_discovery_does_not_run_projector_admission_scans(client, projector_runtime, monkeypatch):
    from korina.routes import models

    runtime = projector_runtime
    add_projectors(runtime)
    monkeypatch.setattr(models, 'llm_models_for', lambda *args, **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])

    def forbidden(*args, **kwargs):
        pytest.fail('display discovery must retain its bounded shared metadata scan')

    monkeypatch.setattr(manager, '_llama_selection', forbidden)
    monkeypatch.setattr(manager, 'find_mmproj_for_model', forbidden)
    monkeypatch.setattr(model_catalog, 'find_mmproj_for_model', forbidden)
    response = client.get('/api/models')
    assert response.status_code == 200, response.text
    assert str(runtime.selected) in response.json()['llama_cpp_local_models']
    assert runtime.actions == []


def test_unit_writer_rechecks_ambiguity_without_overwriting_existing_unit(projector_runtime):
    runtime = projector_runtime
    add_projectors(runtime)
    with pytest.raises(ValueError, match='separate folder'):
        manager.write_llama_server_unit(str(runtime.selected), {})
    assert runtime.unit.read_text() == 'original unit'
