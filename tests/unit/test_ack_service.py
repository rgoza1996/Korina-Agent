"""ACK cache bounds, cancellation, disabled behavior, and WAV publication."""
from __future__ import annotations

import io
import threading
import time

import pytest

from korina.runtime.state import AckState
from korina.services import ack_service


WAV = b'RIFF' + (100).to_bytes(4, 'little') + b'WAVE' + b'\0' * 100


@pytest.fixture
def ack_runtime(monkeypatch, tmp_path):
    config = {'ack_enabled': 'on'}
    cache = tmp_path / 'acks'
    cache.mkdir()
    phrase = {'id': 'test_phrase', 'text': 'Okay.', 'tags': ['global']}
    monkeypatch.setattr(ack_service.state, 'ack', AckState())
    monkeypatch.setattr(ack_service, 'ACK_DIR', cache)
    monkeypatch.setattr(ack_service, 'load_ack_manifest', lambda: [phrase])
    monkeypatch.setattr(ack_service, 'load_config', lambda: config)
    monkeypatch.setattr(ack_service, 'config_tts_base_url', lambda: 'http://tts.test')
    return {'config': config, 'cache': cache, 'phrase': phrase,
            'enqueue': ack_service.enqueue_missing_acks}


def wait_for_worker():
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with ack_service.state.ack.queue_lock:
            if not ack_service.state.ack.worker_running:
                return
        time.sleep(0.005)
    pytest.fail('ACK worker did not finish')


def test_pending_queue_is_globally_bounded_and_deduplicated(ack_runtime, monkeypatch):
    # Hold the worker before it starts so hundreds of voices can contend.
    monkeypatch.setattr(ack_service.threading.Thread, 'start', lambda self: None)
    for index in range(500):
        ack_runtime['enqueue'](f'voice_{index}')
    assert len(ack_service.state.ack.queue) == ack_service.MAX_ACK_QUEUE
    before = list(ack_service.state.ack.queue)
    ack_runtime['enqueue']('voice_0')
    assert ack_service.state.ack.queue == before
    assert ack_service.ack_status('voice_0')['queue_limit'] == ack_service.MAX_ACK_QUEUE


def test_disabled_background_enqueue_never_starts_worker(ack_runtime, monkeypatch):
    ack_runtime['config']['ack_enabled'] = 'off'
    monkeypatch.setattr(ack_service.threading.Thread, 'start', lambda self: pytest.fail('disabled work started'))
    assert ack_runtime['enqueue']('af_heart') == 1  # still report the missing phrase
    assert ack_service.state.ack.queue == []
    assert ack_service.ack_status('af_heart')['enabled'] is False


def test_shutdown_cancels_pending_work_without_deleting_published_cache(ack_runtime, monkeypatch):
    monkeypatch.setattr(ack_service.threading.Thread, 'start', lambda self: None)
    cache = ack_runtime['cache'] / 'published.wav'
    cache.write_bytes(WAV)
    ack_runtime['enqueue']('af_heart')
    before = ack_service.state.ack.generation
    ack_service.cancel_ack_generation()
    assert ack_service.state.ack.queue == []
    assert ack_service.state.ack.generation > before
    assert cache.read_bytes() == WAV


def test_clear_invalidates_inflight_audio_and_allows_same_phrase_rebuild(ack_runtime, monkeypatch):
    first_started, release_first = threading.Event(), threading.Event()
    second_started, release_second = threading.Event(), threading.Event()
    calls = 0

    class BlockedAudio(io.BytesIO):
        def __init__(self, started, release, suffix):
            super().__init__(WAV + suffix)
            self.started, self.release = started, release
            self.first = True

        def read(self, size=-1):
            assert size > 0  # never read an unbounded response
            if self.first:
                self.first = False
                self.started.set()
                assert self.release.wait(3)
            return super().read(size)

    def response(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return BlockedAudio(first_started, release_first, b'OLD')
        return BlockedAudio(second_started, release_second, b'NEW')

    monkeypatch.setattr(ack_service.urllib.request, 'urlopen', response)
    destination = ack_service.ack_path('af_heart', ack_runtime['phrase'])
    try:
        ack_runtime['enqueue']('af_heart')
        assert first_started.wait(3)
        ack_service.clear_ack_wavs()
        assert ack_service.state.ack.queue == []
        ack_runtime['enqueue']('af_heart', force=True)
        release_first.set()
        assert second_started.wait(3)
        assert not destination.exists()
        assert ack_service.ack_status('af_heart')['last_generated'] is None
        release_second.set()
        wait_for_worker()
        assert destination.read_bytes() == WAV + b'NEW'
        assert ack_service.ack_status('af_heart')['in_progress'] == []
        assert list(ack_runtime['cache'].glob('*.tmp')) == []
    finally:
        release_first.set()
        release_second.set()
        wait_for_worker()


def test_turning_ack_off_while_response_is_inflight_suppresses_publication(ack_runtime, monkeypatch):
    class DisableDuringRead(io.BytesIO):
        def read(self, size=-1):
            ack_runtime['config']['ack_enabled'] = 'off'
            return super().read(size)

    monkeypatch.setattr(ack_service.urllib.request, 'urlopen', lambda *args, **kwargs: DisableDuringRead(WAV))
    assert ack_service.synthesize_ack_wav('af_heart', 'test_phrase', 'Okay.') is False
    assert list(ack_runtime['cache'].iterdir()) == []


def test_oversize_or_invalid_response_never_publishes_partial_wav(ack_runtime, monkeypatch):
    monkeypatch.setattr(ack_service, 'MAX_ACK_WAV_BYTES', 100)
    sizes = []

    class TrackedAudio(io.BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)

    monkeypatch.setattr(ack_service.urllib.request, 'urlopen', lambda *args, **kwargs: TrackedAudio(WAV + b'x' * 1000))
    with pytest.raises(RuntimeError, match='exceeds'):
        ack_service.synthesize_ack_wav('af_heart', 'test_phrase', 'Okay.')
    assert sizes and all(0 < size <= 65536 for size in sizes)
    assert list(ack_runtime['cache'].iterdir()) == []
    monkeypatch.setattr(ack_service.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'not audio' * 8))
    with pytest.raises(RuntimeError, match='not a WAV'):
        ack_service.synthesize_ack_wav('af_heart', 'test_phrase', 'Okay.')
    assert list(ack_runtime['cache'].iterdir()) == []


def test_status_tolerates_concurrent_inprogress_updates_and_cache_clear(ack_runtime):
    finished = threading.Event()

    def mutate():
        for index in range(300):
            with ack_service.state.ack.queue_lock:
                ack_service.state.ack.in_progress[('af_heart', str(index))] = 0
                if index:
                    ack_service.state.ack.in_progress.pop(('af_heart', str(index - 1)), None)
        finished.set()

    worker = threading.Thread(target=mutate)
    worker.start()
    while not finished.is_set():
        assert isinstance(ack_service.ack_status('af_heart')['in_progress'], list)
    worker.join(3)
    assert ack_service.ack_status('af_heart')['manifest_count'] == 1
