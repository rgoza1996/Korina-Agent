"""Config commits retain permissions and never publish unfinished contents."""
from __future__ import annotations

import errno
import json
import stat
from pathlib import Path

import pytest


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize('example', [True, False])
def test_auto_created_config_is_private_without_changing_seed_behavior(config_module, app_dir, example):
    source = app_dir / 'config/config.example.json'
    if not example:
        source.unlink()
    saved = config_module.load_config()
    assert mode(config_module.CONFIG_PATH) == 0o600
    assert saved['voice'] == 'af_heart'
    assert saved['llm_provider'] == ('openai-compatible' if example else config_module.DEFAULT_CONFIG['llm_provider'])
    assert json.loads(config_module.CONFIG_PATH.read_text()) == saved


@pytest.mark.parametrize('operation', ['settings', 'model_roots'])
@pytest.mark.parametrize('permissions', [0o600, 0o640])
def test_settings_and_model_root_saves_preserve_existing_permissions(
    config_module, tmp_path, operation, permissions,
):
    saved = config_module.load_config()
    path = config_module.CONFIG_PATH
    path.chmod(permissions)
    before = set(path.parent.iterdir())
    if operation == 'settings':
        config_module.save_config({**saved, 'voice': 'af_bella'})
        assert config_module.load_config()['voice'] == 'af_bella'
    else:
        root = tmp_path / 'models'
        root.mkdir()
        assert config_module.set_local_model_roots([str(root)]) == [str(root)]
        assert config_module.load_config()['local_model_roots'] == [str(root)]
    assert mode(path) == permissions
    assert set(path.parent.iterdir()) == before


def test_new_config_temporaries_are_private_unique_siblings_and_closed_before_commit(config_module, monkeypatch):
    saved = config_module.load_config()
    path = config_module.CONFIG_PATH
    opened = []
    original_temporary = config_module.tempfile.NamedTemporaryFile
    original_replace = Path.replace

    def temporary(*args, **kwargs):
        stream = original_temporary(*args, **kwargs)
        opened.append(stream)
        assert Path(stream.name).parent == path.parent
        assert mode(Path(stream.name)) & 0o077 == 0
        return stream

    def replace(source, target):
        assert opened[-1].closed
        assert json.loads(source.read_text())['voice'] in ('af_bella', 'af_sky')
        return original_replace(source, target)

    monkeypatch.setattr(config_module.tempfile, 'NamedTemporaryFile', temporary)
    monkeypatch.setattr(Path, 'replace', replace)
    config_module.save_config({**saved, 'voice': 'af_bella'})
    config_module.save_config({**saved, 'voice': 'af_sky'})
    assert len({stream.name for stream in opened}) == 2
    assert all(stream.closed and not Path(stream.name).exists() for stream in opened)


def test_temporary_name_does_not_grow_with_a_valid_long_config_basename(config_module, app_dir, monkeypatch):
    path = app_dir / ('c' * 245 + '.json')
    monkeypatch.setattr(config_module, 'CONFIG_PATH', path)
    config_module.save_config({'voice': 'af_bella'})
    assert json.loads(path.read_text())['voice'] == 'af_bella'
    assert mode(path) == 0o600


@pytest.mark.parametrize('phase', ['write', 'close', 'chmod', 'replace'])
def test_failed_commit_preserves_old_contents_mode_primary_error_and_cleans_temporary(
    config_module, monkeypatch, phase,
):
    saved = config_module.load_config()
    path = config_module.CONFIG_PATH
    path.chmod(0o600)
    before, entries = path.read_bytes(), set(path.parent.iterdir())
    failure = OSError(errno.EIO, f'fixture {phase} failure')
    original_temporary = config_module.tempfile.NamedTemporaryFile
    original_chmod, original_replace = Path.chmod, Path.replace
    streams = []

    class Temporary:
        def __init__(self, stream):
            self.stream, self.name = stream, stream.name
            self.closed_once = False

        def write(self, text):
            if phase == 'write':
                self.stream.write(text[:32])
                raise failure
            return self.stream.write(text)

        def close(self):
            self.stream.close()
            if phase == 'close' and not self.closed_once:
                self.closed_once = True
                raise failure

    def temporary(*args, **kwargs):
        stream = Temporary(original_temporary(*args, **kwargs))
        streams.append(stream)
        return stream

    def chmod(candidate, permissions, *args, **kwargs):
        if phase == 'chmod' and candidate == Path(streams[0].name):
            raise failure
        return original_chmod(candidate, permissions, *args, **kwargs)

    def replace(source, destination):
        if phase == 'replace' and source == Path(streams[0].name):
            raise failure
        return original_replace(source, destination)

    monkeypatch.setattr(config_module.tempfile, 'NamedTemporaryFile', temporary)
    monkeypatch.setattr(Path, 'chmod', chmod)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(OSError) as caught:
        config_module.save_config({**saved, 'voice': 'af_bella'})
    assert caught.value is failure
    assert path.read_bytes() == before
    assert mode(path) == 0o600
    assert set(path.parent.iterdir()) == entries
    assert streams[0].stream.closed and not Path(streams[0].name).exists()


def test_cleanup_errors_do_not_mask_the_original_write_failure(config_module, monkeypatch):
    saved = config_module.load_config()
    path = config_module.CONFIG_PATH
    before = path.read_bytes()
    primary = OSError(errno.ENOSPC, 'fixture primary write failure')
    secondary = OSError(errno.EACCES, 'fixture cleanup failure')
    original_temporary = config_module.tempfile.NamedTemporaryFile
    original_unlink = Path.unlink
    streams = []

    class Temporary:
        def __init__(self, stream):
            self.stream, self.name = stream, stream.name

        def write(self, text):
            self.stream.write(text[:32])
            raise primary

        def close(self):
            self.stream.close()
            raise secondary

    def temporary(*args, **kwargs):
        stream = Temporary(original_temporary(*args, **kwargs))
        streams.append(stream)
        return stream

    def unlink(candidate, *args, **kwargs):
        if streams and candidate == Path(streams[0].name):
            raise secondary
        return original_unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as failure_scope:
        failure_scope.setattr(config_module.tempfile, 'NamedTemporaryFile', temporary)
        failure_scope.setattr(Path, 'unlink', unlink)
        with pytest.raises(OSError) as caught:
            config_module.save_config({**saved, 'voice': 'af_bella'})
        assert caught.value is primary
        assert path.read_bytes() == before
        assert mode(path) == 0o600
        assert streams[0].stream.closed
        assert mode(Path(streams[0].name)) == 0o600
    # The fixture deliberately denied removal; clean its owned private file.
    Path(streams[0].name).unlink()


def test_config_symlink_is_replaced_without_modifying_its_target(config_module):
    saved = config_module.load_config()
    path = config_module.CONFIG_PATH
    target = path.with_name('managed-config.json')
    path.rename(target)
    target.chmod(0o640)
    before = target.read_bytes()
    path.symlink_to(target.name)
    config_module.save_config({**saved, 'voice': 'af_bella'})
    assert not path.is_symlink()
    assert json.loads(path.read_text())['voice'] == 'af_bella'
    assert mode(path) == 0o640
    assert target.read_bytes() == before and mode(target) == 0o640
