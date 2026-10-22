
from __future__ import annotations

from pathlib import Path

import pytest


def test_synchronize_llm_dependents_tracks_provider_transition(config_module):
    previous = dict(config_module.DEFAULT_CONFIG)
    previous.update({
        "llm_provider": "openai-compatible",
        "llm_base_url": "http://example.test/v1",
        "lm_model": "old-model",
        "stt_backend": "llm",
        "stt_llm_base_url": "",
        "agent_provider": "openai-compatible",
        "agent_base_url": "http://example.test/v1",
        "agent_model": "old-model",
    })
    current = dict(previous)
    current.update({"llm_provider": "llama.cpp", "lm_model": "/tmp/new-model.gguf"})

    synced = config_module.synchronize_llm_dependents(current, previous)

    assert synced["llm_base_url"] == "http://127.0.0.1:8080/v1"
    assert synced["stt_llm_provider"] == "llama.cpp"
    assert synced["stt_llm_base_url"] == "http://127.0.0.1:8080/v1"
    assert synced["stt_llm_model"] == "/tmp/new-model.gguf"
    assert synced["agent_base_url"] == "http://127.0.0.1:8080/v1"
    assert synced["agent_model"] == "/tmp/new-model.gguf"


def test_resolve_llama_cpp_model_id_variants(reload_korina_modules, model_root):
    family = model_root / "family"
    family.mkdir(parents=True)
    exact = family / "Exact-Model.Q4.gguf"
    exact.write_text("gguf")
    substring = family / "Gemma-4-E2B_q4_0-it.gguf"
    substring.write_text("gguf")
    (family / "mmproj-Gemma-4-E2B.gguf").write_text("skip mmproj")

    provider_manager = reload_korina_modules(
        "korina.services.model_catalog", "korina.services.provider_manager"
    )[1]

    assert provider_manager._resolve_llama_cpp_model_id(str(exact)) == str(exact)
    assert provider_manager._resolve_llama_cpp_model_id(exact.name) == str(exact)
    assert provider_manager._resolve_llama_cpp_model_id(exact.stem) == str(exact)
    assert provider_manager._resolve_llama_cpp_model_id("gemma-4-e2b") == str(substring)
    assert provider_manager._resolve_llama_cpp_model_id("") == ""
    assert provider_manager._resolve_llama_cpp_model_id("missing-model") == "missing-model"


def test_provider_supports_model_examples(reload_korina_modules, model_root, lmstudio_root):
    local = model_root / "local.gguf"
    local.write_text("gguf")
    manifest_dir = lmstudio_root / "owner" / "repo"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text('{"owner":"owner","name":"repo"}')

    provider_manager = reload_korina_modules(
        "korina.services.model_catalog", "korina.services.provider_manager"
    )[1]

    assert provider_manager.provider_supports_model("openai-compatible", "remote-model") == (
        True,
        "user_provided_endpoint",
    )
    assert provider_manager.provider_supports_model("llama.cpp", str(local))[0] is True
    supported, reason = provider_manager.provider_supports_model("llama.cpp", "owner/repo")
    assert supported is False
    assert "endpoint-loaded" in reason
    assert provider_manager.provider_supports_model("lmstudio", "owner/repo") == (
        True,
        "lmstudio_catalog",
    )
    assert provider_manager.provider_supports_model("lmstudio", str(local))[0] is False
    assert provider_manager.provider_supports_model("ollama", "llama3")[0] is True


@pytest.mark.parametrize("reasoning", ["on", "off", "auto"])
def test_write_llama_server_unit_output_shape(monkeypatch, reload_korina_modules, tmp_path, reasoning):
    provider_manager = reload_korina_modules("korina.services.provider_manager")[0]
    model = tmp_path / "model.gguf"
    model.write_text("gguf")
    mmproj = tmp_path / "model-mmproj.gguf"
    mmproj.write_text("mmproj")
    llama_bin = tmp_path / "bin" / "llama-server"
    llama_bin.parent.mkdir()
    llama_bin.write_text("#!/bin/sh\n")
    unit_path = tmp_path / "systemd" / "llama-server.service"

    monkeypatch.setattr(provider_manager, "LLAMA_SERVER_BIN", llama_bin)
    monkeypatch.setattr(provider_manager, "LLAMA_SERVER_USER_UNIT", unit_path)
    monkeypatch.setattr(provider_manager, "LLAMA_SERVER_MEDIA_PATH", "/models")

    provider_manager.write_llama_server_unit(str(model), {"llm_reasoning": reasoning})

    body = unit_path.read_text()
    assert "[Unit]" in body
    assert "[Service]" in body
    assert f"ExecStart={llama_bin} -m {model}" in body
    assert f"--mmproj {mmproj}" in body
    assert f"--reasoning {reasoning}" in body
    assert "--host 127.0.0.1 --port 8080" in body
    assert "--media-path /models" in body
    assert "VK_ICD_FILE" not in body
    assert f"WorkingDirectory={llama_bin.parent}" in body


def test_lmstudio_start_uses_configured_binary_directory(monkeypatch, tmp_path):
    from korina.services import provider_manager as manager

    executable = tmp_path / 'custom install' / 'lm-studio'
    executable.parent.mkdir()
    executable.write_text('#!/bin/sh\n')
    monkeypatch.setattr(manager, 'LMSTUDIO_BIN', executable)
    monkeypatch.setattr(manager.provider_processes, 'has_executable', lambda path: False)
    calls = []
    monkeypatch.setattr(manager.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    manager.start_lmstudio()
    assert calls[0][0] == [str(executable)]
    assert calls[0][1]['cwd'] == str(executable.parent)


def test_gui_environment_keeps_current_user_session(monkeypatch, tmp_path):
    from korina.services import provider_manager as manager

    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'bus').touch()
    authority = runtime / '.mutter-Xwaylandauth.test'
    authority.touch()
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))
    monkeypatch.delenv('DBUS_SESSION_BUS_ADDRESS', raising=False)
    monkeypatch.delenv('XAUTHORITY', raising=False)
    monkeypatch.delenv('DISPLAY', raising=False)
    environment = manager.gui_env()
    assert environment['XDG_RUNTIME_DIR'] == str(runtime)
    assert environment['DBUS_SESSION_BUS_ADDRESS'] == f'unix:path={runtime}/bus'
    assert environment['XAUTHORITY'] == str(authority)
    assert 'DISPLAY' not in environment


def test_unit_arguments_protect_paths_with_spaces_and_specifiers(monkeypatch, tmp_path):
    from korina.services import provider_manager as manager

    directory = tmp_path / 'model files%'
    directory.mkdir()
    model = directory / 'demo $model.gguf'
    model.write_text('gguf')
    binary = directory / 'llama-server'
    binary.write_text('#!/bin/sh\n')
    unit = tmp_path / 'llama-server.service'
    monkeypatch.setattr(manager, 'LLAMA_SERVER_BIN', binary)
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', unit)
    monkeypatch.setattr(manager, 'LLAMA_SERVER_MEDIA_PATH', '')
    manager.write_llama_server_unit(str(model), {'llm_reasoning': 'off'})
    body = unit.read_text()
    assert 'model files%%/demo $$model.gguf"' in body
    assert 'WorkingDirectory=' + str(directory).replace('%', '%%') in body
    assert '--media-path' not in body
