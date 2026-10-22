"""Allowlisted frontend assets and disposable settings for native browser tests.

Enter this context before importing korina: its runtime paths are resolved at
import time. No saved checkout settings, generated audio, weights, or services
are copied into this tree.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

REPO = Path(__file__).resolve().parents[3]
SOURCE_ASSETS = REPO / 'Korina'


def frontend_assets(source: Path = SOURCE_ASSETS) -> list[Path]:
    return [Path('index.html'), Path('styles.css'), Path('Ack/ack_phrases.json'),
            Path('config/config.example.json'),
            *(path.relative_to(source) for path in sorted((source / 'js').glob('*.js')))]


@contextmanager
def browser_runtime(*, source: Path = SOURCE_ASSETS):
    if 'korina.util.paths' in sys.modules:
        raise RuntimeError('Create the browser runtime before importing korina')
    with tempfile.TemporaryDirectory(prefix='korina-browser-') as directory:
        root = Path(directory)
        app = root / 'app'
        for relative in frontend_assets(source):
            destination = app / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        models, hub = root / 'models', root / 'hub'
        for path in (models, hub, root / 'bin', root / 'units', root / 'proc'):
            path.mkdir()
        closed = 'http://127.0.0.1:9'
        (app / 'config.json').write_text(json.dumps({
            'ack_enabled': 'off', 'agent_enabled': 'off', 'stt_backend': 'whisper',
            'stt_device': 'cpu', 'stt_model': 'base.en',
            'llm_provider': 'openai-compatible', 'llm_base_url': closed + '/v1',
            'lm_model': '', 'llm_api_key_env': '',
            'stt_llm_provider': 'openai-compatible', 'stt_llm_base_url': closed + '/v1',
            'stt_llm_model': '', 'stt_llm_api_key_env': '',
            'agent_provider': 'openai-compatible', 'agent_base_url': closed + '/v1',
            'agent_model': '', 'agent_api_key': '',
            'tts_provider': 'kokoro', 'tts_base_url': closed, 'tts_health_url': '',
            'local_model_roots': [], 'local_model_roots_explicit': True,
        }, indent=2) + '\n')
        environment = {
            'KORINA_APP_DIR': str(app), 'KORINA_LOCAL_MODEL_ROOTS': str(models),
            'KORINA_LMSTUDIO_HUB_ROOT': str(hub),
            'KORINA_LLAMA_SERVER_BIN': str(root / 'bin/llama-server'),
            'KORINA_LMSTUDIO_BIN': str(root / 'bin/lm-studio'),
            'KORINA_LMS_CLI_BIN': str(root / 'bin/lms'),
            'KORINA_LLAMA_SERVER_USER_UNIT': str(root / 'units/browser-fixture-llama.service'),
            'KORINA_LLAMA_SERVER_MEDIA_PATH': '',
            'LMSTUDIO_URL': closed + '/v1/chat/completions', 'LMSTUDIO_MODEL': '',
            'KOKORO_URL': closed, 'WHISPER_MODEL_ID': 'base.en', 'WHISPER_DEVICE': 'cpu',
            'WHISPER_COMPUTE_TYPE': '', 'WHISPER_CPU_THREADS': '4', 'WHISPER_BEAM_SIZE': '1',
            'PARTIAL_MIN_SECONDS': '0.6', 'ACK_DEFAULT_VOICE': 'af_heart',
            'KORINA_MAX_AUDIO_SECONDS': '300',
            'PATH': str(root / 'bin'),
            'XDG_CACHE_HOME': str(root / 'cache'), 'HF_HOME': str(root / 'cache/huggingface'),
            'HF_HUB_CACHE': str(root / 'cache/huggingface/hub'), 'HF_HUB_OFFLINE': '1',
            'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_DATA_HOME': str(root / 'data'),
        }
        previous = {key: os.environ.get(key) for key in environment}
        os.environ.update(environment)
        try:
            # Provider teardown also discovers/signals PIDs without a command.
            # Keep that existing process helper inside this disposable tree.
            from korina.services import provider_processes
            previous_proc = provider_processes.PROC_ROOT
            provider_processes.PROC_ROOT = root / 'proc'
            try:
                yield app
            finally:
                provider_processes.PROC_ROOT = previous_proc
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
