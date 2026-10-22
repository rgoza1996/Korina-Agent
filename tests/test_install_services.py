from __future__ import annotations

import os
import json
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def service_environment(tmp_path):
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    systemctl = binary_dir / 'systemctl'
    systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_SYSTEMCTL_LOG"\nexit 0\n')
    systemctl.chmod(0o755)
    env = dict(os.environ, PATH=str(binary_dir) + os.pathsep + os.environ['PATH'],
               XDG_CONFIG_HOME=str(tmp_path / 'config home'), KORINA_PYTHON=os.path.abspath(sys.executable),
               FAKE_SYSTEMCTL_LOG=str(tmp_path / 'systemctl.log'))
    env.pop('KORINA_TTS_PYTHON', None)
    return env


def unit_command(unit):
    line = next(line for line in unit.read_text().splitlines() if line.startswith('ExecStart='))
    # These templates use ordinary quoted argv with systemd's literal %/$
    # escaping. No shell is involved in either the service or this probe.
    return [arg.replace('%%', '%').replace('$$', '$') for arg in shlex.split(line.partition('=')[2])]


def test_install_services_renders_checkout_and_interpreter(tmp_path):
    env = service_environment(tmp_path)
    config_home = tmp_path / 'config home'
    app_dir = tmp_path / 'voice $runtime%'
    interpreter = os.path.abspath(sys.executable)
    env['KORINA_APP_DIR'] = str(app_dir)
    subprocess.run(['bash', str(REPO / 'Korina/install-services.sh')], env=env,
                   check=True, capture_output=True, text=True)
    unit_dir = config_home / 'systemd/user'
    app = (unit_dir / 'korina-voice-lab.service').read_text()
    tts = (unit_dir / 'kokoro-streaming-server.service').read_text()
    assert f'WorkingDirectory={REPO}' in app
    assert unit_command(unit_dir / 'korina-voice-lab.service')[1:] == [interpreter, '-m', 'korina.app']
    assert f'Environment="KORINA_APP_DIR={str(app_dir).replace("%", "%%")}"' in app
    assert '@' not in app + tts
    assert f'"{REPO}/kokoro-streaming-server.py"' in tts


@pytest.mark.parametrize('runtime_input', ['runtime $alias%', '~/runtime $alias%', 'runtime $alias%/../chosen'])
def test_relative_runtime_uses_original_caller_and_preserves_symlink_traversal(tmp_path, runtime_input):
    env = service_environment(tmp_path)
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    for name in ('Korina', 'korina', 'deploy'):
        shutil.copytree(REPO / name, checkout / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'config.json', '*.wav'))
    caller = tmp_path / 'caller'
    caller.mkdir()
    storage = tmp_path / 'storage'
    for name in ('runtime', 'chosen'):
        shutil.copytree(REPO / 'Korina', storage / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'config.json', '*.wav'))
        (storage / name / 'config.json').write_text(json.dumps({
            'voice': 'sentinel-' + name, 'ack_enabled': 'off', 'agent_enabled': 'off',
        }))
    alias = caller / 'runtime $alias%'
    alias.symlink_to(storage / 'runtime', target_is_directory=True)
    env.update(HOME=str(caller), KORINA_APP_DIR=runtime_input)
    subprocess.run(['bash', str(checkout / 'Korina/install-services.sh')], cwd=caller, env=env,
                   check=True, capture_output=True, text=True)
    unit = Path(env['XDG_CONFIG_HOME']) / 'systemd/user/korina-voice-lab.service'
    line = next(line for line in unit.read_text().splitlines() if 'KORINA_APP_DIR=' in line)
    rendered = shlex.split(line.partition('=')[2])[0].partition('=')[2].replace('%%', '%')
    expected = caller / runtime_input.removeprefix('~/')
    assert rendered == str(expected), 'retain the alias and .. rather than resolving or collapsing them'
    service_env = dict(env, KORINA_APP_DIR=rendered)
    result = subprocess.run([sys.executable, '-c', '''
import json
from fastapi.testclient import TestClient
from korina.app_factory import create_app
from korina.config import load_config
from korina.util.paths import CONFIG_PATH
with TestClient(create_app()) as client:
    response = client.get('/')
    print(json.dumps({'status': response.status_code, 'voice': load_config()['voice'],
                      'config_path': str(CONFIG_PATH.resolve())}))
'''], cwd=checkout, env=service_env, capture_output=True, text=True, check=True, timeout=10)
    payload = json.loads(result.stdout)
    selected = 'chosen' if '..' in runtime_input else 'runtime'
    assert payload == {'status': 200, 'voice': 'sentinel-' + selected,
                       'config_path': str(storage / selected / 'config.json')}
    assert not (checkout / 'runtime $alias%').exists()


@pytest.mark.parametrize('setting', ['KORINA_PYTHON', 'KORINA_TTS_PYTHON'])
@pytest.mark.parametrize('invalid', ['missing', 'directory', 'not-python'])
def test_invalid_explicit_interpreter_aborts_before_changes(tmp_path, setting, invalid):
    env = service_environment(tmp_path)
    candidate = tmp_path / invalid
    if invalid == 'directory':
        candidate.mkdir()
    elif invalid == 'not-python':
        candidate.write_text('#!/bin/sh\nexit 0\n')
        candidate.chmod(0o755)
    env[setting] = str(candidate)
    unit = Path(env['XDG_CONFIG_HOME']) / 'systemd/user/korina-voice-lab.service'
    unit.parent.mkdir(parents=True)
    unit.write_text('existing customized unit\n')
    result = subprocess.run(['bash', str(REPO / 'Korina/install-services.sh')], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert setting in result.stderr
    assert unit.read_text() == 'existing customized unit\n'
    assert not (unit.parent / 'kokoro-streaming-server.service').exists()
    assert not Path(env['FAKE_SYSTEMCTL_LOG']).exists()


def test_special_checkout_and_relative_tts_interpreter_run_rendered_services(tmp_path):
    env = service_environment(tmp_path)
    checkout = tmp_path / 'checkout space $cash%percent\\backslash"quote'
    checkout.mkdir()
    for name in ('Korina', 'korina', 'deploy'):
        shutil.copytree(REPO / name, checkout / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'config.json', '*.wav'))
    shutil.copy2(REPO / 'kokoro-streaming-server.py', checkout)
    # A directory alias retains pyvenv.cfg/site-packages while exercising the
    # exact executable path: resolving bin/python would lose the venv.
    primary = tmp_path / 'venv space $cash%percent\\backslash"quote'
    secondary = tmp_path / 'tts venv $cash%percent\\backslash"quote'
    for alias in (primary, secondary):
        alias.symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
    env['KORINA_PYTHON'] = str(primary / 'bin' / Path(sys.executable).name)
    tts_python = secondary / 'bin' / Path(sys.executable).name
    env['KORINA_TTS_PYTHON'] = os.path.relpath(tts_python, tmp_path)
    env['KORINA_APP_DIR'] = str(checkout / 'Korina')
    config = {'ack_enabled': 'off', 'agent_enabled': 'off', 'stt_backend': 'whisper',
              'llm_provider': 'openai-compatible', 'llm_base_url': 'http://127.0.0.1:9/v1'}
    (checkout / 'Korina/config.json').write_text(json.dumps(config))
    subprocess.run(['bash', str(checkout / 'Korina/install-services.sh')], cwd=tmp_path, env=env,
                   check=True, capture_output=True, text=True)
    units = Path(env['XDG_CONFIG_HOME']) / 'systemd/user'
    app_unit = units / 'korina-voice-lab.service'
    tts_unit = units / 'kokoro-streaming-server.service'
    app_command, tts_command = unit_command(app_unit), unit_command(tts_unit)
    assert Path(app_command[0]).name == 'env'
    assert app_command[1] == env['KORINA_PYTHON']
    assert tts_command[1] == str(tts_python)
    assert tts_command[2] == str(checkout / 'kokoro-streaming-server.py')
    if shutil.which('systemd-analyze'):
        subprocess.run(['systemd-analyze', 'verify', '--man=no', '--generators=no', str(app_unit), str(tts_unit)],
                       check=True, capture_output=True, text=True)
    for command, variable, route in ((app_command, 'KORINA_PORT', '/'), (tts_command, 'KORINA_TTS_PORT', '/voices')):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        # Run the rendered argv in the foreground on disposable ports. This
        # verifies entrypoints/interpreters without operating a user manager.
        child_env = dict(env, **{variable: str(port)})
        with (tmp_path / f'{variable}.log').open('w') as log:
            child = subprocess.Popen(command, cwd=checkout, env=child_env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}{route}', timeout=.5) as response:
                            assert response.status == 200
                            assert response.read(1024)
                        break
                    except OSError:
                        assert child.poll() is None, (tmp_path / f'{variable}.log').read_text()
                        assert time.monotonic() < deadline, 'rendered service did not start'
                        time.sleep(.05)
            finally:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
