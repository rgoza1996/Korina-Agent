"""Portable runtime defaults, with explicit environment overrides.

A source checkout serves its tracked ``Korina/`` assets. Deployments can point
``KORINA_APP_DIR`` at a separate runtime tree. Model weights and provider
binaries remain optional and are never assumed to exist on a particular host.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def _path(name: str, default: str | Path) -> Path:
    return Path(os.environ.get(name) or default).expanduser()


def _binary(name: str, command: str, *candidates: Path) -> Path:
    override = os.environ.get(name)
    if override:
        return Path(override).expanduser()
    discovered = shutil.which(command)
    if discovered:
        return Path(discovered)
    return next((p for p in candidates if p.is_file()), Path(command))


REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ASSETS = REPO_ROOT / 'Korina'
_BUNDLED_ASSETS = Path(__file__).resolve().parents[1] / '_assets'
_DEFAULT_ASSETS = _SOURCE_ASSETS if (_SOURCE_ASSETS / 'index.html').is_file() else _BUNDLED_ASSETS
APP_DIR = _path('KORINA_APP_DIR', _DEFAULT_ASSETS)
# A wheel's code/static assets may be read-only. Keep generated state in the
# user's data directory; explicit runtime trees and source installs keep their
# existing config.json/Ack layout.
_DATA_HOME = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
_RUNTIME_DIR = APP_DIR if os.environ.get('KORINA_APP_DIR') or APP_DIR == _SOURCE_ASSETS else _DATA_HOME / 'korina'
INDEX_PATH = APP_DIR / 'index.html'
ACK_DIR = _RUNTIME_DIR / 'Ack'
ACK_PHRASES_PATH = APP_DIR / 'Ack' / 'ack_phrases.json'
CONFIG_PATH = _RUNTIME_DIR / 'config.json'
KOKORO_URL = os.environ.get('KOKORO_URL', 'http://127.0.0.1:8880')
ACK_DEFAULT_VOICE = os.environ.get('ACK_DEFAULT_VOICE', 'af_heart')
WHISPER_MODEL_ID = os.environ.get('WHISPER_MODEL_ID', 'turbo')
WHISPER_DEVICE = os.environ.get('WHISPER_DEVICE')
WHISPER_COMPUTE_TYPE = os.environ.get('WHISPER_COMPUTE_TYPE')
WHISPER_CPU_THREADS = int(os.environ.get('WHISPER_CPU_THREADS', '4'))
WHISPER_BEAM_SIZE = int(os.environ.get('WHISPER_BEAM_SIZE', '1'))
PARTIAL_MIN_SECONDS = float(os.environ.get('PARTIAL_MIN_SECONDS', '0.6'))
LMSTUDIO_MODEL = os.environ.get('LMSTUDIO_MODEL', '')
WHISPER_MODEL_CHOICES = ['tiny.en', 'base.en', 'small.en', 'turbo', 'distil-large-v3']
LOCAL_MODEL_ROOTS = [
    Path(p.strip()).expanduser()
    for p in os.environ.get('KORINA_LOCAL_MODEL_ROOTS', str(Path.home() / 'models')).split(os.pathsep)
    if p.strip()
]
LMSTUDIO_HUB_ROOT = _path('KORINA_LMSTUDIO_HUB_ROOT', Path.home() / '.lmstudio/hub/models')
LLAMA_SERVER_BIN = _binary('KORINA_LLAMA_SERVER_BIN', 'llama-server')
LMSTUDIO_BIN = _binary('KORINA_LMSTUDIO_BIN', 'lm-studio', Path('/opt/LM-Studio/lm-studio'))
LMS_CLI_BIN = _binary(
    'KORINA_LMS_CLI_BIN', 'lms', Path.home() / '.lmstudio/bin/lms',
    Path('/opt/LM-Studio/resources/app/.webpack/lms'),
)
# Only enable llama.cpp media access when the operator chooses a directory.
LLAMA_SERVER_MEDIA_PATH = os.environ.get('KORINA_LLAMA_SERVER_MEDIA_PATH', '')
LLAMA_SERVER_USER_UNIT = _path(
    'KORINA_LLAMA_SERVER_USER_UNIT',
    Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'systemd/user/llama-server.service',
)
