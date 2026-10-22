"""Exercise wrapper failures with a fake user manager, never real services."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def setup_runtime(tmp_path, *, tts='custom', provider='openai-compatible'):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    (runtime / 'config.json').write_text(json.dumps({
        'llm_provider': provider, 'lm_model': '/missing-fixture.gguf', 'tts_provider': tts,
    }))
    binary = tmp_path / 'bin'
    binary.mkdir()
    (binary / 'systemctl').write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_LOG"
if [ "$2" = cat ] && [ "$3" = "${MISSING_UNIT:-}" ]; then exit 1; fi
if [ "$2" = start ] && [ "$3" = "${FAILING_UNIT:-}" ]; then exit 1; fi
exit 0
''')
    # A one-iteration wait drives the existing loop through timeout promptly;
    # fake listeners keep this test independent of services on the host.
    for name, body in [('ss', 'exit 0'), ('sleep', 'exit 0'), ('seq', 'echo 1')]:
        (binary / name).write_text('#!/bin/sh\n' + body + '\n')
    for path in binary.iterdir():
        path.chmod(0o755)
    return dict(os.environ, KORINA_APP_DIR=str(runtime), KORINA_PYTHON=os.path.abspath(sys.executable),
                XDG_CONFIG_HOME=str(tmp_path / 'config'),
                FAKE_SYSTEMCTL_LOG=str(tmp_path / 'systemctl.log'),
                PATH=str(binary) + os.pathsep + os.environ['PATH'])


def start(env, *args):
    return subprocess.run(['bash', str(REPO / 'Korina/start.sh'), *args], env=env,
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('unit', ['korina-voice-lab.service', 'kokoro-streaming-server.service'])
def test_missing_required_unit_fails_before_any_start_or_provider_activation(tmp_path, unit):
    env = setup_runtime(tmp_path, tts='kokoro', provider='llama.cpp')
    env['MISSING_UNIT'] = unit
    result = start(env, '--no-wait')
    assert result.returncode != 0
    assert unit in result.stderr
    assert 'Activating local LLM' not in result.stdout
    assert 'Bundled Korina page:' not in result.stdout
    assert ' start ' not in Path(env['FAKE_SYSTEMCTL_LOG']).read_text()


def test_start_command_failure_does_not_report_success(tmp_path):
    env = setup_runtime(tmp_path)
    env['FAILING_UNIT'] = 'korina-voice-lab.service'
    result = start(env, '--no-wait')
    assert result.returncode != 0
    assert 'could not start korina-voice-lab.service' in result.stderr
    assert 'Start commands completed' not in result.stdout
    assert 'Bundled Korina page:' not in result.stdout


def test_failed_readiness_is_nonzero_and_no_wait_explicitly_skips_it(tmp_path):
    env = setup_runtime(tmp_path)
    waited = start(env)
    assert waited.returncode != 0
    assert 'TIMEOUT: Korina' in waited.stderr
    assert 'Bundled Korina page:' not in waited.stdout
    no_wait = start(env, '--no-wait')
    assert no_wait.returncode == 0
    assert 'readiness was not checked' in no_wait.stdout
    assert 'TIMEOUT' not in no_wait.stderr


def test_dry_run_creates_no_config_and_calls_no_manager(tmp_path):
    env = setup_runtime(tmp_path, tts='kokoro', provider='llama.cpp')
    config = Path(env['KORINA_APP_DIR']) / 'config.json'
    config.unlink()
    result = start(env, '--dry-run')
    assert result.returncode == 0
    assert 'no services were started' in result.stdout
    assert not config.exists()
    assert not Path(env['FAKE_SYSTEMCTL_LOG']).exists()


def test_no_wait_preserves_customized_unit_and_does_not_advertise_shell_bind(tmp_path):
    env = setup_runtime(tmp_path)
    env['KORINA_PORT'] = '43210'
    unit = Path(env['XDG_CONFIG_HOME']) / 'systemd/user/korina-voice-lab.service'
    unit.parent.mkdir(parents=True)
    content = '[Service]\nEnvironment=KORINA_PORT=45001\n'
    unit.write_text(content)
    result = start(env, '--no-wait')
    assert result.returncode == 0
    assert unit.read_text() == content
    assert 'http://127.0.0.1:8001/' in result.stdout
    assert '43210' not in result.stdout
    assert 'custom units may use another address' in result.stdout


@pytest.mark.parametrize('script', ['start.sh', 'stop.sh'])
def test_invalid_explicit_interpreter_does_not_reach_service_manager(tmp_path, script):
    env = setup_runtime(tmp_path)
    env['KORINA_PYTHON'] = str(tmp_path / 'missing-python')
    result = subprocess.run(['bash', str(REPO / 'Korina' / script), '--dry-run'], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'KORINA_PYTHON' in result.stderr
    assert not Path(env['FAKE_SYSTEMCTL_LOG']).exists()
