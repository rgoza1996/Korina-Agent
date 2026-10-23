"""Activation validates an explicit model independently of display truncation."""
from __future__ import annotations

import json

import pytest

from korina.services import model_catalog, provider_manager


def test_lmstudio_explicit_model_beyond_display_cap_remains_admissible(monkeypatch, tmp_path):
    cli = tmp_path / 'lms'
    cli.write_text('#!/bin/sh\n')
    cli.chmod(0o755)
    selected = 'imported-model-599'
    output = json.dumps([
        {'type': 'llm', 'modelKey': f'imported-model-{index}'}
        for index in range(600)
    ]).encode()
    monkeypatch.setattr(model_catalog, 'LMSTUDIO_HUB_ROOT', tmp_path / 'hub')
    monkeypatch.setattr(model_catalog, 'LMS_CLI_BIN', cli)
    monkeypatch.setattr(model_catalog, '_lms_listing', lambda: output)
    monkeypatch.setattr(provider_manager, 'LMS_CLI_BIN', cli)
    monkeypatch.setattr(provider_manager, '_is_lmstudio_server_already_up', lambda: True)
    monkeypatch.setattr(provider_manager.provider_processes, 'user_processes', lambda: iter(()))

    displayed = model_catalog.discover_lmstudio_catalog_models()
    assert len(displayed) == 512
    assert selected not in displayed
    assert provider_manager.provider_supports_model('lmstudio', selected) == (True, 'lmstudio_catalog')
    assert provider_manager.prepare_provider('lmstudio', {}, selected)[0] == selected
    assert provider_manager.provider_supports_model('lmstudio', 'unknown-model')[0] is False


def test_explicit_gguf_path_outside_display_walk_remains_admissible(monkeypatch, tmp_path):
    selected = tmp_path / 'selected.gguf'
    selected.write_bytes(b'GGUF')
    binary = tmp_path / 'llama-server'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o755)
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [tmp_path])
    monkeypatch.setattr(model_catalog, 'MAX_CATALOG_ENTRIES', 0)
    monkeypatch.setattr(provider_manager, 'LLAMA_SERVER_BIN', binary)
    monkeypatch.setattr(provider_manager, 'LLAMA_SERVER_USER_UNIT', tmp_path / 'llama.service')
    monkeypatch.setattr(provider_manager.shutil, 'which', lambda name: '/usr/bin/systemctl' if name == 'systemctl' else None)
    monkeypatch.setattr(provider_manager.provider_processes, 'user_processes', lambda: iter(()))

    assert model_catalog.discover_local_gguf_models() == []
    assert provider_manager.provider_supports_model('llama.cpp', str(selected)) == (True, 'local_gguf')
    assert provider_manager.prepare_provider('llama.cpp', {}, str(selected))[0] == str(selected)
    with pytest.raises(RuntimeError, match='Requested llama.cpp model not found'):
        provider_manager.prepare_provider('llama.cpp', {}, str(tmp_path / 'missing.gguf'))


def test_lmstudio_explicit_hub_model_outside_display_walk_remains_admissible(monkeypatch, tmp_path):
    hub = tmp_path / 'hub'
    manifest = hub / 'owner' / 'selected' / 'manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"owner":"owner","name":"selected"}')
    monkeypatch.setattr(model_catalog, 'LMSTUDIO_HUB_ROOT', hub)
    monkeypatch.setattr(model_catalog, 'LMS_CLI_BIN', tmp_path / 'missing-lms')
    monkeypatch.setattr(model_catalog, 'MAX_CATALOG_ENTRIES', 0)

    assert model_catalog.discover_lmstudio_catalog_models() == []
    assert provider_manager.provider_supports_model('lmstudio', 'owner/selected') == (True, 'lmstudio_catalog')
    assert provider_manager.provider_supports_model('lmstudio', 'owner/missing')[0] is False
