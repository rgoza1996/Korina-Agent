"""Bound cached weights while preserving active lazy segment iterators."""
from __future__ import annotations

import gc
import sys
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from korina.runtime.state import AsrState
from korina.runtime.json_http import RequestCancelledError
from korina.services import whisper_service as whisper


@pytest.fixture
def factory(monkeypatch):
    owner = AsrState()
    monkeypatch.setattr(whisper.state, 'asr', owner)
    created, references = [], []
    behavior = SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([
        SimpleNamespace(text='first', start=0, end=1),
        SimpleNamespace(text='second', start=1, end=2),
    ]), SimpleNamespace(language='en')))

    class Model:
        def __init__(self, model_id, **kwargs):
            created.append((model_id, kwargs['device'], kwargs['compute_type']))
            references.append(weakref.ref(self))

        def transcribe(self, *args, **kwargs):
            return behavior.transcribe(*args, **kwargs)

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(whisper, 'WHISPER_COMPUTE_TYPE', None)
    return SimpleNamespace(owner=owner, created=created, references=references, behavior=behavior)


def test_sequential_arbitrary_selections_keep_only_one_idle_model_and_timestamp(factory):
    for index in range(8):
        whisper.get_asr('cpu', f'custom/model-{index}')
        assert len(factory.owner.models) == 1
        assert len(factory.owner.loaded_at_by_device) == 1
    gc.collect()
    assert sum(reference() is not None for reference in factory.references) == 1
    assert list(factory.owner.models) == ['custom/model-7:cpu:int8']


@pytest.mark.parametrize('change', ['model', 'device', 'compute'])
def test_active_iterator_pins_model_and_rejects_only_different_selection(factory, monkeypatch, change):
    parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='first')
    assert factory.owner.model_users == 1
    options = {'device': 'cpu', 'model_id': 'first'}
    if change == 'model':
        options['model_id'] = 'second'
    elif change == 'device':
        options['device'] = 'cuda'
    else:
        monkeypatch.setattr(whisper, 'WHISPER_COMPUTE_TYPE', 'float32')
    with pytest.raises(whisper.WhisperBusyError):
        whisper.transcribe_wav_segments(Path('fake.wav'), **options)
    assert len(factory.created) == 1
    assert factory.references[0]() is not None
    parts.close()  # Also releases ownership before the first next().
    assert factory.owner.model_users == 0
    replacement, _ = whisper.transcribe_wav_segments(Path('fake.wav'), **options)
    assert len(factory.created) == 2
    assert factory.references[0]() is None
    replacement.close()
    assert factory.owner.model_users == 0


def test_same_model_requests_share_weights_and_serialize_inference_across_threads(factory):
    active = maximum = 0
    guard = threading.Lock()

    def segments():
        nonlocal active, maximum
        for index in range(2):
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.01)
            with guard:
                active -= 1
            yield SimpleNamespace(text=str(index), start=index, end=index + 1)

    factory.behavior.transcribe = lambda *args, **kwargs: (segments(), SimpleNamespace(language='en'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(whisper.transcribe_wav_segments, Path('fake.wav'), device='cpu', model_id='same') for _ in range(2)]
        first, second = [request.result(timeout=2)[0] for request in requests]
        assert factory.owner.model_users == 2
        assert len(factory.created) == 1
        results = [pool.submit(list, parts) for parts in [first, second]]
        assert [len(result.result(timeout=2)) for result in results] == [2, 2]
    assert maximum == 1
    assert factory.owner.model_users == 0


@pytest.mark.parametrize('failure', ['initial', 'iteration', 'close'])
def test_failure_releases_usage_and_allows_switching(factory, failure):
    class Broken:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError('iteration failed')

        def close(self):
            if failure == 'close':
                raise RuntimeError('close failed')

    def transcribe(*args, **kwargs):
        if failure == 'initial':
            raise RuntimeError('initial failed')
        return Broken(), SimpleNamespace(language='en')

    factory.behavior.transcribe = transcribe
    with pytest.raises(RuntimeError, match='failed'):
        parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='broken')
        if failure == 'iteration':
            next(parts)
        else:
            parts.close()
    assert factory.owner.model_users == 0
    whisper.get_asr('cpu', 'replacement')
    assert len(factory.owner.models) == 1


def test_discarded_unconsumed_iterator_releases_its_lease(factory):
    parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='discarded')
    assert factory.owner.model_users == 1
    del parts
    gc.collect()
    assert factory.owner.model_users == 0


@pytest.mark.parametrize('stage', ['cache', 'factory', 'next'])
def test_retired_inference_waiter_skips_native_admission_and_reuses_cache_worker(factory, stage):
    class ObservedLock:
        def __init__(self):
            self.lock = threading.RLock()
            self.waiting = threading.Event()

        def __enter__(self):
            self.waiting.set()
            self.lock.acquire()
            return self

        def __exit__(self, *args):
            self.lock.release()

    calls, workers = [], []
    retired = threading.Event()
    current = lambda: not retired.is_set()

    def transcribe(*args, **kwargs):
        calls.append('transcribe')

        def segments():
            calls.append('next')
            yield SimpleNamespace(text='fresh', start=0, end=1)

        return segments(), SimpleNamespace(language='en')

    factory.behavior.transcribe = transcribe
    whisper.get_asr('cpu', 'same')
    cached = factory.owner.models.copy()
    lock = ObservedLock()
    if stage == 'cache':
        factory.owner.lock = lock
    else:
        factory.owner.infer_lock = lock
    parts = None
    if stage == 'next':
        parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='same',
                                                   is_current=current)
    before = calls.copy()
    lock.waiting.clear()

    def consume(parts=None):
        workers.append(threading.get_ident())
        if parts is None:
            parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='same',
                                                       is_current=current)
        return list(whisper.owned_whisper_segments(parts, is_current=current))

    with ThreadPoolExecutor(max_workers=1) as pool:
        # Signal actual lock admission, then retire the waiter while the lock
        # remains held. No clock deadline or sleep decides the interleaving.
        with lock.lock:
            pending = pool.submit(consume, parts)
            assert lock.waiting.wait(2)
            retired.set()
            assert calls == before
        with pytest.raises(RequestCancelledError):
            pending.result(timeout=2)
        assert calls == before
        assert factory.owner.model_users == 0
        assert factory.owner.models == cached
        retired.clear()
        fresh = pool.submit(consume).result(timeout=2)
    assert [segment.text for segment in fresh] == ['fresh']
    assert calls == [*before, 'transcribe', 'next']
    assert len(set(workers)) == 1
    assert len(factory.created) == 1
    assert factory.owner.model_users == 0


@pytest.mark.parametrize('stage', ['factory', 'next'])
def test_retirement_drains_active_native_work_before_releasing_lease(factory, stage):
    entered, release, retired = threading.Event(), threading.Event(), threading.Event()
    closed, workers = [], []
    current = lambda: not retired.is_set()

    def native_step():
        entered.set()
        assert release.wait(2)

    class Segments:
        def __iter__(self):
            return self

        def __next__(self):
            if stage == 'next':
                native_step()
            return SimpleNamespace(text='late', start=0, end=1)

        def close(self):
            closed.append(True)

    def transcribe(*args, **kwargs):
        if stage == 'factory':
            native_step()
        return Segments(), SimpleNamespace(language='en')

    factory.behavior.transcribe = transcribe

    def consume():
        workers.append(threading.get_ident())
        parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='same',
                                                   is_current=current)
        return list(whisper.owned_whisper_segments(parts, is_current=current))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(consume)
        try:
            assert entered.wait(2)
            retired.set()
            assert not pending.done()
            assert factory.owner.model_users == 1
            with pytest.raises(whisper.WhisperBusyError):
                whisper.get_asr('cpu', 'replacement')
            assert len(factory.created) == 1
        finally:
            release.set()
        with pytest.raises(RequestCancelledError):
            pending.result(timeout=2)
        assert closed == [True]
        assert factory.owner.model_users == 0
        retired.clear()
        factory.behavior.transcribe = lambda *args, **kwargs: (
            iter([SimpleNamespace(text='fresh', start=0, end=1)]), SimpleNamespace(language='en'),
        )
        fresh = pool.submit(consume).result(timeout=2)
    assert [segment.text for segment in fresh] == ['fresh']
    assert len(set(workers)) == 1
    assert len(factory.created) == 1
    assert factory.owner.model_users == 0
