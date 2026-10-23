from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from korina.runtime.json_http import RequestCancelledError
from korina.services import whistle_service as whistle

METADATA = {'duration': 1, 'samples': 16000, 'sample_rate': 16000}


@pytest.fixture
def engine(monkeypatch, tmp_path):
    for key, name in [('KORINA_WHISTLE_MODEL', 'whistle.cact'), ('NEEDLE3_LIB_PATH', 'libneedle.so')]:
        path = tmp_path / name
        path.write_bytes(b'fixture')
        monkeypatch.setenv(key, str(path))
    monkeypatch.setattr(whistle.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(whistle, '_model', None)
    monkeypatch.setattr(whistle, '_loaded_key', None)
    return tmp_path / 'input.wav'


def test_status_never_downloads_and_requires_both_local_assets(engine, monkeypatch):
    monkeypatch.setattr(whistle, '_load_model', lambda _: pytest.fail('health must not load an engine'))
    assert whistle.status()['available'] is True
    assert whistle.status()['loaded'] is False
    monkeypatch.delenv('NEEDLE3_LIB_PATH')
    assert whistle.status()['available'] is False
    with pytest.raises(RuntimeError, match='existing'):
        whistle.transcribe_wav(engine, metadata=METADATA)


def test_one_cached_engine_and_truthful_result(engine, monkeypatch):
    loads, calls = [], []

    class Model:
        def transcribe(self, audio, **kwargs):
            calls.append((audio, kwargs))
            return {'text': ' Actual local speech. ', 'language': 'en'}

    monkeypatch.setattr(whistle, '_load_model', lambda path: loads.append(path) or Model())
    for _ in range(2):
        result = whistle.transcribe_wav(engine, metadata=METADATA)
        assert result['backend'] == 'whistle' and result['device'] == 'cpu'
        assert result['model'] == 'Cactus-Compute/whistle'
        assert result['text'] == result['segments'][0]['text'] == 'Actual local speech.'
    assert len(loads) == 1 and len(calls) == 2 and whistle.status()['loaded'] is True
    assert calls[0][1] == {'language': 'en'}


def test_native_slot_stays_owned_until_cancelled_inference_finishes(engine, monkeypatch):
    entered, release, current = threading.Event(), threading.Event(), threading.Event()
    current.set()

    class Model:
        def transcribe(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return {'text': 'Retired reply'}

    monkeypatch.setattr(whistle, '_load_model', lambda _: Model())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(whistle.transcribe_wav, engine, metadata=METADATA, is_current=current.is_set)
        try:
            assert entered.wait(2)
            current.clear()
            with pytest.raises(whistle.WhistleBusyError):
                whistle.transcribe_wav(engine, metadata=METADATA)
        finally:
            release.set()
        with pytest.raises(RequestCancelledError):
            pending.result(timeout=2)
    assert whistle.transcribe_wav(engine, metadata=METADATA)['text'] == 'Retired reply'


def test_retired_owner_never_loads_and_failed_load_can_retry(engine, monkeypatch):
    monkeypatch.setattr(whistle, '_load_model', lambda _: pytest.fail('retired owner loaded weights'))
    with pytest.raises(RequestCancelledError):
        whistle.transcribe_wav(engine, metadata=METADATA, is_current=lambda: False)
    monkeypatch.setattr(whistle, '_load_model', lambda _: (_ for _ in ()).throw(RuntimeError('load failed')))
    for _ in range(2):
        with pytest.raises(RuntimeError, match='load failed'):
            whistle.transcribe_wav(engine, metadata=METADATA)
