"""Response activation must preserve services used by other active roles."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from korina.services import provider_manager as manager
from korina.services import provider_processes as processes


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    calls = []
    paths = {}
    real_lms_start = manager.start_lmstudio_server
    for attr, name in [('LLAMA_SERVER_BIN', 'llama-server'), ('LMSTUDIO_BIN', 'lm-studio'), ('LMS_CLI_BIN', 'lms')]:
        path = tmp_path / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        paths[attr] = path
        monkeypatch.setattr(manager, attr, path)
    for name in ('old.gguf', 'new.gguf'):
        (tmp_path / name).write_text('fixture')
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', tmp_path / 'demo-llama.service')
    monkeypatch.setattr(manager.shutil, 'which', lambda name: str(tmp_path / name))
    monkeypatch.setattr(manager.provider_processes, 'user_processes', lambda: iter([]))
    monkeypatch.setattr(manager, '_is_lmstudio_server_already_up', lambda: True)
    monkeypatch.setattr(manager, 'provider_supports_model', lambda provider, model: (True, 'fixture'))

    def stop(name):
        def run():
            calls.append('stop ' + name)
            return manager.LifecycleActions(stopped=[name])
        return run

    for name in ('llama_server', 'lmstudio_server', 'lmstudio', 'ollama'):
        monkeypatch.setattr(manager, 'stop_' + name, stop(name))

    def start(name):
        def run(*args, **kwargs):
            calls.append('start ' + name)
            return manager.LifecycleActions(started=[name])
        return run

    for name in ('llama_server', 'lmstudio_server', 'ollama'):
        monkeypatch.setattr(manager, 'start_' + name, start(name))

    def stray(ports=None):
        calls.append(('stray cleanup', ports))
        return manager.LifecycleActions()

    monkeypatch.setattr(manager, '_kill_stray_llama_servers', stray)
    return SimpleNamespace(calls=calls, paths=paths, root=tmp_path, real_lms_start=real_lms_start)


def config(**changes):
    return {
        'llm_provider': 'openai-compatible', 'llm_base_url': 'https://response.test/v1', 'lm_model': 'response',
        'stt_backend': 'whisper', 'agent_enabled': 'off', **changes,
    }


def retained(result):
    return {item['provider']: item['roles'] for item in result['retained']}


def test_generic_switch_retains_all_active_local_roles_without_starting_extra_models(lifecycle):
    result = manager.activate_llm_provider('openai-compatible', config(
        llm_base_url='http://localhost:8080/v1', stt_backend='llm',
        stt_llm_base_url='http://127.0.0.1:1234/v1', stt_llm_model='audio',
        agent_enabled='on', agent_base_url='http://localhost:11434/v1', agent_model='agent',
    ))
    assert lifecycle.calls == []
    assert result['started'] == result['stopped'] == []
    assert retained(result) == {'llama.cpp': ['response_llm'], 'lmstudio': ['stt'], 'ollama': ['agent']}
    assert all('active loopback endpoint' in item['reason'] for item in result['retained'])


@pytest.mark.parametrize('url', [
    'http://localhost:1234/v1', 'http://LOCALHOST.:1234/v1', 'http://127.0.0.1:1234/v1',
    'http://127.8.0.1:1234/v1', 'http://[::1]:1234/v1', 'http://[::ffff:127.0.0.1]:1234/v1',
])
def test_actual_loopback_endpoint_protects_service_independent_of_provider_label(lifecycle, url):
    result = manager.activate_llm_provider('openai-compatible', config(
        stt_backend='llm', stt_llm_provider='openai-compatible', stt_llm_base_url=url, stt_llm_model='audio',
    ))
    assert retained(result) == {'lmstudio': ['stt']}
    assert 'stop lmstudio_server' not in lifecycle.calls
    assert not any(isinstance(call, tuple) and call[0] == 'stray cleanup' for call in lifecycle.calls)


@pytest.mark.parametrize('url', ['https://remote.test:1234/v1', 'http://localhost.remote.test:1234/v1', 'http://192.0.2.1:1234/v1'])
def test_remote_endpoint_does_not_keep_unrelated_local_service(lifecycle, url):
    result = manager.activate_llm_provider('openai-compatible', config(stt_backend='llm', stt_llm_base_url=url))
    assert retained(result) == {}
    assert 'stop lmstudio_server' in lifecycle.calls


def test_disabled_roles_do_not_pin_idle_services(lifecycle):
    result = manager.activate_llm_provider('openai-compatible', config(
        stt_backend='whisper', stt_llm_base_url='http://localhost:1234/v1',
        agent_enabled='off', agent_base_url='http://localhost:11434/v1',
    ))
    assert retained(result) == {}
    assert set(result['stopped']) == {'llama_server', 'lmstudio_server', 'ollama'}
    assert result['started'] == []


def test_managed_executable_on_custom_port_is_retained(lifecycle, monkeypatch):
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([
        (42, lifecycle.paths['LMSTUDIO_BIN'], [str(lifecycle.paths['LMSTUDIO_BIN'])]),
    ]))
    monkeypatch.setattr(processes, 'listens_on', lambda pid, port: pid == 42 and port == 9000)
    result = manager.activate_llm_provider('openai-compatible', config(
        agent_enabled='on', agent_base_url='http://127.0.0.1:9000/v1', agent_model='agent',
    ))
    assert retained(result) == {'lmstudio': ['agent']}
    assert 'stop lmstudio_server' not in lifecycle.calls


def test_lmstudio_real_start_sequence_has_no_hidden_llama_teardown(lifecycle, monkeypatch):
    monkeypatch.setattr(manager, 'start_lmstudio_server', lifecycle.real_lms_start)
    monkeypatch.setattr(manager, 'wait_for_lmstudio_server_ready', lambda: None)
    monkeypatch.setattr(manager, '_is_lmstudio_model_loaded', lambda model: True)
    result = manager.activate_llm_provider('lmstudio', config(
        lm_model='studio-response', stt_backend='llm', stt_llm_base_url='http://localhost:8080/v1',
        stt_llm_model=str(lifecycle.root / 'old.gguf'),
    ))
    assert 'stop llama_server' not in lifecycle.calls
    assert retained(result) == {'llama.cpp': ['stt']}
    assert result['started'] == [] and result['reused'] == ['lm-studio-server']
    assert result['loaded_models'] == []


def test_single_model_replacement_conflict_precedes_all_teardown(lifecycle):
    with pytest.raises(manager.ProviderRoleConflict, match='single llama.cpp model'):
        manager.activate_llm_provider('llama.cpp', config(
            lm_model=str(lifecycle.root / 'new.gguf'), stt_backend='llm',
            stt_llm_base_url='http://localhost:8080/v1', stt_llm_model=str(lifecycle.root / 'old.gguf'),
        ))
    assert lifecycle.calls == []


def test_existing_same_model_used_by_agent_is_reused_without_restart(lifecycle, monkeypatch):
    selected = str(lifecycle.root / 'old.gguf')
    monkeypatch.setattr(manager, 'request_json', lambda *args, **kwargs: {'data': [{'id': selected}]})
    result = manager.activate_llm_provider('llama.cpp', config(
        lm_model=selected, agent_enabled='on', agent_base_url='http://localhost:8080/v1', agent_model=selected,
    ))
    assert 'start llama_server' not in lifecycle.calls
    assert result['reused'] == ['demo-llama.service']
    assert result['started'] == []


@pytest.mark.parametrize('port', [8080, 9000])
@pytest.mark.parametrize('reply', [None, {'data': [{'id': 'uncertain-alias'}]}, {'data': None}])
def test_running_protected_llama_is_not_restarted_after_uncertain_probe(lifecycle, monkeypatch, port, reply):
    selected = str(lifecycle.root / 'old.gguf')
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([
        (42, lifecycle.paths['LLAMA_SERVER_BIN'], [str(lifecycle.paths['LLAMA_SERVER_BIN'])]),
    ]))
    monkeypatch.setattr(processes, 'listens_on', lambda pid, listening_port: pid == 42 and listening_port == port)

    def probe(*args, **kwargs):
        if reply is None:
            raise TimeoutError('temporary model probe failure')
        return reply

    monkeypatch.setattr(manager, 'request_json', probe)
    with pytest.raises(manager.ProviderRoleConflict, match='could not be confirmed reusable'):
        manager.activate_llm_provider('llama.cpp', config(
            lm_model=selected, agent_enabled='on', agent_base_url=f'http://localhost:{port}/v1', agent_model=selected,
        ))
    assert lifecycle.calls == []


@pytest.mark.parametrize('ports,role_port', [({1234}, 1234), ({1234, 8080}, 8080)])
def test_protected_listener_conflict_precedes_port_cleanup(lifecycle, monkeypatch, ports, role_port):
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([
        (42, lifecycle.paths['LLAMA_SERVER_BIN'], [str(lifecycle.paths['LLAMA_SERVER_BIN'])]),
    ]))
    monkeypatch.setattr(processes, 'listens_on', lambda pid, port: pid == 42 and port in ports)
    with pytest.raises(manager.ProviderRoleConflict, match='Port 1234'):
        manager.activate_llm_provider('lmstudio', config(
            lm_model='studio-response', agent_enabled='on', agent_base_url=f'http://localhost:{role_port}/v1',
            agent_model='agent',
        ))
    assert lifecycle.calls == []


@pytest.mark.parametrize('missing,provider', [
    ('LLAMA_SERVER_BIN', 'llama.cpp'), ('LMS_CLI_BIN', 'lmstudio'), ('LMSTUDIO_BIN', 'lmstudio'),
])
def test_missing_startup_executable_is_detected_before_teardown(lifecycle, monkeypatch, missing, provider):
    lifecycle.paths[missing].unlink()
    monkeypatch.setattr(manager, '_is_lmstudio_server_already_up', lambda: False)
    with pytest.raises(RuntimeError, match='executable'):
        manager.activate_llm_provider(provider, config(lm_model=str(lifecycle.root / 'new.gguf')))
    assert lifecycle.calls == []


def test_missing_ollama_or_unsupported_provider_is_detected_before_teardown(lifecycle, monkeypatch):
    monkeypatch.setattr(manager.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='not installed'):
        manager.activate_llm_provider('ollama', config())
    with pytest.raises(ValueError, match='Unsupported response provider'):
        manager.activate_llm_provider('typo', config())
    assert lifecycle.calls == []


def test_non_lmstudio_listener_cannot_hide_missing_gui_before_teardown(lifecycle, monkeypatch):
    lifecycle.paths['LMSTUDIO_BIN'].unlink()
    ollama = lifecycle.root / 'ollama'
    monkeypatch.setattr(processes, 'user_processes', lambda: iter([(42, ollama, [str(ollama), 'serve'])]))
    monkeypatch.setattr(processes, 'listens_on', lambda pid, port: pid == 42 and port == 1234)
    # The competing provider responds200 but will be stopped by activation.
    monkeypatch.setattr(manager, '_is_lmstudio_server_already_up', lambda: True)
    with pytest.raises(RuntimeError, match='LM Studio executable'):
        manager.activate_llm_provider('lmstudio', config(lm_model='studio-response'))
    assert lifecycle.calls == []


def test_stop_command_failure_is_not_reported_as_a_confirmed_stop(monkeypatch):
    monkeypatch.setattr(manager, '_systemctl', lambda *args, **kwargs: SimpleNamespace(returncode=5))
    result = manager._unit_stopped('fixture', 'fixture.service')
    assert result.stopped == []
    assert 'exit 5' in result.warnings[0]


def test_partial_termination_never_claims_provider_stopped(monkeypatch):
    monkeypatch.setattr(processes, 'wait_for_exit', lambda pids: [])
    result = manager._terminated('fixture', processes.TerminationResult(signalled=[1], failed=[2]))
    assert result.stopped == []
    assert result.stop_requested == ['fixture']
    assert any('permission denied' in message for message in result.warnings)


def test_termination_exit_timeout_is_reported_as_requested_not_stopped(monkeypatch):
    monkeypatch.setattr(processes, 'wait_for_exit', lambda pids: pids)
    result = manager._terminated('fixture', processes.TerminationResult(signalled=[1]))
    assert result.stopped == [] and result.stop_requested == ['fixture']
    assert result.warnings
