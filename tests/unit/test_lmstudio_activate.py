from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

from korina import config as config_mod

import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def fake_lms(tmp_path, monkeypatch):
    """Replace the LMS CLI with a stub that logs every argv and exits 0.

    The stub's `server start` action also launches a tiny HTTP server bound to
    127.0.0.1:<port> that responds 200 OK on `/v1/models`, so the readiness
    poll in `wait_for_lmstudio_server_ready` finds a live endpoint without
    touching the real LM Studio install.
    """
    log_path = tmp_path / 'lms.log'
    port = _free_port()

    fake = tmp_path / 'lms'
    fake.write_text(
        '#!/usr/bin/env bash\n'
        f'echo "$@" >> "{log_path}"\n'
        # First three positional args after the script path identify the action.
        'if [ "$1" = "server" ] && [ "$2" = "start" ]; then\n'
        f'  python3 -c "import http.server,socketserver,threading,os;\\n'
        f'class H(http.server.BaseHTTPRequestHandler):\\n'
        f'  def do_GET(self): self.send_response(200); self.send_header(\'Content-Type\',\'application/json\'); self.end_headers(); self.wfile.write(b\'{{\\"data\\":[{{\\"id\\":\\"fake\\"}}]}}\')\\n'
        f'  def log_message(self,*a): pass\\n'
        f'socketserver.TCPServer((\'127.0.0.1\',{port}),H).serve_forever()" &>/dev/null &\n'
        'fi\n'
        'exit 0\n'
    )
    fake.chmod(0o755)

    # Point korina at the fake lms. Re-import paths so LMS_CLI_BIN picks up the
    # new env var.
    monkeypatch.setenv('KORINA_LMS_CLI_BIN', str(fake))
    import importlib
    import korina.util.paths as paths_mod
    importlib.reload(paths_mod)
    import korina.services.provider_manager as pm_mod
    importlib.reload(pm_mod)
    # Patch the module-level references too so the helpers we reloaded see the
    # new LMS_CLI_BIN.
    monkeypatch.setattr(pm_mod, 'LMS_CLI_BIN', paths_mod.LMS_CLI_BIN)
    monkeypatch.setattr(pm_mod, 'LMSTUDIO_BIN', paths_mod.LMSTUDIO_BIN)
    monkeypatch.setattr(pm_mod, 'LLAMA_SERVER_BIN', paths_mod.LLAMA_SERVER_BIN)
    monkeypatch.setattr(pm_mod, 'LLAMA_SERVER_USER_UNIT', paths_mod.LLAMA_SERVER_USER_UNIT)

    # Also patch the symbols that the test client uses (imports made at module
    # load time of routes, etc.).
    import korina.routes.providers as prov_mod
    monkeypatch.setattr(prov_mod, 'activate_llm_provider', pm_mod.activate_llm_provider)

    fake_catalog = ['qwen/qwen3.5-2b', 'gemma/gemma-4-e4b-it']
    from korina.services import model_catalog as mc_mod
    monkeypatch.setattr(mc_mod, 'discover_lmstudio_catalog_models', lambda: list(fake_catalog))

    yield {'lms': fake, 'log': log_path, 'port': port, 'pm': pm_mod, 'paths': paths_mod, 'catalog': fake_catalog}


def test_lms_branch_runs_lms_server_start_with_correct_args(fake_lms, isolated_korina_app_dir, client, monkeypatch):
    cfg = config_mod.load_config()
    cfg['llm_provider'] = 'lmstudio'
    cfg['lm_model'] = 'qwen/qwen3.5-2b'
    cfg['llm_base_url'] = 'http://127.0.0.1:1234/v1'
    config_mod.save_config(cfg)

    # Stub the systemd-touching helpers so the test does not actually start or
    # stop any system service.
    monkeypatch.setattr(fake_lms['pm'], 'stop_llama_server', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'stop_ollama', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'start_lmstudio', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'start_llama_server', lambda *a, **k: None)
    monkeypatch.setattr(fake_lms['pm'], '_kill_stray_llama_servers', lambda: None)

    # The readiness wait polls 127.0.0.1:1234; override to use the fake port.
    monkeypatch.setattr(fake_lms['pm'], 'wait_for_lmstudio_server_ready', lambda *a, **k: None)

    r = client.post('/api/llm/provider/activate', json={'provider': 'lmstudio', 'model': 'qwen/qwen3.5-2b'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['activation']['provider'] == 'lmstudio'
    assert body['activation']['started'] == ['lm-studio-server']
    assert 'stray-llama-server' in body['activation']['stopped']

    log_lines = fake_lms['log'].read_text().splitlines()
    # Order matters: server stop, server start --bind 0.0.0.0 --cors, load <model> --yes
    assert any('server stop' in line for line in log_lines)
    assert any('server start --port 1234 --bind 0.0.0.0 --cors' in line for line in log_lines)
    assert any('load qwen/qwen3.5-2b --yes' in line for line in log_lines)


def test_lms_branch_surfaces_lms_load_error(fake_lms, isolated_korina_app_dir, tmp_path, client, monkeypatch):
    """If the lms CLI fails, the activate endpoint must return 500 with the
    stderr reason, not a generic 'activation failed'."""
    cfg = config_mod.load_config()
    cfg['llm_provider'] = 'lmstudio'
    cfg['lm_model'] = 'qwen/qwen3.5-2b'
    config_mod.save_config(cfg)

    failing_lms = tmp_path / 'lms-fail'
    failing_lms.write_text(
        '#!/usr/bin/env bash\n'
        'if [ "$1" = "load" ]; then echo "load failure simulated" >&2; exit 2; fi\n'
        'exit 0\n'
    )
    failing_lms.chmod(0o755)

    monkeypatch.setattr(fake_lms['pm'], 'LMS_CLI_BIN', failing_lms)
    monkeypatch.setattr(fake_lms['pm'], 'stop_llama_server', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'stop_ollama', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'start_lmstudio', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], '_kill_stray_llama_servers', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'wait_for_lmstudio_server_ready', lambda *a, **k: None)

    r = client.post('/api/llm/provider/activate', json={'provider': 'lmstudio', 'model': 'qwen/qwen3.5-2b'})
    assert r.status_code == 500, r.text
    detail = r.json()['detail']
    assert 'lms load' in detail
    assert 'load failure simulated' in detail


def test_kill_stray_llama_servers_invokes_pkill(isolated_korina_app_dir, monkeypatch):
    from korina.services import provider_manager as pm
    calls = []
    monkeypatch.setattr(pm.subprocess, 'run',
                        lambda *a, **k: calls.append(a[0]) or subprocess.CompletedProcess(a[0], 0, '', ''))
    pm._kill_stray_llama_servers()
    assert calls and 'pkill' in calls[0] and '--port 1234' in ' '.join(calls[0])
    assert '-f' in calls[0]


def test_llama_cpp_branch_also_kills_stray_servers(fake_lms, isolated_korina_app_dir, model_root, client, monkeypatch):
    cfg = config_mod.load_config()
    cfg['llm_provider'] = 'llama.cpp'
    cfg['lm_model'] = ''
    config_mod.save_config(cfg)

    stray_called = []
    monkeypatch.setattr(fake_lms['pm'], '_kill_stray_llama_servers', lambda: stray_called.append('stray'))
    monkeypatch.setattr(fake_lms['pm'], 'stop_llama_server', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'stop_ollama', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'start_llama_server', lambda *a, **k: None)
    monkeypatch.setattr(fake_lms['pm'], 'wait_for_llama_server_ready', lambda *a, **k: None)

    gguf = model_root / 'fake-llama-cpp-test.gguf'
    gguf.write_text('gguf')

    r = client.post('/api/llm/provider/activate', json={'provider': 'llama.cpp', 'model': str(gguf)})
    assert r.status_code == 200, r.text
    assert stray_called, 'expected stray-llama-server kill on llama.cpp activate'
    assert 'stray-llama-server' in r.json()['activation']['stopped']


def test_openai_compatible_branch_also_tears_down_servers(fake_lms, isolated_korina_app_dir, client, monkeypatch):
    cfg = config_mod.load_config()
    cfg['llm_provider'] = 'openai-compatible'
    cfg['llm_base_url'] = 'http://127.0.0.1:9999/v1'
    config_mod.save_config(cfg)

    stray_called = []
    monkeypatch.setattr(fake_lms['pm'], '_kill_stray_llama_servers', lambda: stray_called.append('stray'))
    monkeypatch.setattr(fake_lms['pm'], 'stop_llama_server', lambda: None)
    monkeypatch.setattr(fake_lms['pm'], 'stop_ollama', lambda: None)

    r = client.post('/api/llm/provider/activate', json={'provider': 'openai-compatible'})
    assert r.status_code == 200, r.text
    assert stray_called, 'expected stray-llama-server kill on openai-compatible activate'
    assert 'stray-llama-server' in r.json()['activation']['stopped']