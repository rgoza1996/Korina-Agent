"""Bounded, lazy Kokoro synthesis shared by the HTTP response formats."""
from __future__ import annotations

import struct
import threading
from dataclasses import dataclass
from typing import Callable, Iterator

import numpy as np

from korina.runtime.request_owner import check_current

SAMPLE_RATE = 24000
MAX_AUDIO_SAMPLES = SAMPLE_RATE * 600


class SynthesisBusy(RuntimeError):
    """The single inference slot is already serving another utterance."""


def cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except (ImportError, RuntimeError):
        return False


def normalize_device(requested: str | None = None) -> str:
    value = (requested or '').strip().lower()
    if value in {'gpu', 'cuda'}:
        return 'cuda'
    if value == 'cpu':
        return 'cpu'
    return 'cuda' if cuda_available() else 'cpu'


def _check_audio_shape(shape, max_samples: int | None) -> None:
    if len(shape) != 1:
        raise ValueError('Kokoro must return mono audio')
    if max_samples is not None and shape[0] > max_samples:
        raise RuntimeError('Generated speech exceeds the ten minute audio limit')


def to_int16(audio, *, max_samples: int | None = None) -> np.ndarray:
    """Produce mono little-endian PCM without importing torch eagerly."""
    shape = getattr(audio, 'shape', None)
    if shape is not None:
        # Tensor metadata is available before an expensive GPU-to-CPU copy.
        _check_audio_shape(shape, max_samples)
    if hasattr(audio, 'detach'):
        audio = audio.detach().cpu().numpy()
    audio = np.asarray(audio)
    _check_audio_shape(audio.shape, max_samples)
    if audio.dtype == np.int16:
        return audio.astype('<i2', copy=False)
    audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype('<i2')


def wav_header(num_samples: int | None) -> bytes:
    """PCM WAV header; all-ones sizes indicate an unknown live stream length."""
    size = num_samples * 2 if num_samples is not None else 0xFFFFFFFF
    riff_size = size + 36 if num_samples is not None else 0xFFFFFFFF
    return struct.pack(
        '<4sI4s4sIHHIIHH4sI', b'RIFF', riff_size, b'WAVE', b'fmt ', 16,
        1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, b'data', size,
    )


def _pipeline_factory(device: str):
    try:
        from kokoro import KPipeline
    except ImportError as exc:
        raise RuntimeError('Kokoro is unavailable; install the korina-agent[tts] extra') from exc
    return KPipeline(lang_code='a', device=device)


@dataclass(frozen=True)
class AudioChunk:
    gs: str
    ps: str
    pcm: bytes
    samples: int


class SynthesisSession:
    """Own inference until advancement and cleanup finish, including disconnects."""

    def __init__(self, generator, device: str, release: Callable[[], None],
                 *, is_current: Callable[[], bool] | None = None):
        self.device = device
        self._generator = iter(generator)
        self._release = release
        self._is_current = is_current
        self._operation_lock = threading.Lock()
        self._close_requested = threading.Event()
        self._closed = False
        self._primed = False
        self._total_samples = 0
        self._first: AudioChunk | None = None

    def _next_chunk_locked(self) -> AudioChunk:
        while not self._close_requested.is_set():
            check_current(self._is_current)
            gs, ps, audio = next(self._generator)
            check_current(self._is_current)
            if self._close_requested.is_set():
                break
            pcm = to_int16(audio, max_samples=MAX_AUDIO_SAMPLES - self._total_samples)
            check_current(self._is_current)
            samples = len(pcm)
            if not samples:
                continue
            if self._close_requested.is_set():
                break
            self._total_samples += samples
            return AudioChunk(str(gs), str(ps), pcm.tobytes(), samples)
        raise StopIteration

    def prime(self) -> None:
        """Fail before sending HTTP headers if loading or first inference fails."""
        with self._operation_lock:
            if self._close_requested.is_set():
                raise RuntimeError('Synthesis session is closed')
            if self._primed:
                return
            try:
                self._first = self._next_chunk_locked()
                self._primed = True
            except StopIteration as exc:
                if self._close_requested.is_set():
                    raise RuntimeError('Synthesis session is closed') from exc
                raise RuntimeError('No audio generated') from exc

    def chunks(self) -> Iterator[AudioChunk]:
        try:
            while True:
                with self._operation_lock:
                    check_current(self._is_current)
                    if self._close_requested.is_set():
                        return
                    if self._first is not None:
                        chunk, self._first = self._first, None
                    else:
                        try:
                            chunk = self._next_chunk_locked()
                        except StopIteration:
                            return
                # Never hold the operation lock while the HTTP consumer sends.
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        self._close_requested.set()
        # Native inference cannot be force-cancelled. Keep the runtime slot
        # until any active next()/conversion and generator cleanup complete.
        with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            self._first = None
            try:
                close = getattr(self._generator, 'close', None)
                if close is not None:
                    close()
            finally:
                self._release()


class SynthesisRuntime:
    """Cache one model per device and serialize access to model inference.

    Admission is nonblocking: a busy model returns a retryable HTTP response,
    instead of growing an inference queue or a thread per request.
    """

    def __init__(self, pipeline_factory: Callable = _pipeline_factory):
        self._factory = pipeline_factory
        self._pipelines: dict = {}
        self._load_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._slot = threading.BoundedSemaphore(1)
        self._last_device: str | None = None
        self._loading_device: str | None = None

    def get_pipeline(self, device: str):
        with self._load_lock:
            with self._state_lock:
                cached = self._pipelines.get(device)
                self._loading_device = device if cached is None else None
            try:
                if cached is None:
                    cached = self._factory(device)
                    with self._state_lock:
                        self._pipelines[device] = cached
                with self._state_lock:
                    self._last_device = device
                return cached
            finally:
                with self._state_lock:
                    self._loading_device = None

    def open_session(self, text: str, voice: str, speed: float, device: str | None,
                     *, is_current: Callable[[], bool] | None = None) -> SynthesisSession:
        check_current(is_current)
        if not self._slot.acquire(blocking=False):
            raise SynthesisBusy('Kokoro is already synthesizing speech; retry shortly')
        session = None
        try:
            resolved = normalize_device(device)
            check_current(is_current)
            pipeline = self.get_pipeline(resolved)
            check_current(is_current)
            session = SynthesisSession(pipeline(text, voice=voice, speed=speed), resolved,
                                       self._slot.release, is_current=is_current)
            session.prime()
            return session
        except BaseException:
            if session is None:
                self._slot.release()
            else:
                session.close()
            raise

    def health(self) -> dict:
        # Loading a large model must not hold up liveness requests.
        with self._state_lock:
            result = {
                'status': 'ok', 'loaded': bool(self._pipelines),
                'loaded_devices': sorted(self._pipelines), 'device': self._last_device,
                'loading_device': self._loading_device,
            }
        missing = []
        if self._factory is _pipeline_factory:
            import importlib.util
            missing = [name for name in ('kokoro', 'torch') if importlib.util.find_spec(name) is None]
        return {
            **result, 'ok': not missing, 'runtime_available': not missing,
            'status': 'degraded' if missing else 'ok',
            'error': f'Missing optional TTS dependencies: {", ".join(missing)}; install korina-agent[tts]' if missing else None,
            'cuda_available': cuda_available(),
        }
