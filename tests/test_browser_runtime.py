"""The native browser server uses disposable state even with user overrides set."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from korina.asset_manifest import BUNDLED_ASSETS

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / 'tests/browser/fixtures'


def get(base, path):
    with urlopen(base + path, timeout=1) as response:
        return response.read()


@contextmanager
def server(tmp_path):
    sentinel = tmp_path / 'existing-runtime'
    (sentinel / 'Ack').mkdir(parents=True)
    original = b'{"voice":"sentinel-private-voice","agent_api_key":"sentinel-secret"}\n'
    (sentinel / 'config.json').write_bytes(original)
    (sentinel / 'Ack/generated.wav').write_bytes(b'private generated audio')
    caller_bin = tmp_path / 'caller-bin'
    caller_bin.mkdir()
    command_log = tmp_path / 'caller-command.log'
    for name in ('systemctl', 'ollama'):
        command = caller_bin / name
        command.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$BROWSER_COMMAND_LOG"\n')
        command.chmod(0o755)
    environment = {**os.environ, 'KORINA_APP_DIR': str(sentinel), 'TMPDIR': str(tmp_path),
                   'PATH': str(caller_bin) + os.pathsep + os.environ.get('PATH', ''),
                   'BROWSER_COMMAND_LOG': str(command_log),
                   'HF_HUB_CACHE': str(sentinel / 'private-hub'), 'HF_HUB_OFFLINE': '0'}
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    with (tmp_path / 'browser-server.log').open('w+') as log:
        process = subprocess.Popen([sys.executable, str(FIXTURES / 'browser_server.py'), '--port', str(port)],
                                   cwd=REPO, env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    if json.loads(get(base, '/api/health')).get('ok') is True:
                        break
                except (OSError, URLError):
                    time.sleep(.05)
            else:
                log.seek(0)
                raise AssertionError('Disposable browser server did not start: ' + log.read())
            runtime, = tmp_path.glob('korina-browser-*/app')
            assert not command_log.exists()
            yield base, runtime, sentinel, original
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            assert not command_log.exists(), 'The browser runtime executed a caller provider/service command'


def test_browser_server_serves_matching_assets_and_mutates_only_temporary_settings(tmp_path):
    with server(tmp_path) as (base, runtime, sentinel, original):
        source = REPO / 'Korina'
        assets = [Path(relative) for relative in BUNDLED_ASSETS]
        for asset in assets:
            assert (runtime / asset).read_bytes() == (source / asset).read_bytes(), str(asset)
            if str(asset) != 'config/config.example.json':
                url = '/' if str(asset) == 'index.html' else '/' + asset.as_posix()
                assert get(base, url) == (source / asset).read_bytes(), url
        config = json.loads(get(base, '/api/config'))
        assert config['ack_enabled'] == config['agent_enabled'] == 'off'
        assert config['llm_base_url'] == config['agent_base_url'] == 'http://127.0.0.1:9/v1'
        assert config['tts_base_url'] == 'http://127.0.0.1:9'
        assert config['lm_model'] == config['agent_model'] == config['agent_api_key'] == ''
        assert json.loads(get(base, '/api/models/roots'))['roots'] == []
        request = Request(base + '/api/config', data=b'{"voice":"af_bella"}', method='POST',
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=3) as response:
            assert response.status == 200
        assert json.loads((runtime / 'config.json').read_text())['voice'] == 'af_bella'
        assert (sentinel / 'config.json').read_bytes() == original
        assert (sentinel / 'Ack/generated.wav').read_bytes() == b'private generated audio'
        assert not (runtime / 'Ack/generated.wav').exists()
        assert not list((runtime.parent / 'proc').iterdir())
        activation = Request(base + '/api/llm/provider/activate', method='POST',
                             data=b'{"provider":"openai-compatible"}',
                             headers={'Content-Type': 'application/json'})
        with urlopen(activation, timeout=3) as response:
            result = json.load(response)
        assert result['ok'] is True
        assert result['activation']['started'] == result['activation']['stopped'] == []
    assert not runtime.parent.exists(), 'The fixture must remove its disposable tree on shutdown'
    assert (sentinel / 'config.json').read_bytes() == original


def test_runtime_allowlist_excludes_private_files_and_restores_environment(tmp_path):
    source = tmp_path / 'assets'
    for relative in BUNDLED_ASSETS:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{}' if target.suffix == '.json' else 'fixture')
    for private in ['config.json', '.env', 'start.sh', 'Ack/generated.wav']:
        (source / private).write_text('must not be copied')
    code = '''from pathlib import Path
import json, os, shutil, sys
from runtime import browser_runtime
sys.path.insert(0, str(Path.cwd().parents[2]))
from korina.asset_manifest import BUNDLED_ASSETS
from korina.services import provider_processes
before = dict(os.environ)
before_proc = provider_processes.PROC_ROOT
with browser_runtime(source=Path(os.environ['FIXTURE_ASSETS'])) as app:
    assert set(str(path.relative_to(app)) for path in app.rglob('*') if path.is_file()) == set(BUNDLED_ASSETS) | {'config.json'}
    config = json.loads((app / 'config.json').read_text())
    assert config['ack_enabled'] == config['agent_enabled'] == 'off'
    assert config['agent_api_key'] == ''
    assert not Path(os.environ['KORINA_LLAMA_SERVER_BIN']).exists()
    assert not Path(os.environ['KORINA_LMS_CLI_BIN']).exists()
    assert not list(Path(os.environ['KORINA_LOCAL_MODEL_ROOTS']).iterdir())
    assert os.environ['PATH'] == str(app.parent / 'bin')
    assert os.environ['HF_HUB_CACHE'] == str(app.parent / 'cache/huggingface/hub')
    assert os.environ['HF_HUB_OFFLINE'] == '1'
    assert provider_processes.PROC_ROOT == app.parent / 'proc'
    assert not list(provider_processes.PROC_ROOT.iterdir())
    assert shutil.which('systemctl') is None
    assert shutil.which('ollama') is None
assert not app.exists()
assert dict(os.environ) == before
assert provider_processes.PROC_ROOT == before_proc
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=FIXTURES, env={
        **os.environ, 'FIXTURE_ASSETS': str(source), 'KORINA_APP_DIR': 'sentinel-runtime',
        'HF_HUB_CACHE': 'sentinel-model-cache', 'HF_HUB_OFFLINE': '0',
    }, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_default_browser_server_fails_on_occupied_8765_without_reusing_it(tmp_path):
    listener = socket.socket()
    try:
        try:
            listener.bind(('127.0.0.1', 8765))
            listener.listen()
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
        result = subprocess.run([sys.executable, str(FIXTURES / 'browser_server.py')], cwd=REPO,
                                env={**os.environ, 'TMPDIR': str(tmp_path)},
                                capture_output=True, text=True, timeout=15)
        assert result.returncode != 0
        assert 'address already in use' in result.stderr.lower()
        assert not list(tmp_path.glob('korina-browser-*'))
    finally:
        listener.close()
