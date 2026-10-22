from __future__ import annotations

import base64
import io
import json
import struct
import threading
import wave
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import numpy as np
import pytest

from korina.tts import runtime as module
from korina.tts.runtime import SAMPLE_RATE, SynthesisBusy, SynthesisRuntime, SynthesisSession, to_int16
from korina.tts.server import sse_chunks, wav_chunks


def pipeline(text, voice, speed):
    yield 'first', 'f', np.array([0.0, 1.0, -1.0], dtype=np.float32)
    yield 'second', 's', np.array([100, 200], dtype=np.int16)


def runtime(factory=None):
    return SynthesisRuntime(factory or (lambda device: pipeline))


def open_session(engine):
    return engine.open_session('hello', 'af_heart', 1.0, 'cpu')


def test_sse_pcm_contract_matches_browser():
    chunks = list(sse_chunks(open_session(runtime())))
    events = [json.loads(chunk.removeprefix('data: ')) for chunk in chunks]
    assert [event['samples'] for event in events] == [3, 2]
    assert events[0]['sample_rate'] == SAMPLE_RATE
    assert events[0]['device'] == 'cpu'
    assert np.frombuffer(base64.b64decode(events[0]['audio']), dtype='<i2').tolist() == [0, 32767, -32767]


def test_default_health_reports_missing_optional_runtime_without_importing_models(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None)
    result = SynthesisRuntime().health()
    assert result['ok'] is False
    assert result['runtime_available'] is False
    assert result['loaded'] is False
    assert 'kokoro' in result['error']
    assert runtime().health()['ok'] is True  # injected runtimes have their own dependencies


def test_live_wav_has_one_header_and_each_pcm_chunk_once():
    chunks = list(wav_chunks(open_session(runtime())))
    assert len(chunks) == 3
    assert len(chunks[0]) == 44
    assert chunks[0][:4] == b'RIFF'
    assert struct.unpack_from('<I', chunks[0], 40) == (0xFFFFFFFF,)
    assert np.frombuffer(b''.join(chunks[1:]), dtype='<i2').tolist() == [0, 32767, -32767, 100, 200]


def test_buffered_wav_header_has_exact_sample_count():
    session = open_session(runtime())
    pcm = b''.join(chunk.pcm for chunk in session.chunks())
    with wave.open(io.BytesIO(module.wav_header(len(pcm) // 2) + pcm)) as wav:
        assert wav.getnframes() == 5
        assert wav.getframerate() == SAMPLE_RATE
        assert wav.getnchannels() == 1
        assert wav.readframes(5) == pcm


def test_busy_admission_and_early_close_release_slot():
    engine = runtime()
    session = open_session(engine)
    with pytest.raises(SynthesisBusy):
        open_session(engine)
    chunks = session.chunks()
    next(chunks)
    chunks.close()
    session.close()  # idempotent cleanup
    assert len(list(open_session(engine).chunks())) == 2


def test_load_and_first_inference_errors_release_slot():
    fail = True

    def factory(device):
        nonlocal fail
        if fail:
            fail = False
            raise RuntimeError('load failed')
        return pipeline

    engine = runtime(factory)
    with pytest.raises(RuntimeError, match='load failed'):
        open_session(engine)
    assert len(list(open_session(engine).chunks())) == 2

    def broken(text, voice, speed):
        raise RuntimeError('inference failed')
        yield  # pragma: no cover

    engine = runtime(lambda device: broken)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='inference failed'):
            open_session(engine)


def test_midstream_error_releases_slot():
    def broken(text, voice, speed):
        yield 'ok', '', np.zeros(1)
        raise RuntimeError('midstream')

    engine = runtime(lambda device: broken)
    with pytest.raises(RuntimeError, match='midstream'):
        list(open_session(engine).chunks())
    # It is an inference error again, never a leaked busy slot.
    with pytest.raises(RuntimeError, match='midstream'):
        list(open_session(engine).chunks())


def test_concurrent_close_waits_for_active_inference_and_releases_slot_once(monkeypatch):
    monkeypatch.setattr(module, 'cuda_available', lambda: False)
    entered, release, finalized = threading.Event(), threading.Event(), threading.Event()

    def blocked(text, voice, speed):
        try:
            yield 'first', '', np.zeros(1)
            entered.set()
            assert release.wait(3)
            yield 'late', '', np.zeros(1)
        finally:
            finalized.set()

    engine = runtime(lambda device: blocked)
    session = open_session(engine)
    chunks = session.chunks()
    assert next(chunks).gs == 'first'
    closing_started = threading.Event()

    def close():
        closing_started.set()
        session.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        advancing = pool.submit(next, chunks)
        assert entered.wait(1)
        closers = [pool.submit(close) for _ in range(3)]
        try:
            assert closing_started.wait(1)
            for closing in closers:
                with pytest.raises(TimeoutError):
                    closing.result(0.02)
            with pytest.raises(SynthesisBusy):
                open_session(engine)
            assert engine.health()['loaded'] is True
            assert not finalized.is_set()
        finally:
            release.set()
        with pytest.raises(StopIteration):
            advancing.result(1)  # The disconnected client never receives the late chunk.
        for closing in closers:
            closing.result(1)
    assert finalized.is_set()
    session.close()
    open_session(engine).close()


def test_close_serializes_with_prime_and_stops_before_delivering_its_audio():
    entered, release = threading.Event(), threading.Event()
    releases = []

    def blocked():
        entered.set()
        assert release.wait(3)
        yield '', '', np.zeros(1)

    session = SynthesisSession(blocked(), 'cpu', lambda: releases.append('released'))
    closing_started = threading.Event()

    def close():
        closing_started.set()
        session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        priming = pool.submit(session.prime)
        assert entered.wait(1)
        closing = pool.submit(close)
        try:
            assert closing_started.wait(1)
            with pytest.raises(TimeoutError):
                closing.result(0.02)
            assert releases == []
        finally:
            release.set()
        with pytest.raises(RuntimeError, match='closed'):
            priming.result(1)
        closing.result(1)
    assert list(session.chunks()) == []
    session.close()
    assert releases == ['released']


def test_lazy_pipeline_load_is_synchronized_and_health_remains_responsive(monkeypatch):
    monkeypatch.setattr(module, 'cuda_available', lambda: False)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def factory(device):
        calls.append(device)
        entered.set()
        assert release.wait(2)
        return pipeline

    engine = runtime(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engine.get_pipeline, 'cpu')
        assert entered.wait(1)
        second = pool.submit(engine.get_pipeline, 'cpu')
        try:
            health = engine.health()
            assert health['loaded'] is False
            assert health['loading_device'] == 'cpu'
        finally:
            release.set()
        assert first.result(2) is second.result(2)
    assert calls == ['cpu']
    assert engine.health()['loaded_devices'] == ['cpu']


def test_generated_audio_limit_stops_runaway_pipeline(monkeypatch):
    monkeypatch.setattr(module, 'MAX_AUDIO_SAMPLES', 3)
    engine = runtime()
    with pytest.raises(RuntimeError, match='audio limit'):
        list(open_session(engine).chunks())
    open_session(engine).close()


def test_oversized_generated_chunk_is_rejected_before_pcm_copies(monkeypatch):
    monkeypatch.setattr(module, 'MAX_AUDIO_SAMPLES', 3)

    def too_large(text, voice, speed):
        yield '', '', np.zeros(4, dtype=np.float32)

    def unexpected_copy(*args, **kwargs):
        raise AssertionError('Oversized audio must be rejected before NumPy PCM copies')

    monkeypatch.setattr(module.np, 'nan_to_num', unexpected_copy)
    engine = runtime(lambda device: too_large)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='audio limit'):
            open_session(engine)


def test_generated_sample_budget_counts_prior_chunks_before_converting_next(monkeypatch):
    monkeypatch.setattr(module, 'MAX_AUDIO_SAMPLES', 3)
    converted = []
    convert = module.to_int16

    def track(audio, **kwargs):
        result = convert(audio, **kwargs)
        converted.append(len(result))
        return result

    def too_long(text, voice, speed):
        yield '', '', np.zeros(2)
        yield '', '', np.zeros(2)

    monkeypatch.setattr(module, 'to_int16', track)
    with pytest.raises(RuntimeError, match='audio limit'):
        list(open_session(runtime(lambda device: too_long)).chunks())
    assert converted == [2]


def test_oversized_tensor_is_rejected_before_cpu_transfer():
    class Tensor:
        shape = (4,)

        def detach(self):
            raise AssertionError('Tensor size must be checked before detach/CPU transfer')

    with pytest.raises(RuntimeError, match='audio limit'):
        to_int16(Tensor(), max_samples=3)


def test_pcm_handles_tensor_without_torch_import_and_nonfinite_values():
    class Tensor:
        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.array([float('nan'), float('inf'), -float('inf')])

    assert to_int16(Tensor()).tolist() == [0, 32767, -32767]
    with pytest.raises(ValueError, match='mono'):
        to_int16(np.zeros((3, 2)))
