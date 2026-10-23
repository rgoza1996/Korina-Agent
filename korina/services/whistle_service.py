"""Optional, serialized local Whistle inference with explicit offline assets."""
from __future__ import annotations

import importlib.util
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from korina.runtime.request_owner import check_current

_slot = threading.Lock()
_model = None
_loaded_key: tuple[str, str] | None = None


class WhistleBusyError(RuntimeError):
    """Whistle's process-wide native engine is serving another request."""


def _assets() -> tuple[str, str]:
    return (os.environ.get('KORINA_WHISTLE_MODEL', ''), os.environ.get('NEEDLE3_LIB_PATH', ''))


def status() -> dict:
    """Report availability without importing the engine or downloading assets."""
    weights, library = _assets()
    try:
        configured = bool(weights and library and Path(weights).is_file() and Path(library).is_file())
    except OSError:
        configured = False
    available = importlib.util.find_spec('needle') is not None and configured
    return {'available': available, 'loaded': available and _loaded_key == (weights, library),
            'model': 'Cactus-Compute/whistle', 'device': 'cpu',
            'error': None if available else 'Install the whistle extra and configure local Whistle weights and engine.'}


def _load_model(weights: str):
    # Whistle inference must not send telemetry. Explicit local paths also
    # prevent the package's default first-use model/engine downloads.
    os.environ['NEEDLE_TELEMETRY'] = '0'
    os.environ['DO_NOT_TRACK'] = '1'
    try:
        from needle import Whistle
    except ImportError as exc:
        raise RuntimeError('Whistle is unavailable; install korina-agent[whistle]') from exc
    return Whistle(weights=weights)


def transcribe_wav(wav: Path, *, metadata: dict, is_current: Callable[[], bool] | None = None) -> dict:
    """Use one native engine; retired requests cannot load, publish or queue work."""
    global _model, _loaded_key
    check_current(is_current)
    if not _slot.acquire(blocking=False):
        raise WhistleBusyError('Whistle is busy; retry after the active transcription finishes')
    started = time.perf_counter()
    try:
        check_current(is_current)
        key = _assets()
        if not status()['available']:
            raise RuntimeError('Whistle requires the whistle extra and existing KORINA_WHISTLE_MODEL / NEEDLE3_LIB_PATH files')
        if _loaded_key is not None and _loaded_key[1] != key[1]:
            raise RuntimeError('Restart Korina before changing the Whistle engine library')
        if _loaded_key != key:
            _model = _load_model(key[0])
            _loaded_key = key
        check_current(is_current)
        assert _model is not None
        result = _model.transcribe(str(wav), language='en')
        check_current(is_current)
        text = str(result.get('text') or '').strip()
        return {**metadata, 'text': text, 'seconds': time.perf_counter() - started,
                'model': 'Cactus-Compute/whistle', 'backend': 'whistle', 'device': 'cpu',
                'requested_device': 'cpu', 'language': result.get('language'),
                'partial_capable': True,
                'segments': [{'start': 0, 'end': metadata['duration'], 'text': text}] if text else []}
    finally:
        # Native inference finishes before releasing the engine; cancellation
        # discards its result rather than racing another request through it.
        _slot.release()
