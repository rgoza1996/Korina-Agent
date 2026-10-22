"""Historical commands report retirement without contacting or changing a stack."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'tests' / 'regression_smoke.py'


@pytest.mark.parametrize('flags', [[], ['--no-chat', '--no-transcribe']])
def test_legacy_invocation_is_retired_without_runtime_io(tmp_path, flags):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    config = runtime / 'config.json'
    original = b'{"voice":"sentinel-voice","agent_enabled":"off"}\n'
    config.write_bytes(original)
    modified_at = config.stat().st_mtime_ns
    env = dict(os.environ, KORINA_APP_DIR=str(runtime))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        base = f'http://127.0.0.1:{listener.getsockname()[1]}'
        result = subprocess.run([sys.executable, '-S', str(SCRIPT), '--base', base, *flags],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5)
        listener.setblocking(False)
        with pytest.raises(BlockingIOError):
            listener.accept()
    assert result.returncode == 2
    assert 'is retired' in result.stderr
    assert f'python scripts/check_stack.py --base {base}' in result.stderr
    assert 'docs/hackathon.md' in result.stderr
    assert 'physical microphone' in result.stderr
    assert config.read_bytes() == original
    assert config.stat().st_mtime_ns == modified_at
    assert sorted(path.name for path in runtime.iterdir()) == ['config.json']


def test_retired_command_help_is_available_without_dependencies(tmp_path):
    result = subprocess.run([sys.executable, '-S', str(SCRIPT), '--help'], cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert 'Retired' in result.stdout
    assert 'scripts/check_stack.py' in result.stdout
