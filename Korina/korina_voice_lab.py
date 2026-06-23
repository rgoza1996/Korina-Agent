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

# Paths and env defaults moved to korina/util/paths.py (Phase 1.2)
from korina.util.paths import (
    APP_DIR, INDEX_PATH, ACK_DIR, ACK_PHRASES_PATH, CONFIG_PATH,
    KOKORO_URL, ACK_DEFAULT_VOICE,
    WHISPER_MODEL_ID, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
    WHISPER_CPU_THREADS, WHISPER_BEAM_SIZE,
    PARTIAL_MIN_SECONDS, LMSTUDIO_URL, LMSTUDIO_MODEL,
    WHISPER_MODEL_CHOICES, LOCAL_MODEL_ROOTS, LMSTUDIO_HUB_ROOT,
    LLAMA_SERVER_BIN, LMSTUDIO_BIN, LLAMA_SERVER_MEDIA_PATH,
    LLAMA_SERVER_USER_UNIT,
)

app = FastAPI(title='Korina Voice Lab: Built-in Whisper, multimodal STT, llama.cpp, and Kokoro')
# Backwards-compat alias used by the in-progress EventBus state-report endpoints
korina_app = app
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
ACK_DIR.mkdir(parents=True, exist_ok=True)
app.mount('/Ack', StaticFiles(directory=str(ACK_DIR)), name='ack')




# Runtime state moved to korina/runtime/state.py (Phase 1.3)
from korina.runtime import state

# Config bootstrapping and helpers moved to korina/config.py (Phase 1.2)
from korina.config import (
    DEFAULT_CONFIG, CONFIG_KEYS,
    LEGACY_AGENT_MODE_KEY, LEGACY_DELIVERY_VALUE, LEGACY_INJECTION_MODE_VALUE,
    _normalize_config_value, _config_example_path,
    load_config, save_config,
    provider_preset_base_url, is_local_provider_base_url, display_model_label,
    synchronize_llm_dependents,
    config_tts_base_url, config_llm_base_url, config_llm_chat_url, config_llm_models_url,
    config_stt_llm_provider, config_stt_llm_base_url, config_stt_llm_chat_url, config_stt_llm_models_url,
    config_stt_llm_api_env, config_stt_llm_model,
    config_min_speech_ms, config_partial_window_ms,
    config_llm_reasoning, config_stt_llm_reasoning,
    auth_headers_from_env, api_key_from_config,
    agent_provider, agent_base_url, agent_models_url, agent_chat_url, agent_auth_headers,
    parse_model_ids, agent_model_choices,
    llm_models_for,
)
from korina.routes import register_routes





def local_model_roots() -> list[Path]:
    roots = list(LOCAL_MODEL_ROOTS)
    if LMSTUDIO_HUB_ROOT not in roots:
        roots.append(LMSTUDIO_HUB_ROOT)
    return roots


def discover_local_gguf_models() -> list[str]:
    found: set[str] = set()
    for root in local_model_roots():
        if not root.exists():
            continue
        for path in root.rglob('*.gguf'):
            lname = path.name.lower()
            if 'mmproj' in lname or lname.endswith('-assistant.gguf'):
                continue
            found.add(str(path))
    return sorted(found, key=lambda s: display_model_label(s).lower())


def discover_lmstudio_catalog_models() -> list[str]:
    found: set[str] = set()
    if LMSTUDIO_HUB_ROOT.exists():
        for manifest in LMSTUDIO_HUB_ROOT.rglob('manifest.json'):
            try:
                body = json.loads(manifest.read_text())
            except Exception:
                continue
            owner = body.get('owner')
            name = body.get('name')
            if isinstance(owner, str) and isinstance(name, str) and owner and name:
                found.add(f'{owner}/{name}')
    return sorted(found)


def find_mmproj_for_model(model_path: str) -> str:
    path = Path(str(model_path or '').strip())
    if not path.exists():
        return ''
    matches = sorted(path.parent.glob('*mmproj*.gguf'))
    return str(matches[0]) if matches else ''


def gui_env() -> dict:
    env = os.environ.copy()
    env.setdefault('DISPLAY', ':0')
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/1000')
    if not env.get('XAUTHORITY'):
        candidates = sorted(Path('/run/user/1000').glob('.mutter-Xwaylandauth.*'))
        if candidates:
            env['XAUTHORITY'] = str(candidates[-1])
        elif Path('/home/roggoz/.Xauthority').exists():
            env['XAUTHORITY'] = '/home/roggoz/.Xauthority'
    return env


def stop_lmstudio() -> None:
    subprocess.run(['pkill', '-f', '/opt/LM-Studio/lm-studio'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_lmstudio() -> None:
    if not LMSTUDIO_BIN.exists():
        raise RuntimeError('LM Studio binary not found at /opt/LM-Studio/lm-studio')
    already = subprocess.run(['pgrep', '-f', '/opt/LM-Studio/lm-studio'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if already.returncode == 0:
        return
    subprocess.Popen([str(LMSTUDIO_BIN)], env=gui_env(), cwd='/opt/LM-Studio', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stop_ollama() -> None:
    subprocess.run(['systemctl', '--user', 'stop', 'ollama.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', '-f', 'ollama serve'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_ollama() -> None:
    if shutil.which('ollama') is None:
        raise RuntimeError('Ollama is not installed on roggoz')
    run = subprocess.run(['systemctl', '--user', 'start', 'ollama.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if run.returncode != 0:
        subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def write_llama_server_unit(model_path: str, config: Optional[dict] = None) -> None:
    config = dict(config or load_config())
    model = Path(str(model_path or '').strip())
    if not model.exists():
        raise RuntimeError(f'llama.cpp model not found: {model}')
    if not LLAMA_SERVER_BIN.exists():
        raise RuntimeError(f'llama-server binary not found: {LLAMA_SERVER_BIN}')
    mmproj = find_mmproj_for_model(str(model))
    unit = [
        '[Unit]',
        'Description=Llama.cpp Server (Korina-selected model)',
        'After=network.target',
        '',
        '[Service]',
        'Type=simple',
        'Restart=always',
        'RestartSec=5',
        'Environment=VK_ICD_FILE=/usr/share/vulkan/icd.d/radeon_icd.json',
    ]
    exec_parts = [str(LLAMA_SERVER_BIN), '-m', str(model)]
    if mmproj:
        exec_parts += ['--mmproj', mmproj]
    exec_parts += ['--reasoning', config_llm_reasoning(config), '--host', '0.0.0.0', '--port', '8080', '--media-path', LLAMA_SERVER_MEDIA_PATH, '-ngl', '99', '-t', '4']
    unit.append('ExecStart=' + ' '.join(exec_parts))
    unit.append(f'WorkingDirectory={LLAMA_SERVER_BIN.parent}')
    unit += ['', '[Install]', 'WantedBy=default.target', '']
    service_path = LLAMA_SERVER_USER_UNIT
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text('\n'.join(unit))


def wait_for_llama_server_ready(timeout_seconds: float = 90.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ''
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/v1/models', timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as e:
            last_error = str(e)
        time.sleep(0.5)
    raise RuntimeError(f'llama-server did not become ready within {timeout_seconds:.0f}s: {last_error}')


def start_llama_server(model_path: str, config: Optional[dict] = None) -> None:
    write_llama_server_unit(model_path, config=config)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'enable', 'llama-server.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'restart', 'llama-server.service'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for_llama_server_ready()


def stop_llama_server() -> None:
    subprocess.run(['systemctl', '--user', 'stop', 'llama-server.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _resolve_llama_cpp_model_id(value: str) -> str:
    """Resolve a configured llama.cpp model to an absolute GGUF path on disk.

    Accepts:
    - an absolute path to a .gguf file
    - a bare model id like 'qwen3.5-2b-uncensored-hauhaucs-aggressive' that
      matches the basename of a discovered GGUF

    Returns the resolved absolute path, or the original string if it already
    looks like an absolute path (so write_llama_server_unit can raise its
    own clearer error). Returns '' if the input is empty.
    """
    v = str(value or '').strip()
    if not v:
        return ''
    p = Path(v)
    if p.is_absolute():
        return str(p)
    candidates = [str(g) for g in discover_local_gguf_models()]
    # Exact filename match first
    for g in candidates:
        if Path(g).name == v or Path(g).stem == v:
            return g
    # Suffix match (e.g. 'gemma-4-e2b' should match 'gemma-4-E2B_q4_0-it.gguf')
    lv = v.lower()
    for g in candidates:
        if lv in Path(g).name.lower():
            return g
    return v


def activate_llm_provider(provider: str, config: Optional[dict] = None, model: Optional[str] = None) -> dict:
    config = dict(config or load_config())
    provider = str(provider or config.get('llm_provider') or '').strip().lower()
    if provider == 'llama.cpp':
        raw_selected = str(model or config.get('lm_model') or '').strip()
        selected = _resolve_llama_cpp_model_id(raw_selected)
        if not selected or not Path(selected).exists():
            discovered = discover_local_gguf_models()
            if discovered:
                selected = discovered[0]
            else:
                raise RuntimeError('No local GGUF models found for llama.cpp')
        stop_lmstudio()
        stop_ollama()
        start_llama_server(selected, config=config)
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': selected, 'base_url': preset or 'http://127.0.0.1:8080/v1', 'stopped': ['lmstudio', 'ollama'], 'started': ['llama-server.service']}
    if provider == 'lmstudio':
        stop_llama_server()
        stop_ollama()
        start_lmstudio()
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:1234/v1', 'stopped': ['llama-server.service', 'ollama'], 'started': ['lm-studio']}
    if provider == 'ollama':
        stop_llama_server()
        stop_lmstudio()
        start_ollama()
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:11434/v1', 'stopped': ['llama-server.service', 'lmstudio'], 'started': ['ollama']}
    stop_llama_server()
    stop_lmstudio()
    stop_ollama()
    saved_base = str(config.get('llm_base_url') or '').strip()
    return {'provider': provider or 'openai-compatible', 'model': str(model or config.get('lm_model') or ''), 'base_url': saved_base, 'stopped': ['llama-server.service', 'lmstudio', 'ollama'], 'started': []}


class ProviderActivateRequest(BaseModel):
    provider: str
    model: Optional[str] = None










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
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    missing = missing_ack_phrases(voice, tag)
    with state.ack.queue_lock:
        existing = {(v, pid) for v, pid, _ in state.ack.queue} | set(state.ack.in_progress)
        added = 0
        for phrase in missing:
            key = (voice, phrase['id'])
            if key not in existing:
                state.ack.queue.append((voice, phrase['id'], phrase['text']))
                existing.add(key)
                added += 1
        if added and not state.ack.worker_running:
            state.ack.worker_running = True
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
    try:
        while True:
            with state.ack.queue_lock:
                if not state.ack.queue:
                    state.ack.worker_running = False
                    return
                voice, phrase_id, text = state.ack.queue.pop(0)
                state.ack.in_progress.add((voice, phrase_id))
            try:
                synthesize_ack_wav(voice, phrase_id, text)
                state.ack.last_generated = f'{voice}:{phrase_id}'
                state.ack.last_error = None
                print(f'[acks] generated {state.ack.last_generated}', flush=True)
            except Exception as e:
                state.ack.last_error = f'{voice}:{phrase_id}: {e}'
                print(f'[acks] generation failed: {state.ack.last_error}', flush=True)
                time.sleep(2)
            finally:
                with state.ack.queue_lock:
                    state.ack.in_progress.discard((voice, phrase_id))
    finally:
        with state.ack.queue_lock:
            if not state.ack.queue:
                state.ack.worker_running = False


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
    with state.ack.queue_lock:
        queued = len(state.ack.queue)
        worker_running = state.ack.worker_running
    return {
        'voice': voice,
        'manifest_count': len(manifest),
        'generated_count': len(generated),
        'missing_count': len(missing_ack_phrases(voice)),
        'queue_depth': queued,
        'in_progress': [f'{v}:{pid}' for v, pid in sorted(state.ack.in_progress)],
        'generating': worker_running,
        'last_generated': state.ack.last_generated,
        'last_error': state.ack.last_error,
    }


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int = 180
    model: str | None = None
    reasoning: str | None = None


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
    resolved = normalize_device(device, default_env=WHISPER_DEVICE)
    selected_model = (model_id or WHISPER_MODEL_ID).strip() or WHISPER_MODEL_ID
    compute_type = compute_type_for(resolved)
    key = f'{selected_model}:{resolved}:{compute_type}'
    if key in state.asr.models:
        state.asr.device = resolved
        state.asr.compute_type = compute_type
        return state.asr.models[key]
    with state.asr.lock:
        if key in state.asr.models:
            state.asr.device = resolved
            state.asr.compute_type = compute_type
            return state.asr.models[key]
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
        state.asr.models[key] = model
        state.asr.loaded_at_by_device[key] = time.time()
        state.asr.device = resolved
        state.asr.compute_type = compute_type
        print(f'[faster-whisper] loaded {key}', flush=True)
        return model


def transcribe_wav_segments(wav: Path, *, vad_filter: bool = True, device: Optional[str] = None, model_id: Optional[str] = None):
    """Yield faster-whisper segments.

    faster-whisper is lazy, so callers must consume the returned generator.
    Serializing inference avoids overlapping partial/final requests thrashing CPU/GPU.
    """
    asr = get_asr(device, model_id)
    with state.asr.infer_lock:
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
            config = load_config()
            try:
                result = lmstudio_transcribe_wav(wav, model=llm_model, base_url=config_stt_llm_chat_url(config), api_env=config_stt_llm_api_env(config))
                result.update({
                    'samples': int(len(data)),
                    'sample_rate': int(sr),
                    'duration': duration,
                    'partial_capable': True,
                })
                return result
            except RuntimeError as e:
                detail = str(e)
                unsupported_audio = ('input_audio' in detail or "either 'text' or 'image_url'" in detail or 'No multimodal STT model available' in detail)
                if not unsupported_audio:
                    raise
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
                    'model': model_id or config.get('stt_model') or WHISPER_MODEL_ID,
                    'backend': 'whisper-fallback',
                    'device': state.asr.device,
                    'requested_device': normalize_device(device, default_env=WHISPER_DEVICE),
                    'compute_type': state.asr.compute_type,
                    'language': info.language,
                    'language_probability': info.language_probability,
                    'warning': detail,
                    'segments': [
                        {'start': seg.start, 'end': seg.end, 'text': seg.text.strip()}
                        for seg in parts
                    ],
                }

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
            'device': state.asr.device,
            'requested_device': normalize_device(device, default_env=WHISPER_DEVICE),
            'compute_type': state.asr.compute_type,
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
    return llm_models_for(config_llm_base_url(config), str(config.get('llm_api_key_env') or ''))


def lmstudio_transcribe_wav(wav: Path, *, model: Optional[str] = None, base_url: Optional[str] = None, api_env: Optional[str] = None) -> dict:
    """Attempt audio transcription through a multimodal STT model endpoint.

    OpenAI-compatible multimodal endpoints definitely support text/images. Audio input
    depends on the loaded model + server support, so this endpoint returns a clear
    error if the selected model rejects input_audio.
    """
    config = load_config()
    resolved_base_url = str(base_url or config_stt_llm_chat_url(config)).strip()
    resolved_api_env = str(api_env or config_stt_llm_api_env(config) or '').strip()
    selected_model = (model or config_stt_llm_model(config)).strip()
    if not selected_model:
        available = llm_models_for(config_stt_llm_base_url(config), resolved_api_env)
        selected_model = available[0].strip() if available else ''
    if not selected_model:
        raise RuntimeError('No multimodal STT model available for the selected endpoint')
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
        'reasoning': config_stt_llm_reasoning(config),
    }
    headers = {'Content-Type': 'application/json'} | auth_headers_from_env(resolved_api_env)
    http_req = urllib.request.Request(
        resolved_base_url,
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
            f'Multimodal STT model {selected_model!r} rejected audio transcription request: HTTP {e.code}: {detail}'
        )
    text = (body.get('choices', [{}])[0].get('message', {}).get('content') or '').strip()
    return {
        'text': text,
        'seconds': time.time() - started,
        'model': selected_model,
        'backend': 'multimodal-stt',
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


def response_llm_chat(req: ChatRequest) -> str:
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

    config = load_config()
    payload = {
        'model': (req.model or str(config.get('lm_model') or LMSTUDIO_MODEL)),
        'messages': messages,
        'temperature': req.temperature,
        'max_tokens': req.max_tokens,
        'stream': False,
        'reasoning': (str(req.reasoning).strip().lower() if req.reasoning else config_llm_reasoning(config)),
    }
    data = json.dumps(payload).encode('utf-8')
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
        raise RuntimeError(f'Response LLM HTTP {e.code}: {detail}')
    except Exception as e:
        raise RuntimeError(f'Response LLM request failed: {e}')

    try:
        return format_voice_reply(body['choices'][0]['message']['content'] or '')
    except Exception:
        raise RuntimeError(f'Unexpected response LLM payload: {body!r}')


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
        f"injection_mode={config.get('agent_injection_mode')}, follow_up_mode={config.get('agent_follow_up_mode')}, "
        f"thinking_level={config.get('agent_thinking_level')}, auto_compact={config.get('agent_auto_compact')}."
    )
    system = (
        'You are Korina Agent, an agentic state tracker inspired by Pi Agent Harness concepts: maintain compact state, infer next useful injection, and do not chat with the user.\n'
        'Create a concise state report for Korina Converse to inject into its next spoken reply. Do not write the spoken reply. Do not use emojis.\n'
        'Start with exactly one line: Priority: low|normal|important|critical.\n'
        'Priority rules: low = bookkeeping/debug/no user-facing update. normal = useful state for the next reply only. important = user should hear this soon, but it can wait for a sentence boundary. critical = immediate safety/security/data-loss risk, time-sensitive blocking result, or explicit permission required before a tool call.\n'
        'Do NOT mark garbled STT/Whisper output, uncertain transcript text, routine model errors, repeated observations, or general warnings as critical. Treat transcript uncertainty as low or normal unless it creates an immediate unsafe action.\n'
        'If permission is required, include the exact phrase Permission request: followed by the requested action, risk, and yes/no question.\n'
        'Include: current user intent, relevant facts, unresolved tasks/questions, emotional/interaction notes, and suggested next-response injection.\n'
        'Korina Agent and Korina Converse exchange hidden state. Never speak directly to the user except by emitting important/critical reports for Converse to relay.\n'
        'If yolo_mode is on, be more decisive in suggested injection, but still never perform external side effects from this state-report endpoint.\n'
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
    with state.agent.lock:
        state.agent.event_seq += 1
        event = dict(event)
        event['id'] = state.agent.event_seq
        event['created_at'] = time.time()
        state.agent.events.append(event)
        del state.agent.events[:-100]
        return event


def agent_snapshot() -> dict:
    with state.agent.lock:
        return {
            'busy': state.agent.busy,
            'status': state.agent.status,
            'last_report': state.agent.last_report,
            'pending_injections': len(state.agent.pending_injections),
            'last_error': state.agent.last_error,
            'last_event_id': state.agent.event_seq,
        }


def run_agent_transcript_job(req: AgentTranscriptRequest) -> None:
    config = load_config()
    with state.agent.lock:
        if state.agent.busy:
            state.agent.pending_injections.append({
                'transcript': req.transcript,
                'reason': req.reason,
                'turn_count': req.turn_count,
                'created_at': time.time(),
            })
            queued_busy = True
        else:
            queued_busy = False
        if not queued_busy:
            state.agent.busy = True
            state.agent.status = 'working'
    if queued_busy:
        push_agent_event({'type': 'agent_status', 'status': 'busy_queued_injection', 'priority': 'low', 'message': 'Korina Agent is busy; transcript delta queued as injection.'})
        return
    push_agent_event({'type': 'agent_status', 'status': 'working', 'priority': 'low', 'message': 'Korina Agent received transcript update.'})
    try:
        previous = state.agent.last_report
        with state.agent.lock:
            if state.agent.pending_injections:
                injection_text = '\n\nQueued injection while busy:\n' + json.dumps(state.agent.pending_injections[-5:], ensure_ascii=False)
                state.agent.pending_injections.clear()
            else:
                injection_text = ''
        state_req = AgentStateRequest(
            transcript=req.transcript,
            previous_report=(previous + injection_text)[-6000:],
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
        duplicate_recent = report_hash == state.agent.last_emitted_report_hash and (now - state.agent.last_emitted_report_at) < 60 and priority != 'critical'
        state.agent.last_emitted_report_hash = report_hash
        state.agent.last_emitted_report_at = now
        with state.agent.lock:
            state.agent.last_report = report
            state.agent.status = 'idle'
            state.agent.last_error = None
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
        with state.agent.lock:
            state.agent.status = 'idle'
            state.agent.last_error = str(e)
        push_agent_event({'type': 'agent_error', 'priority': 'important', 'message': str(e)})
    finally:
        with state.agent.lock:
            state.agent.busy = False


def submit_agent_transcript(req: AgentTranscriptRequest) -> dict:
    config = load_config()
    if str(config.get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'accepted': False, 'disabled': True, 'status': agent_snapshot()}
    if req.delivery_mode == 'injection':
        with state.agent.lock:
            state.agent.pending_injections.append({'transcript': req.transcript, 'reason': req.reason, 'turn_count': req.turn_count, 'created_at': time.time()})
        push_agent_event({'type': 'agent_status', 'status': 'injection_received', 'priority': 'low', 'message': 'Transcript injection queued for Korina Agent.'})
        return {'ok': True, 'accepted': True, 'queued_as': 'injection', 'status': agent_snapshot()}
    threading.Thread(target=run_agent_transcript_job, args=(req,), daemon=True).start()
    return {'ok': True, 'accepted': True, 'queued_as': 'prompt', 'status': agent_snapshot()}


@app.on_event('startup')
def startup_generate_default_acks():
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    enqueue_missing_acks(str(load_config().get('voice') or ACK_DEFAULT_VOICE))


def _route_index():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail='index.html missing')
    return FileResponse(INDEX_PATH)


def _route_health():
    config = load_config()
    return {
        'ok': True,
        'service': 'korina-voice-lab',
        'whisper_backend': 'faster-whisper',
        'whisper_model': WHISPER_MODEL_ID,
        'whisper_model_choices': WHISPER_MODEL_CHOICES,
        'whisper_loaded': bool(state.asr.models),
        'whisper_loaded_devices': sorted(state.asr.models.keys()),
        'whisper_loaded_at_by_device': state.asr.loaded_at_by_device,
        'whisper_device': state.asr.device or normalize_device(None, default_env=WHISPER_DEVICE),
        'whisper_compute_type': state.asr.compute_type or compute_type_for(normalize_device(None, default_env=WHISPER_DEVICE)),
        'cuda_available': torch.cuda.is_available(),
        'whisper_beam_size': WHISPER_BEAM_SIZE,
        'whisper_cpu_threads': WHISPER_CPU_THREADS,
        'partial_min_seconds': PARTIAL_MIN_SECONDS,
        'min_speech_ms': config_min_speech_ms(config),
        'partial_window_ms': config_partial_window_ms(config),
        'cuda': torch.cuda.is_available(),
        'response_llm_provider': str(config.get('llm_provider') or 'llama.cpp'),
        'response_llm_base_url': config_llm_base_url(config),
        'response_llm_chat_url': config_llm_chat_url(config),
        'response_llm_model': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'agent_provider': agent_provider(config),
        'agent_base_url': agent_base_url(config),
        'agent_chat_url': agent_chat_url(config),
        'agent_model': str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
        'multimodal_stt_provider': config_stt_llm_provider(config),
        'multimodal_stt_base_url': config_stt_llm_base_url(config),
        'multimodal_stt_chat_url': config_stt_llm_chat_url(config),
        'multimodal_stt_model': config_stt_llm_model(config),
        'tts_base_url': config_tts_base_url(config),
        'ack_count': len(ack_files_for(state.ack.current_voice)),
        'ack_status': ack_status(state.ack.current_voice),
    }


def _route_get_config():
    return load_config()


def _route_update_config(payload: dict):
    current = load_config()
    previous = dict(current)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in CONFIG_KEYS:
                current[key] = value
    current = synchronize_llm_dependents(current, previous)
    saved = save_config(current)
    try:
        llm_provider_now = str(saved.get('llm_provider') or '').strip().lower()
        llm_provider_before = str(previous.get('llm_provider') or '').strip().lower()
        if llm_provider_now in {'llama.cpp', 'lmstudio', 'ollama'} and (
            str(saved.get('lm_model') or '') != str(previous.get('lm_model') or '')
            or config_llm_reasoning(saved) != config_llm_reasoning(previous)
            or llm_provider_before != llm_provider_now
        ):
            activate_llm_provider(llm_provider_now, saved, model=str(saved.get('lm_model') or ''))
        elif llm_provider_before in {'llama.cpp', 'lmstudio', 'ollama'} and llm_provider_now == 'openai-compatible':
            activate_llm_provider('openai-compatible', saved, model=str(saved.get('lm_model') or ''))
    except Exception:
        pass
    return saved


def _route_models(llm_base_url: Optional[str] = Query(None), llm_api_key_env: Optional[str] = Query(None), stt_llm_base_url: Optional[str] = Query(None), stt_llm_api_key_env: Optional[str] = Query(None)):
    config = load_config()
    llm_base = str(llm_base_url or config_llm_base_url(config)).strip().rstrip('/')
    llm_api_env_name = str(llm_api_key_env or config.get('llm_api_key_env') or '').strip()
    stt_base = str(stt_llm_base_url or config_stt_llm_base_url(config)).strip().rstrip('/')
    stt_api_env_name = str(stt_llm_api_key_env or config_stt_llm_api_env(config) or '').strip()
    llm_error = None
    llm_models = []
    stt_llm_error = None
    stt_llm_models = []
    try:
        llm_models = llm_models_for(llm_base, llm_api_env_name)
        if llm_base == "http://127.0.0.1:8080/v1":
            for d in discover_local_gguf_models():
                if d not in llm_models:
                    llm_models.append(d)
    except Exception as e:
        llm_error = str(e)
    try:
        stt_llm_models = llm_models_for(stt_base, stt_api_env_name)
        if stt_base == "http://127.0.0.1:8080/v1":
            for d in discover_local_gguf_models():
                if d not in stt_llm_models:
                    stt_llm_models.append(d)
    except Exception as e:
        stt_llm_error = str(e)
    return {
        'whisper_models': WHISPER_MODEL_CHOICES,
        'llm_models': llm_models,
        'llm_default': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'llm_base_url': llm_base,
        'llm_error': llm_error,
        'stt_llm_models': stt_llm_models,
        'stt_llm_default': config_stt_llm_model(config),
        'stt_llm_base_url': stt_base,
        'stt_llm_error': stt_llm_error,
        'llama_cpp_local_models': discover_local_gguf_models(),
        'lmstudio_catalog_models': discover_lmstudio_catalog_models(),
        'labels': {mid: display_model_label(mid) for mid in list(dict.fromkeys(llm_models + stt_llm_models + discover_local_gguf_models()))},
    }


def _route_activate_provider(req: ProviderActivateRequest):
    config = load_config()
    provider = str(req.provider or config.get('llm_provider') or 'openai-compatible').strip()
    current = dict(config)
    current['llm_provider'] = provider
    preset = provider_preset_base_url(provider)
    if provider == 'openai-compatible':
        current['llm_base_url'] = str(config.get('llm_base_url') or current.get('llm_base_url') or '').strip()
    elif preset:
        current['llm_base_url'] = preset
    if req.model:
        current['lm_model'] = str(req.model).strip()
    current = synchronize_llm_dependents(current, config)
    saved = save_config(current)
    try:
        result = activate_llm_provider(provider, saved, model=req.model)
        return {'ok': True, 'saved': saved, 'activation': result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _route_acks(voice: str = Query(ACK_DEFAULT_VOICE), tag: Optional[str] = Query(None)):
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    enqueue_missing_acks(voice, tag)
    return {
        'acks': ack_files_for(voice, tag),
        'tag': tag or 'global',
        'status': ack_status(voice),
    }


def _route_acks_status(voice: str = Query(ACK_DEFAULT_VOICE)):
    return ack_status((voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE)


def _route_acks_rebuild(payload: dict):
    voice = (payload.get('voice') or ACK_DEFAULT_VOICE).strip()
    tag = payload.get('tag')
    clear = bool(payload.get('clear', False))
    if clear:
        removed = clear_ack_wavs()
    else:
        removed = 0
    state.ack.current_voice = voice
    missing = enqueue_missing_acks(voice, tag)
    return {'ok': True, 'voice': voice, 'tag': tag, 'removed': removed, 'queued_or_missing': missing, 'status': ack_status(voice)}


def _route_agent_status():
    return {'ok': True, **agent_snapshot()}


def _route_agent_events(after: int = Query(0)):
    with state.agent.lock:
        events = [e for e in state.agent.events if int(e.get('id', 0)) > after]
        last_id = state.agent.event_seq
    return {'ok': True, 'events': events, 'last_event_id': last_id, **agent_snapshot()}


def _route_agent_transcript(req: AgentTranscriptRequest):
    return submit_agent_transcript(req)


def _route_agent_permission_answer(req: AgentPermissionAnswer):
    push_agent_event({'type': 'permission_answer', 'priority': 'normal', 'request_id': req.request_id, 'answer': req.answer, 'transcript': req.transcript})
    return submit_agent_transcript(AgentTranscriptRequest(transcript=req.transcript, delivery_mode='prompt', reason=f'permission_answer:{req.answer}', turn_count=0))


def _route_agent_reset():
    with state.agent.lock:
        state.agent.events.clear()
        state.agent.event_seq = 0
        state.agent.pending_injections.clear()
        state.agent.busy = False
        state.agent.status = 'idle'
        state.agent.last_report = ''
        state.agent.last_error = None
        state.agent.last_emitted_report_hash = ''
        state.agent.last_emitted_report_at = 0.0
    return {'ok': True, 'status': agent_snapshot()}


def _route_agent_models():
    try:
        models = agent_model_choices()
        return {'ok': True, 'models': models, 'provider': agent_provider(), 'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL), 'error': None}
    except Exception as e:
        return {'ok': False, 'models': [], 'provider': agent_provider(), 'default': str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL), 'error': str(e)}


def _route_agent_state_report(req: AgentStateRequest):
    if str(load_config().get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'state_report': '', 'disabled': True}
    try:
        report = generate_agent_state_report(req)
        return {'ok': True, 'state_report': report, 'model': req.model or str(load_config().get('agent_model') or load_config().get('lm_model') or LMSTUDIO_MODEL)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _route_transcribe(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
    suffix = Path(audio.filename or 'recording.webm').suffix or '.webm'
    try:
        return JSONResponse(transcribe_upload_file(audio.file, suffix, vad_filter=True, device=device, model_id=model, backend=backend, llm_model=llm_model))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _route_transcribe_partial(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
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


async def _route_transcribe_stream(audio: UploadFile = File(...), device: Optional[str] = Query(None), model: Optional[str] = Query(None), backend: str = Query('whisper'), llm_model: Optional[str] = Query(None)):
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
                    result = lmstudio_transcribe_wav(wav, model=llm_model, base_url=config_stt_llm_chat_url(load_config()), api_env=config_stt_llm_api_env(load_config()))
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
                        'backend': 'multimodal-stt',
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
                    'device': state.asr.device,
                    'compute_type': state.asr.compute_type,
                    'language': info.language,
                    'language_probability': info.language_probability,
                    'segments': count,
                })
            except Exception as e:
                yield sse_event('error', {'detail': str(e)})

    return StreamingResponse(events(), media_type='text/event-stream')


def _route_chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail='No message provided')
    started = time.time()
    try:
        reply = response_llm_chat(req)
        return JSONResponse({
            'reply': reply,
            'seconds': time.time() - started,
            'model': req.model or str(load_config().get('lm_model') or LMSTUDIO_MODEL),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




# ============================================================
# Korina Agent: State Report + Permission Answer Endpoints
# ============================================================

async def _route_alpha_agent_state_report(request: Request):
    """
    Receives transcript deltas/turns from Korina Converse.
    Returns state_report, interrupt, and/or permission_request.
    """
    body = await request.json()
    cfg = load_config()
    agent_cfg = {
        'enabled': cfg.get('agent_enabled') == 'on',
        'model': cfg.get('agent_model', ''),
        'base_url': cfg.get('agent_base_url', ''),
    }

    # TODO: wire up to Korina Agent Alpha here
    # For now, return empty responses — Agent not yet connected
    return {
        "state_report": None,
        "interrupt": None,
        "permission_request": None,
        "debug": {
            "turns_received": len(body.get('turns', [])),
            "trigger": body.get('trigger', '?'),
            "agent_enabled": agent_cfg['enabled'],
        }
    }


async def _route_alpha_agent_permission_answer(request: Request):
    """
    Receives the user's answer to a permission question.
    Routes it back to Korina Agent.
    """
    body = await request.json()
    request_id = body.get('request_id', 'unknown')
    answer = body.get('answer', 'no')
    # TODO: wire up to Korina Agent
    print(f"[Korina Agent] Permission answer: {request_id} -> {answer}")
    return {"ok": True, "request_id": request_id, "answer": answer}



register_routes(app, globals())

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8001)
