"""Real disposable unit files survive controlled write/publish failures."""
from __future__ import annotations

import io
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from korina.services import provider_manager as manager


@pytest.fixture
def unit_runtime(monkeypatch, tmp_path):
    model = tmp_path / 'Model.GGUF'
    model.write_bytes(b'fixture')
    binary = tmp_path / 'llama-server'
    binary.write_bytes(b'fixture')
    target = tmp_path / 'units' / 'demo.service'
    target.parent.mkdir()
    target.write_bytes(b'original complete unit\n')
    target.chmod(0o640)
    monkeypatch.setattr(manager, 'LLAMA_SERVER_BIN', binary)
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', target)
    commands = []

    def forbidden(*args, **kwargs):
        commands.append(args)
        pytest.fail('a failed unit write must not run service commands')

    monkeypatch.setattr(manager, '_systemctl', forbidden)
    return SimpleNamespace(model=model, binary=binary, target=target, commands=commands)


def fail_file_operation(monkeypatch, phase, failure):
    real_open = io.open
    streams = []

    class Writer:
        def __init__(self, stream):
            self.stream = stream
            streams.append(stream)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, value):
            if phase == 'write':
                self.stream.write(value[:13])
                self.stream.flush()
                raise failure
            return self.stream.write(value)

        def flush(self):
            self.stream.flush()
            if phase == 'flush':
                raise failure

        def close(self):
            self.stream.close()
            if phase == 'close':
                raise failure

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def opened(file, mode='r', *args, **kwargs):
        stream = real_open(file, mode, *args, **kwargs)
        return Writer(stream) if 'w' in mode else stream

    monkeypatch.setattr(io, 'open', opened)
    return streams


@pytest.mark.parametrize('symlink', [False, True])
@pytest.mark.parametrize('phase', ['write', 'flush', 'close', 'replace'])
def test_failed_unit_publish_preserves_existing_bytes_mode_and_symlink(
    unit_runtime, monkeypatch, tmp_path, phase, symlink,
):
    runtime = unit_runtime
    link = tmp_path / 'linked.service'
    if symlink:
        link.symlink_to(runtime.target.relative_to(tmp_path))
        monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', link)
    failure = OSError('controlled ' + phase + ' failure')
    streams = fail_file_operation(monkeypatch, phase, failure)
    if phase == 'replace':
        def reject(*args):
            raise failure
        monkeypatch.setattr(Path, 'replace', reject)
    before = runtime.target.read_bytes()
    with pytest.raises(OSError) as caught:
        manager.start_llama_server(str(runtime.model), {'llm_reasoning': 'off'})
    assert caught.value is failure
    assert runtime.target.read_bytes() == before
    assert stat.S_IMODE(runtime.target.stat().st_mode) == 0o640
    assert list(runtime.target.parent.iterdir()) == [runtime.target]
    assert all(stream.closed for stream in streams)
    if symlink:
        assert link.is_symlink()
        assert link.readlink() == runtime.target.relative_to(tmp_path)
    assert runtime.commands == []


@pytest.mark.parametrize('symlink', [False, True])
def test_successful_unit_publish_preserves_mode_link_and_rendered_arguments(unit_runtime, monkeypatch, tmp_path, symlink):
    runtime = unit_runtime
    link = tmp_path / 'linked.service'
    if symlink:
        link.symlink_to(runtime.target.relative_to(tmp_path))
        monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', link)
    manager.write_llama_server_unit(str(runtime.model), {'llm_reasoning': 'off'})
    content = runtime.target.read_text()
    assert f'ExecStart={runtime.binary} -m {runtime.model}' in content
    assert '--reasoning off' in content
    assert stat.S_IMODE(runtime.target.stat().st_mode) == 0o640
    assert list(runtime.target.parent.iterdir()) == [runtime.target]
    if symlink:
        assert link.is_symlink()
        assert link.readlink() == runtime.target.relative_to(tmp_path)


def test_cleanup_failure_does_not_mask_primary_write_error(unit_runtime, monkeypatch):
    runtime = unit_runtime
    failure = OSError('primary partial-write error')
    fail_file_operation(monkeypatch, 'write', failure)
    original_unlink = Path.unlink

    def reject_temporary(path, *args, **kwargs):
        if path.parent == runtime.target.parent and path != runtime.target:
            raise PermissionError('controlled cleanup failure')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', reject_temporary)
    try:
        with pytest.raises(OSError) as caught:
            manager.write_llama_server_unit(str(runtime.model), {'llm_reasoning': 'off'})
        assert caught.value is failure
        assert runtime.target.read_bytes() == b'original complete unit\n'
    finally:
        for path in runtime.target.parent.iterdir():
            if path != runtime.target:
                original_unlink(path)


def test_failed_new_unit_write_does_not_publish_a_partial_destination(unit_runtime, monkeypatch):
    runtime = unit_runtime
    runtime.target.unlink()
    failure = OSError('controlled partial write')
    fail_file_operation(monkeypatch, 'write', failure)
    with pytest.raises(OSError) as caught:
        manager.start_llama_server(str(runtime.model), {'llm_reasoning': 'off'})
    assert caught.value is failure
    assert not runtime.target.exists()
    assert list(runtime.target.parent.iterdir()) == []
    assert runtime.commands == []


def test_dangling_unit_link_does_not_create_a_missing_target_parent(unit_runtime, monkeypatch, tmp_path):
    runtime = unit_runtime
    destination = tmp_path / 'missing' / 'target.service'
    link = tmp_path / 'linked.service'
    link.symlink_to(destination.relative_to(tmp_path))
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', link)
    with pytest.raises(FileNotFoundError):
        manager.start_llama_server(str(runtime.model), {'llm_reasoning': 'off'})
    assert link.is_symlink()
    assert link.readlink() == destination.relative_to(tmp_path)
    assert not destination.parent.exists()
    assert runtime.commands == []


def test_valid_long_unit_basename_does_not_overflow_temporary_filename(unit_runtime, monkeypatch):
    runtime = unit_runtime
    target = runtime.target.with_name('u' * 230 + '.service')
    monkeypatch.setattr(manager, 'LLAMA_SERVER_USER_UNIT', target)
    manager.write_llama_server_unit(str(runtime.model), {'llm_reasoning': 'off'})
    assert f'ExecStart={runtime.binary} -m {runtime.model}' in target.read_text()
    assert set(target.parent.iterdir()) == {runtime.target, target}
    assert runtime.commands == []


def test_new_unit_uses_caller_umask_without_a_production_umask_change(unit_runtime):
    runtime = unit_runtime
    runtime.target.unlink()
    script = '''
import json, stat, sys
from pathlib import Path
from korina.services import provider_manager as manager
manager.LLAMA_SERVER_BIN = Path(sys.argv[1])
manager.LLAMA_SERVER_USER_UNIT = Path(sys.argv[2])
manager.write_llama_server_unit(sys.argv[3], {'llm_reasoning': 'off'})
print(json.dumps({'mode': stat.S_IMODE(manager.LLAMA_SERVER_USER_UNIT.stat().st_mode)}))
'''
    completed = subprocess.run(
        [sys.executable, '-c', script, str(runtime.binary), str(runtime.target), str(runtime.model)],
        check=True, capture_output=True, text=True, umask=0o027,
    )
    assert json.loads(completed.stdout) == {'mode': 0o640}
    assert list(runtime.target.parent.iterdir()) == [runtime.target]
