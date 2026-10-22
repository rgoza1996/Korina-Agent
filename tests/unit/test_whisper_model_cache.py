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
