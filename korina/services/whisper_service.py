from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import json
import shutil
import soundfile as sf
import subprocess
import tempfile
import threading
import time

from korina.config import (
    config_stt_llm_api_env,
    config_stt_llm_chat_url,
    load_config,
)

from korina.runtime import state
from korina.runtime.limits import bounded_seconds, resolve_audio_limits

from korina.services.multimodal_stt import (
    lmstudio_transcribe_wav,
    transient_whisper_fallback_reason,
)

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


class WhisperBusyError(RuntimeError):
    """A different model is still serving a lazy transcription."""


def get_asr(device: Optional[str] = None, model_id: Optional[str] = None):
    resolved = normalize_device(device, default_env=WHISPER_DEVICE)
    selected_model = (model_id or WHISPER_MODEL_ID).strip() or WHISPER_MODEL_ID
    compute_type = compute_type_for(resolved)
    key = f'{selected_model}:{resolved}:{compute_type}'
    with state.asr.lock:
        if key in state.asr.models:
            state.asr.device = resolved
            state.asr.compute_type = compute_type
            return state.asr.models[key]
        if state.asr.model_users:
            raise WhisperBusyError('Whisper is transcribing with another model or device; retry after it finishes')
        # Release only idle cache references before allocating replacement weights.
        # Active iterators pin their model; never force native teardown mid-inference.
        state.asr.models.clear()
        state.asr.loaded_at_by_device.clear()
        state.asr.device = None
        state.asr.compute_type = None
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


class _WhisperSegments:
    """Own an inference iterator/model lease across pool-thread boundaries."""

    def __init__(self, iterator, model, owner):
        self._iterator = iterator
        self._model = model
        self._owner = owner
        self._closed = False
        self._operation = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self):
        with self._operation:
            if self._closed:
                raise StopIteration
            try:
                with self._owner.infer_lock:
                    return next(self._iterator)
            except BaseException:
                self._close_locked()
                raise

    def _close_locked(self):
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._iterator, 'close', None)
            if close is not None:
                with self._owner.infer_lock:
                    close()
        finally:
            self._iterator = None
            self._model = None
            with self._owner.lock:
                self._owner.model_users -= 1

    def close(self):
        with self._operation:
            self._close_locked()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

@dataclass(frozen=True)
class WhisperInferenceInfo:
    """Keep resolved inference settings beside this request's model result."""

    result: Any
    model_id: str
    device: str
    compute_type: str

    def __getattr__(self, name):
        return getattr(self.result, name)


def transcribe_wav_segments(wav: Path, *, vad_filter: bool = True, device: Optional[str] = None, model_id: Optional[str] = None):
    """Return lazy segments, serializing each model operation off the event loop.

    StreamingResponse may advance its iterator on different pool threads, so
    never hold an RLock across a yield. Each inference step acquires/releases
    the lock in the same thread; callers can emit the first segment promptly.
    """
    resolved_device = normalize_device(device, default_env=WHISPER_DEVICE)
    selected_model = (model_id or WHISPER_MODEL_ID).strip() or WHISPER_MODEL_ID
    owner = state.asr
    with owner.lock:
        asr = get_asr(resolved_device, selected_model)
        owner.model_users += 1
    try:
        with owner.infer_lock:
            segments, info = asr.transcribe(
                str(wav),
                beam_size=WHISPER_BEAM_SIZE,
                vad_filter=vad_filter,
                language='en',
                condition_on_previous_text=False,
            )
        parts = _WhisperSegments(iter(segments), asr, owner)
    except BaseException:
        with owner.lock:
            owner.model_users -= 1
        raise

    return parts, WhisperInferenceInfo(
        info, selected_model, resolved_device, compute_type_for(resolved_device),
    )


def audio_metadata(wav: Path) -> dict:
    """Inspect the WAV header without loading the entire recording into memory."""
    info = sf.info(wav)
    return {
        'samples': int(info.frames),
        'sample_rate': int(info.samplerate),
        'duration': float(info.frames) / float(info.samplerate or 16000),
    }


def whisper_payload(parts, info, *, metadata: dict, started: float,
                    device: Optional[str] = None, model_id: Optional[str] = None,
                    backend: str = 'faster-whisper') -> dict:
    """Build the common buffered/streaming Whisper result contract."""
    parts = list(parts)
    if isinstance(info, WhisperInferenceInfo):
        resolved_device = info.device
        resolved_model = info.model_id
        resolved_compute_type = info.compute_type
    else:
        resolved_device = normalize_device(device, default_env=WHISPER_DEVICE)
        resolved_model = model_id or WHISPER_MODEL_ID
        resolved_compute_type = compute_type_for(resolved_device)
    return {
        'text': ''.join(seg.text for seg in parts).strip(),
        'seconds': time.time() - started,
        **metadata,
        'model': resolved_model,
        'backend': backend,
        'device': resolved_device,
        'requested_device': resolved_device,
        'compute_type': resolved_compute_type,
        'language': getattr(info, 'language', None),
        'language_probability': getattr(info, 'language_probability', None),
        'segments': [
            {'start': seg.start, 'end': seg.end, 'text': seg.text.strip()}
            for seg in parts
        ],
    }


def transcribe_upload_file(audio_file, suffix: str, *, vad_filter: bool = True, device: Optional[str] = None, model_id: Optional[str] = None, backend: str = 'whisper', llm_model: Optional[str] = None) -> dict:
    with tempfile.TemporaryDirectory(prefix='korina-stt-') as td:
        td_path = Path(td)
        src = td_path / f'input{suffix}'
        wav = td_path / 'input-16k.wav'
        with src.open('wb') as f:
            shutil.copyfileobj(audio_file, f)
        convert_to_16k_wav(src, wav)
        metadata = audio_metadata(wav)
        if backend == 'llm':
            config = load_config()

            def whisper_fallback(detail: str, fallback_reason: str) -> dict:
                started = time.time()
                parts, info = transcribe_wav_segments(wav, vad_filter=vad_filter, device=device, model_id=model_id)
                return {
                    **whisper_payload(
                        parts, info, metadata=metadata, started=started,
                        device=device,
                        model_id=model_id or config.get('stt_model'),
                        backend='whisper-fallback',
                    ),
                    'warning': detail,
                    'fallback_reason': fallback_reason,
                }

            try:
                result = lmstudio_transcribe_wav(
                    wav, model=llm_model, base_url=config_stt_llm_chat_url(config),
                    api_env=config_stt_llm_api_env(config),
                )
            except Exception as exc:
                return whisper_fallback(str(exc), 'multimodal_call_failed')
            text_out = str(result.get('text', '') or '').strip()
            if not text_out:
                reason = transient_whisper_fallback_reason('empty_text')
                if reason:
                    return whisper_fallback('empty_text', reason)
            result.update({'text': text_out, **metadata, 'partial_capable': True})
            return result

        started = time.time()
        parts, info = transcribe_wav_segments(wav, vad_filter=vad_filter, device=device, model_id=model_id)
        return whisper_payload(
            parts, info, metadata=metadata, started=started,
            device=device, model_id=model_id,
        )


def sse_event(event: str, payload: dict) -> str:
    return f'event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'

class AudioDurationLimitError(ValueError):
    """A recording exceeds the configured decoded duration limit."""


def convert_to_16k_wav(src: Path, dst: Path) -> None:
    """Decode a bounded mono WAV, even when a small upload expands greatly.

    Decode one extra second to distinguish an overlong recording from one
    ending exactly at the limit, then reject rather than silently truncate it.
    The output byte ceiling and subprocess timeout bound disk and CPU work.
    """
    max_seconds = resolve_audio_limits()['max_duration_seconds']
    timeout = bounded_seconds('KORINA_FFMPEG_TIMEOUT_SECONDS', 60, 300)
    cmd = [
        'ffmpeg', '-y', '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-i', str(src), '-map', '0:a:0', '-vn',
        '-t', str(max_seconds + 1),
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
        '-fs', str(int((max_seconds + 1) * 32000) + 4096),
        '-f', 'wav', str(dst),
    ]
    # A malformed file can print many decode errors. Spool diagnostics to disk
    # and read only a short tail instead of retaining an unbounded PIPE buffer.
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=errors, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'Audio conversion exceeded the {timeout:g}s time limit') from exc
        if proc.returncode != 0:
            errors.seek(0, 2)
            errors.seek(max(0, errors.tell() - 8192))
            detail = errors.read().decode('utf-8', errors='replace').strip()
            raise RuntimeError(detail or 'ffmpeg conversion failed')
    if audio_metadata(dst)['duration'] > max_seconds:
        raise AudioDurationLimitError(f'Audio exceeds the {max_seconds:g}s decoded duration limit')
