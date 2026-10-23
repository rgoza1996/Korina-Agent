
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest


@pytest.mark.parametrize('default_service_stop', [True, False])
def test_ollama_process_cleanup_can_leave_unit_eligibility_to_wrapper(monkeypatch, default_service_stop):
    from korina.services import provider_manager as manager
    from korina.services.provider_processes import TerminationResult

    units, processes = [], []
    monkeypatch.setattr(manager, '_systemctl', lambda *args:
                        units.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(manager.shutil, 'which', lambda command: '/fixture/ollama')
    monkeypatch.setattr(manager.provider_processes, 'terminate_executable', lambda executable, command:
                        processes.append((executable, command)) or TerminationResult(signalled=[123]))
    monkeypatch.setattr(manager.provider_processes, 'wait_for_exit', lambda pids: [])
    actions = manager.stop_ollama() if default_service_stop else manager.stop_ollama(stop_service=False)
    assert units == ([('stop', 'ollama.service')] if default_service_stop else [])
    assert processes == [(Path('/fixture/ollama'), 'serve')]
    assert actions.stopped == (['ollama.service', 'ollama'] if default_service_stop else ['ollama'])
    assert not actions.warnings


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


def test_separate_stt_and_agent_endpoints_survive_response_provider_switch(config_module):
    previous = {
        **config_module.DEFAULT_CONFIG,
        'llm_provider': 'openai-compatible', 'llm_base_url': 'http://response.test/v1', 'lm_model': 'response',
        'stt_backend': 'llm', 'stt_llm_provider': 'openai-compatible',
        'stt_llm_base_url': 'http://audio.test/v1', 'stt_llm_model': 'audio',
        'agent_base_url': 'http://agent.test/v1', 'agent_model': 'agent',
    }
    current = {**previous, 'llm_provider': 'llama.cpp', 'lm_model': '/models/new.gguf'}
    saved = config_module.synchronize_llm_dependents(current, previous)
    assert saved['stt_llm_base_url'] == 'http://audio.test/v1'
    assert saved['stt_llm_model'] == 'audio'
    assert saved['agent_base_url'] == 'http://agent.test/v1'
    assert saved['agent_model'] == 'agent'


def test_explicit_stt_change_is_preserved_when_saving(config_module):
    previous = {**config_module.DEFAULT_CONFIG, 'stt_backend': 'llm'}
    current = {**previous, 'stt_llm_provider': 'openai-compatible', 'stt_llm_base_url': 'http://audio.test/v1', 'stt_llm_model': 'audio'}
    saved = config_module.synchronize_llm_dependents(current, previous)
    assert saved['stt_llm_model'] == 'audio'
    assert saved['stt_llm_base_url'] == 'http://audio.test/v1'


def test_resolve_llama_cpp_model_id_variants(monkeypatch, model_root):
    from korina.services import model_catalog, provider_manager

    family = model_root / "family"
    family.mkdir(parents=True)
    exact = family / "Exact-Model.Q4.gguf"
    exact.write_text("gguf")
    substring = family / "Gemma-4-E2B_q4_0-it.gguf"
    substring.write_text("gguf")
    (family / "mmproj-Gemma-4-E2B.gguf").write_text("skip mmproj")

    # Patch the data boundary rather than reloading the service: its exception
    # class and activation lock are also held by the imported API routes.
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [model_root])

    assert provider_manager._resolve_llama_cpp_model_id(str(exact)) == str(exact)
    assert provider_manager._resolve_llama_cpp_model_id(exact.name) == str(exact)
    assert provider_manager._resolve_llama_cpp_model_id(exact.stem) == str(exact)
    assert provider_manager._resolve_llama_cpp_model_id("gemma-4-e2b") == str(substring)
    assert provider_manager._resolve_llama_cpp_model_id("") == ""
    assert provider_manager._resolve_llama_cpp_model_id("missing-model") == "missing-model"


def test_provider_supports_model_examples(monkeypatch, model_root, lmstudio_root):
    from korina.services import model_catalog, provider_manager

    local = model_root / "local.gguf"
    local.write_text("gguf")
    manifest_dir = lmstudio_root / "owner" / "repo"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text('{"owner":"owner","name":"repo"}')

    monkeypatch.setattr(model_catalog, 'LMSTUDIO_HUB_ROOT', lmstudio_root)
    monkeypatch.setattr(model_catalog, 'LMS_CLI_BIN', model_root / 'missing-lms')

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
def test_write_llama_server_unit_output_shape(monkeypatch, tmp_path, reasoning):
    from korina.services import provider_manager

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


def test_custom_unit_is_linked_and_managed_by_its_actual_name(monkeypatch, tmp_path):
    from korina.services import provider_manager as manager

    unit = tmp_path / 'custom-units' / 'korina-demo-llm.service'
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', unit)
    monkeypatch.setattr(manager, 'write_llama_server_unit', lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, 'wait_for_llama_server_ready', lambda: None)
    calls = []
    def systemctl(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(manager, '_systemctl', systemctl)
    manager.start_llama_server('demo.gguf')
    manager.stop_llama_server()
    assert ('link', str(unit)) in calls
    assert ('restart', 'korina-demo-llm.service') in calls
    assert ('stop', 'korina-demo-llm.service') in calls


def test_optional_teardown_works_without_systemd(monkeypatch):
    from korina.services import provider_manager as manager

    def missing(*args, **kwargs):
        raise FileNotFoundError('systemctl')

    monkeypatch.setattr(manager.subprocess, 'run', missing)
    manager.stop_llama_server()
    assert manager._systemctl('stop', 'ollama.service').returncode == 127
    with pytest.raises(RuntimeError, match='requires systemd'):
        manager._systemctl('restart', 'llama-server.service', required=True)


def test_explicit_missing_model_does_not_select_a_different_file(monkeypatch, tmp_path):
    from korina.services import provider_manager as manager

    other = tmp_path / 'other.gguf'
    other.touch()
    monkeypatch.setattr(manager, 'discover_local_gguf_models', lambda: [str(other)])
    with pytest.raises(RuntimeError, match='Requested llama.cpp model not found'):
        manager.activate_llm_provider('llama.cpp', {}, model='missing-model.gguf')
