from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_source_checkout_defaults_serve_tracked_assets(tmp_path):
    # A fresh interpreter avoids changing import-time constants for other tests.
    env = {key: value for key, value in os.environ.items() if not key.startswith('KORINA_')}
    env['HOME'] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, '-c', 'import json; from korina.util import paths as p; '
         'print(json.dumps([str(p.APP_DIR),str(p.INDEX_PATH),p.LMSTUDIO_MODEL,'
         'str(p.LMSTUDIO_HUB_ROOT),list(map(str,p.LOCAL_MODEL_ROOTS))]))'],
        cwd=REPO, env=env, capture_output=True, text=True, check=True,
    )
    app, index, model, hub, roots = json.loads(result.stdout)
    assert Path(app) == REPO / 'Korina'
    assert Path(index).is_file()
    assert model == ''
    assert hub == str(tmp_path / '.lmstudio/hub/models')
    assert roots == [str(tmp_path / 'models')]


def test_binary_override_and_path_discovery(monkeypatch, tmp_path):
    from korina.util.paths import _binary

    executable = tmp_path / 'llama-server'
    executable.write_text('#!/bin/sh\n')
    executable.chmod(0o755)
    monkeypatch.delenv('KORINA_TEST_BINARY', raising=False)
    monkeypatch.setenv('PATH', str(tmp_path))
    assert _binary('KORINA_TEST_BINARY', 'llama-server') == executable
    monkeypatch.setenv('KORINA_TEST_BINARY', '~/custom/llama-server')
    assert _binary('KORINA_TEST_BINARY', 'llama-server') == Path.home() / 'custom/llama-server'


def test_bundled_assets_keep_generated_state_outside_package(tmp_path, monkeypatch):
    import importlib.util
    import shutil

    package = tmp_path / 'site-packages/korina'
    module = package / 'util/paths.py'
    module.parent.mkdir(parents=True)
    shutil.copyfile(REPO / 'korina/util/paths.py', module)
    assets = package / '_assets'
    assets.mkdir()
    (assets / 'index.html').write_text('<!doctype html>')
    data_home = tmp_path / 'user-data'
    # Load just the isolated path module, leaving every globally imported
    # korina module untouched for the normal API fixtures.
    monkeypatch.delenv('KORINA_APP_DIR', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(data_home))
    spec = importlib.util.spec_from_file_location('wheel_paths', module)
    paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paths)
    assert paths.APP_DIR == assets
    assert paths.INDEX_PATH == assets / 'index.html'
    assert paths.CONFIG_PATH == data_home / 'korina/config.json'
    assert paths.ACK_DIR == data_home / 'korina/Ack'
    assert paths.ACK_PHRASES_PATH == assets / 'Ack/ack_phrases.json'
