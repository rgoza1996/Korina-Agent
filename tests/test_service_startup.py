"""Exercise wrapper failures with a fake user manager, never real services."""
from __future__ import annotations

import json
import os
import shutil
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
if [ "$2" = cat ] && [ "${ALL_UNITS_MISSING:-0}" = 1 ]; then exit 1; fi
if [ "$2" = start ] && [ "$3" = "${FAILING_UNIT:-}" ]; then exit 1; fi
if [ "$2" = stop ] && [ "$3" = "${FAILING_STOP_UNIT:-}" ]; then exit 1; fi
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


def copied_service_checkout(tmp_path):
    """The copied provider fixture can never supervise actual host processes."""
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    for name in ('Korina', 'korina'):
        shutil.copytree(REPO / name, checkout / name,
                        ignore=shutil.ignore_patterns('__pycache__', 'config.json', '*.wav'))
    (checkout / 'korina/services/provider_manager.py').write_text('''
import json
import os
from pathlib import Path
from types import SimpleNamespace

def record(action, **kwargs):
    with Path(os.environ['PROVIDER_LOG']).open('a') as log:
        log.write(json.dumps({'action': action, 'runtime': os.environ['KORINA_APP_DIR'], **kwargs}) + '\\n')
    with Path(os.environ['FAKE_SYSTEMCTL_LOG']).open('a') as log:
        log.write('provider ' + action + '\\n')

def activate_llm_provider(provider, config, model=None):
    record('activate', voice=config['voice'])
    return {}

def stop_lmstudio_server():
    record('lmstudio-server')
    outcome = os.environ.get('PROVIDER_OUTCOME', 'success')
    if outcome == 'exception':
        raise OSError('fixture stop failed')
    return SimpleNamespace(warnings=['fixture stop unconfirmed'] if outcome == 'warning' else [],
                           stop_requested=['fixture-process'] if outcome == 'unconfirmed' else [])

def stop_lmstudio():
    record('lmstudio')
    return SimpleNamespace(warnings=[], stop_requested=[])

def stop_ollama(*, stop_service=True):
    assert stop_service is False, 'the wrapper owns unit eligibility'
    record('ollama-process')
    return SimpleNamespace(warnings=[], stop_requested=[])
''')
    return checkout


def stop_fixture(tmp_path):
    env = setup_runtime(tmp_path)
    checkout = copied_service_checkout(tmp_path)
    env['PROVIDER_LOG'] = str(tmp_path / 'providers.jsonl')
    env['KORINA_LLAMA_SERVER_USER_UNIT'] = str(tmp_path / 'custom-llama.service')
    return env, checkout


def stopped(env, checkout, *args, cwd=None):
    return subprocess.run(['bash', str(checkout / 'Korina/stop.sh'), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=10)


def test_stop_reports_unit_failure_after_attempting_remaining_cleanup(tmp_path):
    env, checkout = stop_fixture(tmp_path)
    env['FAILING_STOP_UNIT'] = 'korina-voice-lab.service'
    result = stopped(env, checkout)
    assert result.returncode != 0
    assert 'stop command failed' in result.stderr
    assert 'shutdown incomplete' in result.stderr
    commands = Path(env['FAKE_SYSTEMCTL_LOG']).read_text()
    for unit in ['korina-voice-lab.service', 'kokoro-streaming-server.service',
                 'custom-llama.service', 'llama-server.service', 'ollama.service']:
        assert '--user stop ' + unit in commands
        assert commands.index('--user stop ' + unit) < commands.index('provider lmstudio-server'), (
            'stop every owning unit before entering provider process fallback, even when a stop fails'
        )
    assert [json.loads(line)['action'] for line in Path(env['PROVIDER_LOG']).read_text().splitlines()] == [
        'lmstudio-server', 'lmstudio', 'ollama-process',
    ]


@pytest.mark.parametrize('outcome', ['exception', 'warning', 'unconfirmed'])
def test_stop_reports_provider_failure_without_skipping_later_services(tmp_path, outcome):
    env, checkout = stop_fixture(tmp_path)
    env['PROVIDER_OUTCOME'] = outcome
    result = stopped(env, checkout)
    assert result.returncode != 0
    assert 'shutdown incomplete' in result.stderr
    assert 'fixture' in result.stderr
    assert '--user stop ollama.service' in Path(env['FAKE_SYSTEMCTL_LOG']).read_text()
    assert len(Path(env['PROVIDER_LOG']).read_text().splitlines()) == 3


def test_missing_units_are_normal_and_dry_run_never_stops_or_imports_providers(tmp_path):
    env, checkout = stop_fixture(tmp_path)
    env['ALL_UNITS_MISSING'] = '1'
    result = stopped(env, checkout)
    assert result.returncode == 0, result.stderr
    assert 'Skipping custom-llama.service' in result.stdout
    assert ' stop ' not in Path(env['FAKE_SYSTEMCTL_LOG']).read_text()
    Path(env['PROVIDER_LOG']).unlink()
    env.pop('ALL_UNITS_MISSING')
    env['FAILING_STOP_UNIT'] = 'korina-voice-lab.service'
    env['PROVIDER_OUTCOME'] = 'exception'
    result = stopped(env, checkout, '--dry-run')
    assert result.returncode == 0, result.stderr
    assert not Path(env['PROVIDER_LOG']).exists()
    assert ' stop ' not in Path(env['FAKE_SYSTEMCTL_LOG']).read_text()


def test_start_and_stop_pass_the_original_relative_runtime_into_provider_processes(tmp_path):
    env = setup_runtime(tmp_path, provider='llama.cpp')
    checkout = copied_service_checkout(tmp_path)
    runtime = Path(env['KORINA_APP_DIR'])
    config = json.loads((runtime / 'config.json').read_text())
    config['voice'] = 'sentinel-voice'
    (runtime / 'config.json').write_text(json.dumps(config))
    alias = tmp_path / 'runtime alias'
    alias.symlink_to(runtime, target_is_directory=True)
    env.update(KORINA_APP_DIR=alias.name, PROVIDER_LOG=str(tmp_path / 'providers.jsonl'))
    result = subprocess.run(['bash', str(checkout / 'Korina/start.sh'), '--no-wait'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    result = stopped(env, checkout, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in Path(env['PROVIDER_LOG']).read_text().splitlines()]
    assert records[0] == {'action': 'activate', 'runtime': str(alias), 'voice': 'sentinel-voice'}
    assert all(record['runtime'] == str(alias) for record in records)
    assert not (checkout / alias.name).exists()
