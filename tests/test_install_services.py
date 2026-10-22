from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_install_services_renders_checkout_and_interpreter(tmp_path):
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    systemctl = binary_dir / 'systemctl'
    systemctl.write_text('#!/bin/sh\nexit 0\n')
    systemctl.chmod(0o755)
    config_home = tmp_path / 'config home'
    app_dir = tmp_path / 'voice $runtime%'
    env = dict(os.environ, PATH=str(binary_dir) + os.pathsep + os.environ['PATH'],
               XDG_CONFIG_HOME=str(config_home), KORINA_PYTHON=sys.executable,
               KORINA_APP_DIR=str(app_dir))
    subprocess.run(['bash', str(REPO / 'Korina/install-services.sh')], env=env,
                   check=True, capture_output=True, text=True)
    unit_dir = config_home / 'systemd/user'
    app = (unit_dir / 'korina-voice-lab.service').read_text()
    tts = (unit_dir / 'kokoro-streaming-server.service').read_text()
    assert f'WorkingDirectory={REPO}' in app
    assert f'ExecStart="{sys.executable}" -m korina.app' in app
    assert f'Environment="KORINA_APP_DIR={str(app_dir).replace("%", "%%")}"' in app
    assert '@' not in app + tts
    assert f'"{REPO}/kokoro-streaming-server.py"' in tts
