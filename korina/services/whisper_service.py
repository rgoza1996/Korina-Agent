from __future__ import annotations

import json
import numpy as np
import shutil
import soundfile as sf
import subprocess
import tempfile
import time

from korina.config import (
    config_stt_llm_api_env,
    config_stt_llm_chat_url,
    load_config,
)

from korina.runtime import state

from korina.services.multimodal_stt import lmstudio_transcribe_wav

from korina.util.paths import (
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_CPU_THREADS,
    WHISPER_DEVICE,
    WHISPER_MODEL_ID,
)

from pathlib import Path

from typing import Optional


def cuda_available() -> bool:
    """Return whether torch reports CUDA, without making torch a CI dependency."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def normalize_device(requested: Optional[str], *, default_env: Optional[str] = None) -> str:
    value = (requested or default_env or '').strip().lower()
    if value in ('gpu', 'cuda'):
        return 'cuda'
    if value == 'cpu':
        return 'cpu'
    return 'cuda' if cuda_available() else 'cpu'

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
