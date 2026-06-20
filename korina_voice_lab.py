from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_DIR = Path('/home/roggoz/Korina')
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
LMSTUDIO_URL = os.environ.get('LMSTUDIO_URL', 'http://127.0.0.1:1234/v1/chat/completions')
LMSTUDIO_MODEL = os.environ.get('LMSTUDIO_MODEL', 'qwen3.5-2b-uncensored-hauhaucs-aggressive')
WHISPER_MODEL_CHOICES = ['tiny.en', 'base.en', 'small.en', 'turbo', 'distil-large-v3']

app = FastAPI(title='Korina Voice Lab: Kokoro + Whisper Turbo + LM Studio')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
ACK_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/Ack', StaticFiles(directory=str(ACK_DIR)), name='ack')

_asr_models: dict[str, object] = {}
_asr_loaded_at_by_device: dict[str, float] = {}
_asr_lock = threading.Lock()
_asr_infer_lock = threading.Lock()
_asr_device: Optional[str] = None
_asr_compute_type: Optional[str] = None



DEFAULT_CONFIG = {
    'voice': ACK_DEFAULT_VOICE,
    'speed': 1.0,
    'mode': 'sse',
    'ack_enabled': 'on',
    'stt_backend': 'whisper',
    'stt_device': 'cpu',
    'stt_model': 'base.en',
    'lm_model': LMSTUDIO_MODEL,
    'tts_device': 'cpu',
    'tts_provider': 'kokoro',
    'tts_port': 8880,
    'tts_base_url': '',
    'tts_model': 'kokoro',
    'llm_provider': 'openai-compatible',
    'llm_base_url': 'http://127.0.0.1:1234/v1',
    'llm_api_key_env': '',
    'stt_cloud_provider': '',
    'stt_cloud_base_url': '',
    'stt_cloud_model': '',
    'stt_api_key_env': '',
    'agent_enabled': 'on',
    'agent_provider': 'openai-compatible',
    'agent_base_url': 'http://127.0.0.1:1234/v1',
    'agent_api_key': '',
    'agent_model': '',
    'agent_max_turns': 16,
    'agent_max_tokens': 512,
    'agent_yolo_mode': 'off',
    'agent_project_trust': 'ask',
    'agent_steering_mode': 'one-at-a-time',
    'agent_follow_up_mode': 'one-at-a-time',
    'agent_thinking_level': 'low',
    'agent_auto_compact': 'on',
    'agent_compaction_reserve_tokens': 16384,
    'agent_compaction_keep_recent_tokens': 20000,
    'agent_hide_thinking': 'on',
    'agent_transport': 'auto',
    'agent_retry_enabled': 'on',
    'agent_max_retries': 3,
    'agent_retry_base_delay_ms': 2000,
    'agent_http_idle_timeout_ms': 0,
    'agent_enable_skill_commands': 'on',
    'agent_block_images': 'off',
    'agent_first_delivery_mode': 'first_turn_or_timer',
    'agent_first_delivery_seconds': 20,
    'agent_periodic_delivery_turns': 2,
    'agent_periodic_delivery_seconds': 45,
    'agent_busy_delivery_mode': 'steer',
    'agent_idle_delivery_mode': 'prompt',
    'agent_interrupts_enabled': 'on',
    'agent_interrupt_min_priority': 'important',
    'agent_hard_interrupt_min_priority': 'critical',
    'agent_permission_interrupts': 'on',
    'agent_report_injection_mode': 'next_reply',
    'endpoint_mode': 'reading',
    'silence_ms': 3200,
    'final_stt_mode': 'chunks',
    'idle_ack_initial_ms': 5000,
    'idle_ack_step_ms': 5000,
}

CONFIG_KEYS = set(DEFAULT_CONFIG.keys())


def load_config() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_PATH.read_text())
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    merged = dict(DEFAULT_CONFIG)
    for key, value in data.items():
        if key in CONFIG_KEYS:
            merged[key] = value
    return merged


def save_config(config: dict) -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONFIG)
    for key, value in (config or {}).items():
        if key in CONFIG_KEYS:
            merged[key] = value
    tmp = CONFIG_PATH.with_suffix('.tmp.json')
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + '\n')
    tmp.replace(CONFIG_PATH)
    return merged



def config_tts_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    explicit = str(config.get('tts_base_url') or '').strip().rstrip('/')
    if explicit:
        return explicit
    port = int(config.get('tts_port') or 8880)
    return f'http://127.0.0.1:{port}'


def config_llm_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('llm_base_url') or 'http://127.0.0.1:1234/v1').strip().rstrip('/')


def config_llm_chat_url(config: Optional[dict] = None) -> str:
    base = config_llm_base_url(config)
    return base if base.endswith('/chat/completions') else f'{base}/chat/completions'


def config_llm_models_url(config: Optional[dict] = None) -> str:
    base = config_llm_base_url(config)
    if base.endswith('/chat/completions'):
        base = base.rsplit('/chat/completions', 1)[0]
    return f'{base}/models'


def auth_headers_from_env(env_name: str) -> dict:
    env_name = (env_name or '').strip()
    if not env_name:
        return {}
    value = os.environ.get(env_name, '').strip()
    if not value:
        return {}
    return {'Authorization': f'Bearer {value}'}


def api_key_from_config(config: dict, direct_key: str = '', env_key_name: str = '') -> str:
    direct = str(direct_key or '').strip()
    if direct:
        return direct
    env_name = str(env_key_name or '').strip()
    return os.environ.get(env_name, '').strip() if env_name else ''


def agent_provider(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('agent_provider') or 'openai-compatible').strip().lower()


def agent_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('agent_base_url') or config.get('llm_base_url') or 'http://127.0.0.1:1234/v1').strip().rstrip('/')


def agent_models_url(config: Optional[dict] = None) -> str:
    base = agent_base_url(config)
    if base.endswith('/messages'):
        base = base.rsplit('/messages', 1)[0]
    if base.endswith('/chat/completions'):
        base = base.rsplit('/chat/completions', 1)[0]
    return f'{base}/models'


def agent_chat_url(config: Optional[dict] = None) -> str:
    base = agent_base_url(config)
    provider = agent_provider(config)
    if provider == 'anthropic':
        return base if base.endswith('/messages') else f'{base}/messages'
    return base if base.endswith('/chat/completions') else f'{base}/chat/completions'


def agent_auth_headers(config: dict) -> dict:
    key = api_key_from_config(config, config.get('agent_api_key') or '', config.get('llm_api_key_env') or '')
    provider = agent_provider(config)
    if not key:
        return {}
    if provider == 'anthropic':
        return {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    return {'Authorization': f'Bearer {key}'}


def parse_model_ids(body: dict) -> list[str]:
    models = []
    data = body.get('data', []) if isinstance(body, dict) else []
    if isinstance(data, list):
        for item in data:
            mid = item.get('id') if isinstance(item, dict) else item
            if isinstance(mid, str) and mid.strip():
                models.append(mid.strip())
    return models


def agent_model_choices() -> list[str]:
    config = load_config()
    req = urllib.request.Request(agent_models_url(config), headers=agent_auth_headers(config), method='GET')
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    return parse_model_ids(body)


_ack_queue: list[tuple[str, str, str]] = []
_ack_in_progress: set[tuple[str, str]] = set()
_ack_queue_lock = threading.Lock()
_ack_worker_running = False
_ack_last_error: Optional[str] = None
_ack_last_generated: Optional[str] = None
_ack_current_voice = ACK_DEFAULT_VOICE


_agent_lock = threading.Lock()
_agent_events: list[dict] = []
_agent_event_seq = 0
_agent_busy = False
_agent_status = 'idle'
_agent_last_report = ''
_agent_pending_steers: list[dict] = []
_agent_last_error: Optional[str] = None
_agent_last_emitted_report_hash = ''
_agent_last_emitted_report_at = 0.0


def safe_slug(value: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', (value or '').strip()).strip('_').lower()
    return slug or 'ack'


def load_ack_manifest() -> list[dict]:
    """Read ack_phrases.json. Supports old list[str] format and new tagged format."""
    if not ACK_PHRASES_PATH.exists():
        return []
    data = json.loads(ACK_PHRASES_PATH.read_text())
    if isinstance(data, list):
        phrases = data
        return [
            {'id': f'ack_{i:02d}', 'text': str(text), 'tags': ['global']}
            for i, text in enumerate(phrases, start=1)
            if str(text).strip()
        ]
    raw_phrases = data.get('phrases', []) if isinstance(data, dict) else []
    out = []
    for i, item in enumerate(raw_phrases, start=1):
        if isinstance(item, str):
            text = item.strip()
            tags = ['global']
            ack_id = f'ack_{i:02d}'
        elif isinstance(item, dict):
            text = str(item.get('text', '')).strip()
            tags = item.get('tags') or ['global']
            if isinstance(tags, str):
                tags = [tags]
            tags = [safe_slug(str(t)) for t in tags if str(t).strip()] or ['global']
            ack_id = safe_slug(str(item.get('id') or text or f'ack_{i:02d}'))
        else:
            continue
        if text:
            out.append({'id': ack_id, 'text': text, 'tags': tags})
    return out


def ack_filename(voice: str, phrase: dict) -> str:
    digest = hashlib.sha1(f"{phrase['id']}|{phrase['text']}".encode('utf-8')).hexdigest()[:8]
    return f"{safe_slug(voice)}__{safe_slug(phrase['id'])}__{digest}.wav"


def ack_path(voice: str, phrase: dict) -> Path:
    return ACK_DIR / ack_filename(voice, phrase)


def ack_files_for(voice: str, tag: Optional[str] = None) -> list[dict]:
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    requested_tag = safe_slug(tag or 'global')
    manifest = load_ack_manifest()
    matching = [p for p in manifest if requested_tag in p.get('tags', [])]
    if tag and not matching:
        matching = [p for p in manifest if 'global' in p.get('tags', [])]
    elif not tag:
        matching = [p for p in manifest if 'global' in p.get('tags', [])]
    files = []
    for phrase in matching:
        path = ack_path(voice, phrase)
        if path.exists() and path.stat().st_size > 44:
            files.append({
                'id': phrase['id'],
                'text': phrase['text'],
                'tags': phrase.get('tags', ['global']),
                'voice': voice,
                'name': path.name,
                'url': f'/Ack/{path.name}',
                'bytes': path.stat().st_size,
            })
    return files


def missing_ack_phrases(voice: str, tag: Optional[str] = None) -> list[dict]:
    requested_tag = safe_slug(tag) if tag else None
    manifest = load_ack_manifest()
    if requested_tag:
        manifest = [p for p in manifest if requested_tag in p.get('tags', [])]
    missing = []
    for phrase in manifest:
        path = ack_path(voice, phrase)
        if not path.exists() or path.stat().st_size <= 44:
            missing.append(phrase)
    return missing


def enqueue_missing_acks(voice: str, tag: Optional[str] = None) -> int:
    global _ack_worker_running
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    missing = missing_ack_phrases(voice, tag)
    with _ack_queue_lock:
        existing = {(v, pid) for v, pid, _ in _ack_queue} | set(_ack_in_progress)
        added = 0
        for phrase in missing:
            key = (voice, phrase['id'])
            if key not in existing:
                _ack_queue.append((voice, phrase['id'], phrase['text']))
                existing.add(key)
                added += 1
        if added and not _ack_worker_running:
            _ack_worker_running = True
            threading.Thread(target=ack_generation_worker, daemon=True).start()
    return len(missing)


def synthesize_ack_wav(voice: str, phrase_id: str, text: str) -> None:
    phrase = next((p for p in load_ack_manifest() if p['id'] == phrase_id), {'id': phrase_id, 'text': text, 'tags': ['global']})
    out = ack_path(voice, phrase)
    payload = json.dumps({'input': text, 'voice': voice, 'speed': 1.0, 'device': 'cpu'}).encode('utf-8')
    req = urllib.request.Request(
        f'{config_tts_base_url()}/v1/audio/speech',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    tmp = out.with_suffix('.tmp.wav')
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read()
    if len(data) <= 44:
        raise RuntimeError(f'generated ack for {phrase_id} was empty')
    tmp.write_bytes(data)
    tmp.replace(out)


def ack_generation_worker() -> None:
    global _ack_worker_running, _ack_last_error, _ack_last_generated
    try:
        while True:
            with _ack_queue_lock:
                if not _ack_queue:
                    _ack_worker_running = False
                    return
                voice, phrase_id, text = _ack_queue.pop(0)
                _ack_in_progress.add((voice, phrase_id))
            try:
                synthesize_ack_wav(voice, phrase_id, text)
                _ack_last_generated = f'{voice}:{phrase_id}'
                _ack_last_error = None
                print(f'[acks] generated {_ack_last_generated}', flush=True)
            except Exception as e:
                _ack_last_error = f'{voice}:{phrase_id}: {e}'
                print(f'[acks] generation failed: {_ack_last_error}', flush=True)
                time.sleep(2)
            finally:
                with _ack_queue_lock:
                    _ack_in_progress.discard((voice, phrase_id))
    finally:
        with _ack_queue_lock:
            if not _ack_queue:
                _ack_worker_running = False


def clear_ack_wavs() -> int:
    count = 0
    for f in ACK_DIR.glob('*.wav'):
        try:
            f.unlink()
            count += 1
        except FileNotFoundError:
            pass
    return count


def ack_status(voice: str) -> dict:
    manifest = load_ack_manifest()
    generated = [p for p in manifest if ack_path(voice, p).exists() and ack_path(voice, p).stat().st_size > 44]
    with _ack_queue_lock:
        queued = len(_ack_queue)
        worker_running = _ack_worker_running
    return {
        'voice': voice,
        'manifest_count': len(manifest),
        'generated_count': len(generated),
        'missing_count': len(missing_ack_phrases(voice)),
        'queue_depth': queued,
        'in_progress': [f'{v}:{pid}' for v, pid in sorted(_ack_in_progress)],
        'generating': worker_running,
        'last_generated': _ack_last_generated,
        'last_error': _ack_last_error,
    }


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int = 180
    model: str | None = None


class AgentStateRequest(BaseModel):
    transcript: list[dict] = []
    previous_report: str = ''
    model: str | None = None
    max_tokens: int = 512


class AgentTranscriptRequest(BaseModel):
    transcript: list[dict] = []
    delivery_mode: str = 'prompt'
    reason: str = ''
    turn_count: int = 0


class AgentPermissionAnswer(BaseModel):
    request_id: str = ''
    answer: str = ''
    transcript: list[dict] = []


def normalize_device(requested: Optional[str], *, default_env: Optional[str] = None) -> str:
    value = (requested or default_env or '').strip().lower()
    if value in ('gpu', 'cuda'):
        return 'cuda'
    if value == 'cpu':
        return 'cpu'
    return 'cuda' if torch.cuda.is_available() else 'cpu'


def compute_type_for(device: str) -> str:
    if WHISPER_COMPUTE_TYPE:
        return WHISPER_COMPUTE_TYPE
    return 'float16' if device == 'cuda' else 'int8'


def get_asr(device: Optional[str] = None, model_id: Optional[str] = None):
    global _asr_device, _asr_compute_type
    resolved = normalize_device(device, default_env=WHISPER_DEVICE)
    selected_model = (model_id or WHISPER_MODEL_ID).strip() or WHISPER_MODEL_ID
    compute_type = compute_type_for(resolved)
    key = f'{selected_model}:{resolved}:{compute_type}'
    if key in _asr_models:
        _asr_device = resolved
        _asr_compute_type = compute_type
        return _asr_models[key]
    with _asr_lock:
        if key in _asr_models:
            _asr_device = resolved
            _asr_compute_type = compute_type
            return _asr_models[key]
        from faster_whisper import WhisperModel

        print(
            f'[faster-whisper] loading {selected_model} '
            f'device={resolved} compute_type={compute_type} cpu_threads={WHISPER_CPU_THREADS}',
            flush=True,
        )
        model = WhisperModel(
            selected_model,
            device=resolved,
            compute_type=compute_type,
            cpu_threads=WHISPER_CPU_THREADS,
        )
        _asr_models[key] = model
        _asr_loaded_at_by_device[key] = time.time()
        _asr_device = resolved
        _asr_compute_type = compute_type
        print(f'[faster-whisper] loaded {key}', flush=True)
        return model


def transcribe_wav_segments(wav: Path, *, vad_filter: bool = True, device: Optional[str] = None, model_id: Optional[str] = None):
    """Yield faster-whisper segments.

    faster-whisper is lazy, so callers must consume the returned generator.
    Serializing inference avoids overlapping partial/final requests thrashing CPU/GPU.
    """
    asr = get_asr(device, model_id)
    with _asr_infer_lock:
        segments, info = asr.transcribe(
            str(wav),
            beam_size=WHISPER_BEAM_SIZE,
            vad_filter=vad_filter,
            language='en',
            condition_on_previous_text=False,
        )
        parts = list(segments)
    return parts, info


def transcribe_upload_file(audio_file, suffix: str, *, vad_filter: bool = True, device: Optional[str] = None, model_id: Optional[str] = None, backend: str = 'whisper', llm_model: Optional[str] = None) -> dict:
    with tempfile.TemporaryDirectory(prefix='korina-stt-') as td:
        td_path = Path(td)
        src = td_path / f'input{suffix}'
        wav = td_path / 'input-16k.wav'
        with src.open('wb') as f:
            shutil.copyfileobj(audio_file, f)
        convert_to_16k_wav(src, wav)
        data, sr = sf.read(wav, dtype='float32')
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        duration = float(len(data)) / float(sr or 16000)
        if backend == 'llm':
            result = lmstudio_transcribe_wav(wav, model=llm_model)
            result.update({
                'samples': int(len(data)),
                'sample_rate': int(sr),
                'duration': duration,
                'partial_capable': True,
            })
            return result

        started = time.time()
        parts, info = transcribe_wav_segments(wav, vad_filter=vad_filter, device=device, model_id=model_id)
        elapsed = time.time() - started
        text = ''.join(seg.text for seg in parts).strip()
        return {
            'text': text,
            'seconds': elapsed,
            'samples': int(len(data)),
            'sample_rate': int(sr),
            'duration': duration,
            'model': model_id or WHISPER_MODEL_ID,
            'backend': 'faster-whisper',
            'device': _asr_device,
            'requested_device': normalize_device(device, default_env=WHISPER_DEVICE),
            'compute_type': _asr_compute_type,
            'language': info.language,
            'language_probability': info.language_probability,
            'segments': [
                {'start': seg.start, 'end': seg.end, 'text': seg.text.strip()}
                for seg in parts
            ],
        }


def sse_event(event: str, payload: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'


def convert_to_16k_wav(src: Path, dst: Path) -> None:
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(src),
        '-ac', '1', '-ar', '16000', '-f', 'wav', str(dst),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or 'ffmpeg conversion failed')


def lmstudio_models() -> list[str]:
    config = load_config()
    url = config_llm_models_url(config)
    headers = auth_headers_from_env(str(config.get('llm_api_key_env') or ''))
    req = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    models = []
    for item in body.get('data', []):
        mid = item.get('id')
        if isinstance(mid, str) and mid.strip():
            models.append(mid.strip())
    return models


def lmstudio_transcribe_wav(wav: Path, *, model: Optional[str] = None) -> dict:
    """Attempt audio transcription through an LM Studio multimodal/audio model.

    LM Studio's OpenAI-compatible API definitely supports text/images. Audio input
    depends on the loaded model + server support, so this endpoint returns a clear
    error if the selected model rejects input_audio.
    """
    config = load_config()
    selected_model = (model or str(config.get('lm_model') or LMSTUDIO_MODEL)).strip()
    data = wav.read_bytes()
    b64 = base64.b64encode(data).decode('ascii')
    started = time.time()
    payload = {
        'model': selected_model,
        'messages': [
            {
                'role': 'system',
                'content': 'You are a strict speech-to-text engine. Return only the exact transcript. No commentary.',
            },
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Transcribe this audio exactly. Return only the spoken words.'},
                    {'type': 'input_audio', 'input_audio': {'data': b64, 'format': 'wav'}},
                ],
            },
        ],
        'temperature': 0,
        'max_tokens': 512,
        'stream': False,
    }
    headers = {'Content-Type': 'application/json'} | auth_headers_from_env(str(config.get('llm_api_key_env') or ''))
    http_req = urllib.request.Request(
        config_llm_chat_url(config),
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(http_req, timeout=120) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(
            f'LM Studio model {selected_model!r} rejected audio transcription request: HTTP {e.code}: {detail}'
        )
    text = (body.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
    return {
        'text': text,
        'seconds': time.time() - started,
        'model': selected_model,
        'backend': 'lmstudio-audio',
        'segments': [{'start': None, 'end': None, 'text': text}] if text else [],
    }


def format_voice_reply(text: str) -> str:
    """Make replies more TTS/chunk-friendly: one short sentence per line."""
    text = (text or '').strip()
    if not text:
        return text
    # Preserve deliberate newlines, but split any multi-sentence line.
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = re.split(r'(?<=[.!?…])\s+', raw)
        lines.extend(p.strip() for p in parts if p.strip())
    return '\n'.join(lines)


def lmstudio_chat(req: ChatRequest) -> str:
    system = req.system or (
        'You are Korina, a concise real-time voice conversation assistant.\n'
        'Speak in short, natural sentences that are suitable for being heard aloud.\n'
        'Put each sentence on its own new line. This is important because the TTS chunker uses new lines as clean break points.\n'
        'Prefer 1 to 3 short sentences unless the user explicitly asks for detail.\n'
        'Avoid markdown tables, bullets, long paragraphs, emojis, emoticons, kaomoji, symbols used as decoration, and stage directions.\n'
        'Never output emoji characters. Use plain words only, because replies are spoken aloud by TTS.\n'
        'If the user interrupts you mid-speech, treat that interruption as intentional.\n'
        'Use the provided interruption context to know roughly what you had already said and where the user cut in.\n'
        'After an interruption, respond to the latest user message rather than continuing your previous answer.'
    )
    messages = [{'role': 'system', 'content': system}]
    for m in req.history[-12:]:
        role = m.get('role')
        content = m.get('content')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            messages.append({'role': role, 'content': content.strip()})
    messages.append({'role': 'user', 'content': req.message.strip()})

    payload = {
        'model': (req.model or str(load_config().get('lm_model') or LMSTUDIO_MODEL)),
        'messages': messages,
        'temperature': req.temperature,
        'max_tokens': req.max_tokens,
        'stream': False,
    }
    data = json.dumps(payload).encode('utf-8')
    config = load_config()
    headers = {'Content-Type': 'application/json'} | auth_headers_from_env(str(config.get('llm_api_key_env') or ''))
    http_req = urllib.request.Request(
        config_llm_chat_url(config),
        data=data,
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(http_req, timeout=90) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'LM Studio HTTP {e.code}: {detail}')
    except Exception as e:
        raise RuntimeError(f'LM Studio request failed: {e}')

    try:
        return format_voice_reply(body['choices'][0]['message']['content'] or '')
    except Exception:
        raise RuntimeError(f'Unexpected LM Studio response: {body!r}')


def generate_agent_state_report(req: AgentStateRequest) -> str:
    config = load_config()
    selected_model = (req.model or str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL)).strip()
    turns = []
    for m in req.transcript[-int(config.get('agent_max_turns') or 16):]:
        role = m.get('role')
        content = m.get('content')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            turns.append({'role': role, 'content': content.strip()[:2000]})
    autonomy_note = (
        f"Pi-style settings: yolo_mode={config.get('agent_yolo_mode')}, project_trust={config.get('agent_project_trust')}, "
        f"steering_mode={config.get('agent_steering_mode')}, follow_up_mode={config.get('agent_follow_up_mode')}, "
        f"thinking_level={config.get('agent_thinking_level')}, auto_compact={config.get('agent_auto_compact')}."
    )
    system = (
        'You are Korina Agent, an agentic state tracker inspired by Pi Agent Harness concepts: maintain compact state, infer next useful steering, and do not chat with the user.\n'
        'Create a concise state report for Korina Converse to inject into its next spoken reply. Do not write the spoken reply. Do not use emojis.\n'
        'Start with exactly one line: Priority: low|normal|important|critical.\n'
        'Priority rules: low = bookkeeping/debug/no user-facing update. normal = useful state for the next reply only. important = user should hear this soon, but it can wait for a sentence boundary. critical = immediate safety/security/data-loss risk, time-sensitive blocking result, or explicit permission required before a tool call.\n'
        'Do NOT mark garbled STT/Whisper output, uncertain transcript text, routine model errors, repeated observations, or general warnings as critical. Treat transcript uncertainty as low or normal unless it creates an immediate unsafe action.\n'
        'If permission is required, include the exact phrase Permission request: followed by the requested action, risk, and yes/no question.\n'
        'Include: current user intent, relevant facts, unresolved tasks/questions, emotional/interaction notes, and suggested next-response steering.\n'
        'Korina Agent and Korina Converse exchange hidden state. Never speak directly to the user except by emitting important/critical reports for Converse to relay.\n'
        'If yolo_mode is on, be more decisive in suggested steering, but still never perform external side effects from this state-report endpoint.\n'
        f'{autonomy_note}\n'
        'Use compact plain text with short headings.'
    )
    user_payload = {
        'previous_report': req.previous_report[-4000:],
        'recent_transcript': turns,
    }
    provider = agent_provider(config)
    headers = {'Content-Type': 'application/json'} | agent_auth_headers(config)
    if provider == 'anthropic':
        payload = {
            'model': selected_model,
            'system': system,
            'messages': [{'role': 'user', 'content': json.dumps(user_payload, ensure_ascii=False)}],
            'temperature': 0.2,
            'max_tokens': int(req.max_tokens or config.get('agent_max_tokens') or 512),
        }
    else:
        payload = {
            'model': selected_model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(user_payload, ensure_ascii=False)},
            ],
            'temperature': 0.2,
            'max_tokens': int(req.max_tokens or config.get('agent_max_tokens') or 512),
            'stream': False,
        }
    http_req = urllib.request.Request(
        agent_chat_url(config),
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(http_req, timeout=90) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Korina Agent {provider} HTTP {e.code}: {detail}')
    except Exception as e:
        raise RuntimeError(f'Korina Agent request failed: {e}')
    try:
        if provider == 'anthropic':
            parts = body.get('content') or []
            return ''.join(part.get('text', '') for part in parts if isinstance(part, dict)).strip()
        return (body['choices'][0]['message']['content'] or '').strip()
    except Exception:
        raise RuntimeError(f'Unexpected Korina Agent response: {body!r}')


def sanitize_agent_report(report: str) -> str:
    text = report or ''
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.I)
    text = re.sub(r'<think>[\s\S]*', '', text, flags=re.I)
    text = re.sub(r'\[\s*Ack Phrase\s*\]\s*[^.?!]*(?:[.?!]\s*)?', '', text, flags=re.I)
    text = re.sub(r'\bAck Phrase\b\s*', '', text, flags=re.I)
    text = re.sub(r'\[\s*Korina Agent Interrupt\s*\]\s*[^\n]*', '', text, flags=re.I)
    text = re.sub(r'\bKorina Agent Interrupt\s*:\s*[^\n]*', '', text, flags=re.I)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


PRIORITY_ORDER = {'low': 0, 'normal': 1, 'important': 2, 'critical': 3}


def classify_agent_priority(report: str) -> str:
    text = (report or '').lower()
    m = re.search(r'^\s*priority\s*:\s*(low|normal|important|critical)\b', text, re.I | re.M)
    explicit = m.group(1).lower() if m else None
    critical_cues = ['permission request:', 'explicit permission', 'approve this command', 'authorization required', 'destructive command', 'delete data', 'data loss', 'secret leaked', 'credential exposed']
    garbled_cues = ['garbled', 'whisper', 'transcription uncertainty', 'stt', 'unclear transcript']
    self_loop_cues = ['<think>', 'state report', 'korina agent interrupt', 'acknowledgment loop', 'ack phrase']
    # Guardrail: even if the model says critical/important, garbled STT or self-referential reporting alone is not interrupt-worthy.
    if explicit in ('critical', 'important') and any(word in text for word in garbled_cues + self_loop_cues) and not any(word in text for word in critical_cues):
        return 'normal'
    if explicit:
        return explicit
    # Critical is intentionally narrow: immediate safety/security/data-loss/time-sensitive risk or explicit permission.
    if any(word in text for word in critical_cues):
        return 'critical'
    if any(word in text for word in ['critical:', 'urgent:', 'blocked:', 'security risk', 'cannot continue without', 'task is blocked']):
        return 'important'
    # Garbled STT, Whisper uncertainty, generic errors, and routine warnings should not interrupt by keyword accident.
    if any(word in text for word in ['garbled', 'whisper', 'transcription uncertainty', 'stt', 'unclear transcript']):
        return 'normal'
    return 'normal'


def push_agent_event(event: dict) -> dict:
    global _agent_event_seq
    with _agent_lock:
        _agent_event_seq += 1
        event = dict(event)
        event['id'] = _agent_event_seq
        event['created_at'] = time.time()
        _agent_events.append(event)
        del _agent_events[:-100]
        return event


def agent_snapshot() -> dict:
    with _agent_lock:
        return {
            'busy': _agent_busy,
            'status': _agent_status,
            'last_report': _agent_last_report,
            'pending_steers': len(_agent_pending_steers),
            'last_error': _agent_last_error,
            'last_event_id': _agent_event_seq,
        }


def run_agent_transcript_job(req: AgentTranscriptRequest) -> None:
    global _agent_busy, _agent_status, _agent_last_report, _agent_last_error, _agent_last_emitted_report_hash, _agent_last_emitted_report_at
    config = load_config()
    with _agent_lock:
        if _agent_busy:
            _agent_pending_steers.append({
                'transcript': req.transcript,
                'reason': req.reason,
                'turn_count': req.turn_count,
                'created_at': time.time(),
            })
            queued_busy = True
        else:
            queued_busy = False
        if not queued_busy:
            _agent_busy = True
            _agent_status = 'working'
    if queued_busy:
        push_agent_event({'type': 'agent_status', 'status': 'busy_queued_steer', 'priority': 'low', 'message': 'Korina Agent is busy; transcript delta queued as steering.'})
        return
    push_agent_event({'type': 'agent_status', 'status': 'working', 'priority': 'low', 'message': 'Korina Agent received transcript update.'})
    try:
        previous = _agent_last_report
        with _agent_lock:
            if _agent_pending_steers:
                steer_text = '\n\nQueued steering while busy:\n' + json.dumps(_agent_pending_steers[-5:], ensure_ascii=False)
                _agent_pending_steers.clear()
            else:
                steer_text = ''
        state_req = AgentStateRequest(
            transcript=req.transcript,
            previous_report=(previous + steer_text)[-6000:],
            model=str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
            max_tokens=int(config.get('agent_max_tokens') or 512),
        )
        agent_input = {
            'delivery_mode': req.delivery_mode,
            'reason': req.reason,
            'turn_count': req.turn_count,
            'previous_report': state_req.previous_report,
            'transcript': state_req.transcript,
            'model': state_req.model,
        }
        raw_report = generate_agent_state_report(state_req)
        report = sanitize_agent_report(raw_report)
        if not report:
            push_agent_event({'type': 'agent_status', 'status': 'empty_report_suppressed', 'priority': 'low', 'message': 'Empty/internal Korina Agent report suppressed.', 'agent_input': agent_input})
            return
        priority = classify_agent_priority(raw_report + '\n' + report)
        report_hash = hashlib.sha256(report.encode('utf-8')).hexdigest()
        now = time.time()
        duplicate_recent = report_hash == _agent_last_emitted_report_hash and (now - _agent_last_emitted_report_at) < 60 and priority != 'critical'
        _agent_last_emitted_report_hash = report_hash
        _agent_last_emitted_report_at = now
        with _agent_lock:
            _agent_last_report = report
            _agent_status = 'idle'
            _agent_last_error = None
        event_type = 'permission_request' if priority == 'critical' and 'permission request:' in report.lower() else 'state_report'
        if not duplicate_recent:
            push_agent_event({
                'type': event_type,
                'priority': priority,
                'report': report,
                'message': report,
                'delivery_mode': req.delivery_mode,
                'reason': req.reason,
                'turn_count': req.turn_count,
                'agent_input': agent_input,
                'duplicate_suppressed': False,
            })
        else:
            push_agent_event({'type': 'agent_status', 'status': 'duplicate_report_suppressed', 'priority': 'low', 'message': 'Duplicate Korina Agent report suppressed.', 'agent_input': agent_input})
    except Exception as e:
        with _agent_lock:
            _agent_status = 'idle'
            _agent_last_error = str(e)
        push_agent_event({'type': 'agent_error', 'priority': 'important', 'message': str(e)})
    finally:
        with _agent_lock:
            _agent_busy = False


def submit_agent_transcript(req: AgentTranscriptRequest) -> dict:
    config = load_config()
    if str(config.get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'accepted': False, 'disabled': True, 'status': agent_snapshot()}
    if req.delivery_mode == 'steer':
        with _agent_lock:
            _agent_pending_steers.append({'transcript': req.transcript, 'reason': req.reason, 'turn_count': req.turn_count, 'created_at': time.time()})
        push_agent_event({'type': 'agent_status', 'status': 'steer_received', 'priority': 'low', 'message': 'Transcript steering queued for Korina Agent.'})
        return {'ok': True, 'accepted': True, 'queued_as': 'steer', 'status': agent_snapshot()}
    threading.Thread(target=run_agent_transcript_job, args=(req,), daemon=True).start()
    return {'ok': True, 'accepted': True, 'queued_as': 'prompt', 'status': agent_snapshot()}


@app.on_event('startup')
def startup_generate_default_acks():
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    enqueue_missing_acks(str(load_config().get('voice') or ACK_DEFAULT_VOICE))


@app.get('/')
def index():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail='index.html missing')
    return FileResponse(INDEX_PATH)


@app.get('/api/health')
def health():
    return {
        'ok': True,
        'service': 'korina-voice-lab',
        'whisper_backend': 'faster-whisper',
        'whisper_model': WHISPER_MODEL_ID,
        'whisper_model_choices': WHISPER_MODEL_CHOICES,
        'whisper_loaded': bool(_asr_models),
        'whisper_loaded_devices': sorted(_asr_models.keys()),
        'whisper_loaded_at_by_device': _asr_loaded_at_by_device,
        'whisper_device': _asr_device or normalize_device(None, default_env=WHISPER_DEVICE),
        'whisper_compute_type': _asr_compute_type or compute_type_for(normalize_device(None, default_env=WHISPER_DEVICE)),
        'cuda_available': torch.cuda.is_available(),
        'whisper_beam_size': WHISPER_BEAM_SIZE,
        'whisper_cpu_threads': WHISPER_CPU_THREADS,
        'partial_min_seconds': PARTIAL_MIN_SECONDS,
        'cuda': torch.cuda.is_available(),
        'lmstudio_url': config_llm_chat_url(),
        'lmstudio_model': str(load_config().get('lm_model') or LMSTUDIO_MODEL),
        'tts_base_url': config_tts_base_url(),
        'ack_count': len(ack_files_for(_ack_current_voice)),
        'ack_status': ack_status(_ack_current_voice),
    }


@app.get('/api/config')
def get_config():
    return load_config()


@app.post('/api/config')
def update_config(payload: dict):
    current = load_config()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in CONFIG_KEYS:
                current[key] = value
    return save_config(current)


@app.get('/api/models')
def models():
    lm_error = None
    lm_models = []
    try:
        lm_models = lmstudio_models()
    except Exception as e:
        lm_error = str(e)
    return {
        'whisper_models': WHISPER_MODEL_CHOICES,
        'lmstudio_models': lm_models,
        'lmstudio_default': LMSTUDIO_MODEL,
        'lmstudio_error': lm_error,
    }


@app.get('/api/acks')
def acks(voice: str = Query(ACK_DEFAULT_VOICE), tag: Optional[str] = Query(None)):
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    enqueue_missing_acks(voice, tag)
    return {
        'acks': ack_files_for(voice, tag),
        'tag': tag or 'global',
        'status': ack_status(voice),
    }


@app.get('/api/acks/status')
def acks_status(voice: str = Query(ACK_DEFAULT_VOICE)):
    return ack_status((voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE)


@app.post('/api/acks/rebuild')
def acks_rebuild(payload: dict):
    global _ack_current_voice
    voice = (payload.get('voice') or ACK_DEFAULT_VOICE).strip()
    tag = payload.get('tag')
    clear = bool(payload.get('clear', False))
    if clear:
        removed = clear_ack_wavs()
    else:
        removed = 0
    _ack_current_voice = voice
    missing = enqueue_missing_acks(voice, tag)
    return {'ok': True, 'voice': voice, 'tag': tag, 'removed': removed, 'queued_or_missing': missing, 'status': ack_status(voice)}


@app.get('/api/agent/status')
def agent_status():
    return {'ok': True, **agent_snapshot()}


@app.get('/api/agent/events')
def agent_events(after: int = Query(0)):
    with _agent_lock:
        events = [e for e in _agent_events if int(e.get('id', 0)) > after]
        last_id = _agent_event_seq
    return {'ok': True, 'events': events, 'last_event_id': last_id, **agent_snapshot()}


@app.post('/api/agent/transcript')
def agent_transcript(req: AgentTranscriptRequest):
    return submit_agent_transcript(req)


@app.post('/api/agent/permission-answer')
def agent_permission_answer(req: AgentPermissionAnswer):
    push_agent_event({'type': 'permission_answer', 'priority': 'normal', 'request_id': req.request_id, 'answer': req.answer, 'transcript': req.transcript})
    return submit_agent_transcript(AgentTranscriptRequest(transcript=req.transcript, delivery_mode='prompt', reason=f'permission_answer:{req.answer}', turn_count=0))


@app.get('/api/agent/models')
def agent_models():
    try:
        models = agent_model_choices()
        return {'ok': True, 'models': models, 'provider': agent_provider(), 'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL), 'error': None}
    except Exception as e:
        return {'ok': False, 'models': [], 'provider': agent_provider(), 'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL), 'error': str(e)}


@app.post('/api/agent/state-report')
def agent_state_report(req: AgentStateRequest):
    if str(load_config().get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'state_report': '', 'disabled': True}
    try:
        report = generate_agent_state_report(req)
        return {'ok': True, 'state_report': report, 'model': req.model or str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/transcribe')
async def transcribe(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'
    try:
        return JSONResponse(transcribe_upload_file(audio.file, suffix, vad_filter=True, device=device, model_id=model, backend=backend, llm_model=llm_model))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/transcribe/partial')
async def transcribe_partial(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
    """Low-latency rolling partial transcript for live mode.

    Browser sends the growing current utterance every ~1.8s while the user is
    still speaking. faster-whisper itself is not a streaming decoder, so this
    endpoint transcribes snapshots of the in-progress utterance and returns the
    latest best partial.
    """
    suffix = Path(audio.filename or 'partial.webm').suffix or '.webm'
    try:
        result = transcribe_upload_file(audio.file, suffix, vad_filter=False, device=device, model_id=model, backend=backend, llm_model=llm_model)
        result['partial'] = True
        result['stable'] = False
        if result['duration'] < PARTIAL_MIN_SECONDS:
            result['text'] = ''
            result['segments'] = []
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post('/api/transcribe/stream')
async def transcribe_stream(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'

    def events():
        with tempfile.TemporaryDirectory(prefix='korina-stt-stream-') as td:
            td_path = Path(td)
            src = td_path / f'input{suffix}'
            wav = td_path / 'input-16k.wav'
            with src.open('wb') as f:
                shutil.copyfileobj(audio.file, f)
            started = time.time()
            try:
                yield sse_event('status', {'message': 'converting', 'backend': 'faster-whisper'})
                convert_to_16k_wav(src, wav)
                data, sr = sf.read(wav, dtype='float32')
                if data.ndim > 1:
                    data = np.mean(data, axis=1)
                yield sse_event('status', {'message': 'transcribing', 'samples': int(len(data)), 'sample_rate': int(sr), 'backend': backend})
                if backend == 'llm':
                    result = lmstudio_transcribe_wav(wav, model=llm_model)
                    text = result.get('text', '')
                    if text:
                        yield sse_event('segment', {'start': None, 'end': None, 'text': text, 'index': 1})
                    elapsed = time.time() - started
                    yield sse_event('done', {
                        'text': text,
                        'seconds': elapsed,
                        'samples': int(len(data)),
                        'sample_rate': int(sr),
                        'model': llm_model or LMSTUDIO_MODEL,
                        'backend': 'lmstudio-audio',
                        'segments': 1 if text else 0,
                    })
                    return
                parts, info = transcribe_wav_segments(wav, vad_filter=True, device=device, model_id=model)
                text_parts = []
                count = 0
                for seg in parts:
                    count += 1
                    part = seg.text.strip()
                    if part:
                        text_parts.append(seg.text)
                    yield sse_event('segment', {
                        'start': seg.start,
                        'end': seg.end,
                        'text': part,
                        'index': count,
                    })
                elapsed = time.time() - started
                yield sse_event('done', {
                    'text': ''.join(text_parts).strip(),
                    'seconds': elapsed,
                    'samples': int(len(data)),
                    'sample_rate': int(sr),
                    'model': model or WHISPER_MODEL_ID,
                    'backend': 'faster-whisper',
                    'device': _asr_device,
                    'compute_type': _asr_compute_type,
                    'language': info.language,
                    'language_probability': info.language_probability,
                    'segments': count,
                })
            except Exception as e:
                yield sse_event('error', {'detail': str(e)})

    return StreamingResponse(events(), media_type='text/event-stream')


@app.post('/api/chat')
def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail='No message provided')
    started = time.time()
    try:
        reply = lmstudio_chat(req)
        return JSONResponse({
            'reply': reply,
            'seconds': time.time() - started,
            'model': req.model or str(load_config().get('lm_model') or LMSTUDIO_MODEL),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8001)
