"""Discovered GGUF filenames remain usable without rewriting their spelling."""
from __future__ import annotations

import pytest

from korina.routes import models, providers
from korina.services import model_catalog, provider_manager


@pytest.mark.parametrize('extension', ['.gguf', '.GGUF', '.GgUf'])
def test_discovered_model_activates_with_original_path_and_audio_status(
    client, config_module, monkeypatch, tmp_path, extension,
):
    model = tmp_path / ('FiXtUrE-model' + extension)
    model.write_bytes(b'fixture')
    sidecar = tmp_path / ('FiXtUrE-MMPrOj' + extension)
    sidecar.write_bytes(b'fixture')
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [tmp_path])
    monkeypatch.setattr(models, 'llm_models_for', lambda *args, **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    config_module.update_config_values({
        'llm_provider': 'llama.cpp', 'lm_model': str(model),
        'stt_backend': 'llm', 'stt_llm_provider': 'llama.cpp',
        'stt_llm_base_url': 'http://127.0.0.1:8080/v1', 'stt_llm_model': str(model),
    })
    activated = []

    def activate(provider, saved, model=None):
        activated.append((provider, saved['lm_model'], model))
        return {'provider': provider, 'model': model}

    monkeypatch.setattr(providers, 'activate_llm_provider', activate)
    discovered = client.get('/api/models')
    assert discovered.status_code == 200
    body = discovered.json()
    assert body['llama_cpp_local_models'] == [str(model)]
    response = client.post('/api/llm/provider/activate', json={
        'provider': 'llama.cpp', 'model': str(model),
    })
    assert response.status_code == 200, response.text
    assert response.json()['saved']['lm_model'] == str(model)
    assert activated == [('llama.cpp', str(model), str(model))]
    assert body['llm_models_status'][str(model)]['usable'] is True
    assert body['stt_llm_models_status'][str(model)]['usable'] is True
    assert body['stt_llm_models_capabilities'][str(model)]['has_mmproj'] is True


@pytest.mark.parametrize('extension', ['.gguf', '.GGUF', '.GgUf'])
def test_selected_existing_model_stays_pinned_after_directory_budget(
    monkeypatch, tmp_path, extension,
):
    model = tmp_path / ('Chosen-Model' + extension)
    model.write_bytes(b'fixture')
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [tmp_path])
    monkeypatch.setattr(model_catalog, 'MAX_CATALOG_ENTRIES', 0)
    assert model_catalog.discover_local_gguf_models() == []
    assert model_catalog.discover_local_gguf_models(pinned=[str(model)]) == [str(model)]


@pytest.mark.parametrize('extension', ['.gguf', '.GGUF', '.GgUf'])
def test_generated_unit_keeps_model_and_sidecar_path_spelling(monkeypatch, tmp_path, extension):
    model = tmp_path / ('FiXtUrE-model' + extension)
    model.write_bytes(b'fixture')
    sidecar = tmp_path / ('FiXtUrE-MMPrOj' + extension)
    sidecar.write_bytes(b'fixture')
    binary = tmp_path / 'llama-server'
    binary.write_bytes(b'fixture')
    unit = tmp_path / 'llama-server.service'
    monkeypatch.setattr(provider_manager, 'LLAMA_SERVER_BIN', binary)
    monkeypatch.setattr(provider_manager, 'LLAMA_SERVER_USER_UNIT', unit)
    assert model_catalog.find_mmproj_for_model(str(model)) == str(sidecar)
    provider_manager.write_llama_server_unit(str(model), {})
    command = next(line for line in unit.read_text().splitlines() if line.startswith('ExecStart='))
    assert f'-m {model}' in command
    assert f'--mmproj {sidecar}' in command


def test_catalog_uppercase_sidecar_status_is_consistent():
    model = 'owner/FiXtUrE/Model.GGUF'
    sidecar = 'owner/FiXtUrE/Model-MMPrOj.GgUf'
    status = models._status_for_model(
        model, provider='lmstudio', base_url='http://127.0.0.1:1234/v1', require_audio=True,
        capability={'supports_audio_input': True, 'has_mmproj': True}, audio_cache={},
        all_models=[model, sidecar], catalog_models={model, sidecar},
    )
    assert status['usable'] is False
    assert status['reasons'] == ['paired mmproj sidecar exists for this GGUF']


def test_uppercase_model_without_sidecar_keeps_missing_sidecar_status():
    status = models._status_for_model(
        'owner/Model.GGUF', provider='openai-compatible', base_url='http://fixture.invalid/v1',
        require_audio=True, capability={'supports_audio_input': True, 'has_mmproj': False},
        audio_cache={}, all_models=[], catalog_models=set(),
    )
    assert status['usable'] is False
    assert status['reasons'] == ['missing mmproj for multimodal audio']
