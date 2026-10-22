"""Retirement during real config-lock waits cannot publish stale STT state."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from korina.runtime.json_http import RequestCancelledError
from korina.services import audio_probe, stt_resolution
from korina.services.multimodal_stt import MultimodalSttError


class ObservedConfigLock:
    """Observe a worker waiting on the existing transaction's real RLock."""

    def __init__(self, actual):
        self.actual = actual
        self.worker = None
        self.watching = False
        self.waiting = threading.Event()

    def __enter__(self):
        if self.watching and threading.get_ident() == self.worker:
            self.waiting.set()
        self.actual.acquire()
        return self

    def __exit__(self, *args):
        self.actual.release()


@pytest.mark.parametrize('stage,outcome', [
    ('initial_read', 'unsupported'),
    ('failure_read', 'unsupported'), ('failure_read', 'empty'),
    ('failure_write', 'unsupported'), ('failure_write', 'empty'),
])
def test_shared_resolution_rechecks_owner_after_cache_transaction_wait(
    config_module, monkeypatch, stage, outcome,
):
    config = config_module.update_config_values({
        'stt_llm_provider': 'openai-compatible',
        'stt_llm_base_url': 'http://fixture.test/v1', 'stt_llm_model': 'audio-model',
    })
    original_file = config_module.CONFIG_PATH.read_bytes()
    lock = ObservedConfigLock(config_module._CONFIG_LOCK)
    monkeypatch.setattr(config_module, '_CONFIG_LOCK', lock)
    current, entered, release = threading.Event(), threading.Event(), threading.Event()
    current.set()
    calls, fallbacks = [], []

    def transcribe(*args, **kwargs):
        calls.append('provider')
        if stage == 'failure_read':
            entered.set()
            assert release.wait(3)
        if outcome == 'unsupported':
            raise MultimodalSttError('audio-model', 415, 'audio unsupported')
        return {'text': '', 'finish_reason': 'length', 'model': 'audio-model'}

    if stage == 'failure_write':
        classify = audio_probe.classify_failure

        def paused_classification(status, body, finish_reason=''):
            result = classify(status, body, finish_reason=finish_reason)
            if status in (200, 415):
                entered.set()
                assert release.wait(3)
            return result

        monkeypatch.setattr(audio_probe, 'classify_failure', paused_classification)

    def resolve():
        lock.worker = threading.get_ident()
        return stt_resolution.resolve_multimodal_transcription(
            Path('unused.wav'), model='audio-model', config=config,
            transcribe=transcribe, is_current=current.is_set,
            fallback=lambda: fallbacks.append('native') or {'text': 'forbidden'},
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        if stage == 'initial_read':
            with lock:
                lock.watching = True
                pending = pool.submit(resolve)
                assert lock.waiting.wait(2)
                current.clear()
        else:
            pending = pool.submit(resolve)
            assert entered.wait(2)
            with lock:
                lock.watching = True
                release.set()
                assert lock.waiting.wait(2)
                current.clear()
        with pytest.raises(RequestCancelledError):
            pending.result(timeout=2)

    assert calls == ([] if stage == 'initial_read' else ['provider'])
    assert fallbacks == []
    assert config_module.config_audio_unsupported() == {}
    assert config_module.CONFIG_PATH.read_bytes() == original_file
