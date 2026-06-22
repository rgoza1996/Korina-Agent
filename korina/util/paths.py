"""File path resolution and environment-default constants.

Single source of truth for APP_DIR, INDEX_PATH, CONFIG_PATH, KOKORO_URL,
WHISPER_*, LOCAL_MODEL_ROOTS, LLAMA_SERVER_*, and similar env defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

_KORINA_REPO_ROOT = Path(__file__).resolve().parent
_DEFAULT_APP_DIR = '/home/roggoz/Korina'
APP_DIR = Path(os.environ.get('KORINA_APP_DIR', _DEFAULT_APP_DIR if (Path(_DEFAULT_APP_DIR) / 'index.html').exists() else str(_KORINA_REPO_ROOT)))
INDEX_PATH = APP_DIR / 'index.html'
ACK_DIR = APP_DIR / 'Ack'
ACK_PHRASES_PATH = ACK_DIR / 'ack_phrases.json'
CONFIG_PATH = APP_DIR / 'config.json'
KOKORO_URL = os.environ.get('KOKORO_URL', 'http://127.0.0.1:8880')
ACK_DEFAULT_VOICE = os.environ.get('ACK_DEFAULT_VOICE', 'af_heart')
WHISPER_MODEL_ID = os.environ.get('WHISPER_MODEL_ID', 'turbo')
WHISPER_DEVICE = os.environ.get('WHISPER_DEVICE')
WHISPER_COMPUTE_TYPE = os.environ.get('WHISPER_COMPUTE_TYPE')
WHISPER_CPU_THREADS = int(os.environ.get('WHISPER_CPU_THREADS', '4'))
WHISPER_BEAM_SIZE = int(os.environ.get('WHISPER_BEAM_SIZE', '1'))
PARTIAL_MIN_SECONDS = float(os.environ.get('PARTIAL_MIN_SECONDS', '0.6'))
LMSTUDIO_URL = os.environ.get('LMSTUDIO_URL', 'http://127.0.0.1:8080/v1/chat/completions')
LMSTUDIO_MODEL = os.environ.get('LMSTUDIO_MODEL', '/home/roggoz/Disks/SN750/models/google/gemma-4-E2B-it-qat-q4_0-gguf/gemma-4-E2B_q4_0-it.gguf')
WHISPER_MODEL_CHOICES = ['tiny.en', 'base.en', 'small.en', 'turbo', 'distil-large-v3']
LOCAL_MODEL_ROOTS = [Path(p) for p in os.environ.get('KORINA_LOCAL_MODEL_ROOTS', '/home/roggoz/Disks/SN750/models').split(os.pathsep) if p.strip()]
LMSTUDIO_HUB_ROOT = Path(os.environ.get('KORINA_LMSTUDIO_HUB_ROOT', '/home/roggoz/.lmstudio/hub/models'))
LLAMA_SERVER_BIN = Path(os.environ.get('KORINA_LLAMA_SERVER_BIN', '/home/roggoz/Disks/SN750/llama.cpp/build/bin/llama-server'))
LMSTUDIO_BIN = Path(os.environ.get('KORINA_LMSTUDIO_BIN', '/opt/LM-Studio/lm-studio'))
LLAMA_SERVER_MEDIA_PATH = os.environ.get('KORINA_LLAMA_SERVER_MEDIA_PATH', '/home/roggoz/Disks/SN750')
LLAMA_SERVER_USER_UNIT = Path(os.environ.get('KORINA_LLAMA_SERVER_USER_UNIT', '/home/roggoz/.config/systemd/user/llama-server.service'))
