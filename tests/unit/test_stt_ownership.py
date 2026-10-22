"""Retired STT owners do not enter fallback, cache mutation, or new native work."""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from korina.routes import stt
from korina.runtime.json_http import RequestCancelledError
from korina.services import stt_resolution, whisper_service
from korina.services.multimodal_stt import MultimodalSttError


@pytest.mark.parametrize('outcome', ['cancelled', 'success', 'unsupported', 'failure'])
def test_retired_provider_completion_never_starts_fallback_or_mutates_cache(config_module, outcome):
    config = config_module.update_config_values({
        'stt_llm_provider': 'openai-compatible', 'stt_llm_base_url': 'http://fixture/v1',
        'stt_llm_model': 'audio-model', 'audio_unsupported': {},
    })
    current = True
    cancelled = RequestCancelledError('cancelled provider fixture')
    fallbacks = []

    def provider(*args, **kwargs):
        nonlocal current
        assert kwargs['is_current']()
        current = False
        if outcome == 'cancelled':
            raise cancelled
        if outcome == 'unsupported':
            raise MultimodalSttError('audio-model', 415, 'audio unsupported')
        if outcome == 'failure':
            raise OSError('disconnected provider')
        return {'text': 'stale transcript', 'model': 'audio-model'}

    with pytest.raises(RequestCancelledError) as caught:
        stt_resolution.resolve_multimodal_transcription(
            Path('unused.wav'), model=None, config=config, transcribe=provider,
            fallback=lambda: fallbacks.append(True) or {'text': 'forbidden'}, is_current=lambda: current,
        )
    if outcome == 'cancelled':
        assert caught.value is cancelled
    assert fallbacks == []
    assert config_module.config_audio_unsupported() == {}


def test_retired_cached_request_does_not_admit_whisper(config_module):
    config_module.set_audio_unsupported('openai-compatible', 'http://fixture/v1', 'audio-model', 'unsupported', '')
    config = {**config_module.load_config(), 'stt_llm_provider': 'openai-compatible',
              'stt_llm_base_url': 'http://fixture/v1', 'stt_llm_model': 'audio-model'}
    calls = []
    with pytest.raises(RequestCancelledError):
        stt_resolution.resolve_multimodal_transcription(
            Path('unused.wav'), model=None, config=config, is_current=lambda: False,
            transcribe=lambda *args, **kwargs: calls.append('provider'),
            fallback=lambda: calls.append('native'),
        )
    assert calls == []


def test_retirement_between_cache_lookup_and_fallback_prevents_native_admission(monkeypatch):
    current = True
    calls = []

    def cached(**kwargs):
        nonlocal current
        current = False
        return True, kwargs['whisper_fallback_fn']()

    monkeypatch.setattr(stt_resolution, 'maybe_fallback_to_whisper', cached)
    with pytest.raises(RequestCancelledError):
        stt_resolution.resolve_multimodal_transcription(
            Path('unused.wav'), model='audio-model', config={}, transcribe=lambda *args, **kwargs: None,
            fallback=lambda: calls.append('native'), is_current=lambda: current,
        )
    assert calls == []


def test_retirement_during_existing_native_fallback_discards_its_result():
    current = True

    def failed(*args, **kwargs):
        raise OSError('provider unavailable')

    def native():
        nonlocal current
        current = False
        return {'text': 'late native fallback result'}

    with pytest.raises(RequestCancelledError):
        stt_resolution.resolve_multimodal_transcription(
            Path('unused.wav'), model=None, config={}, transcribe=failed,
            fallback=native, is_current=lambda: current,
        )


def test_stream_retired_after_status_does_not_admit_native_model(monkeypatch):
    current = True
    admitted = []
    monkeypatch.setattr(stt, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(stt, 'audio_metadata', lambda wav: {'samples': 16000, 'sample_rate': 16000, 'duration': 1})
    monkeypatch.setattr(stt, 'transcribe_wav_segments', lambda *args, **kwargs: admitted.append(True))
    stream = stt._stream_events(io.BytesIO(b'fixture'), '.wav', device=None, model=None,
                                backend='whisper', llm_model=None, is_current=lambda: current)
    assert 'converting' in next(stream)
    assert 'transcribing' in next(stream)
    current = False
    assert list(stream) == []
    assert admitted == []


def test_stream_retired_after_converting_status_does_not_start_decoder(monkeypatch):
    current = True
    decoded = []
    monkeypatch.setattr(stt, 'convert_to_16k_wav', lambda *args: decoded.append(True))
    stream = stt._stream_events(io.BytesIO(b'fixture'), '.wav', device=None, model=None,
                                backend='whisper', llm_model=None, is_current=lambda: current)
    assert 'converting' in next(stream)
    current = False
    assert list(stream) == []
    assert decoded == []


def test_buffered_upload_retired_during_copy_does_not_start_decoder(monkeypatch):
    current = True
    decoded = []
    copy = whisper_service.shutil.copyfileobj

    def copied(*args):
        nonlocal current
        copy(*args)
        current = False

    monkeypatch.setattr(whisper_service.shutil, 'copyfileobj', copied)
    monkeypatch.setattr(whisper_service, 'convert_to_16k_wav', lambda *args: decoded.append(True))
    with pytest.raises(RequestCancelledError):
        whisper_service.transcribe_upload_file(io.BytesIO(b'fixture'), '.wav', is_current=lambda: current)
    assert decoded == []


def test_retired_lazy_stream_does_not_start_its_next_native_segment():
    calls = []

    class Segments:
        def __iter__(self):
            return self

        def __next__(self):
            calls.append('native next')
            return SimpleNamespace(text='fixture', start=0, end=1)

        def close(self):
            calls.append('closed')

    stream = stt._emit_whisper_sse(Segments(), SimpleNamespace(), metadata={}, started=0,
                                   device='cpu', model='fixture', is_current=lambda: False)
    with pytest.raises(RequestCancelledError):
        next(stream)
    assert calls == ['closed']


@pytest.mark.parametrize('backend', ['whisper', 'llm'])
@pytest.mark.parametrize('vad_filter', [True, False])
def test_buffered_and_partial_stop_before_later_native_steps(monkeypatch, backend, vad_filter):
    current = True
    calls = []

    class Segments:
        def __iter__(self):
            return self

        def __next__(self):
            nonlocal current
            calls.append('native step')
            current = False
            return SimpleNamespace(text='late native segment', start=0, end=1)

        def close(self):
            calls.append('closed')

    monkeypatch.setattr(whisper_service, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(whisper_service, 'audio_metadata', lambda wav: {'duration': 1})
    monkeypatch.setattr(whisper_service, 'transcribe_wav_segments', lambda *args, **kwargs: (Segments(), SimpleNamespace()))

    def unavailable(*args, **kwargs):
        raise OSError('provider unavailable')

    monkeypatch.setattr(whisper_service, 'lmstudio_transcribe_wav', unavailable)
    with pytest.raises(RequestCancelledError):
        whisper_service.transcribe_upload_file(io.BytesIO(b'fixture'), '.wav', backend=backend,
                                               vad_filter=vad_filter, is_current=lambda: current)
    assert calls == ['native step', 'closed']


def test_stream_fallback_stops_before_later_native_steps(monkeypatch):
    current = True
    calls = []

    class Segments:
        def __iter__(self):
            return self

        def __next__(self):
            nonlocal current
            calls.append('native step')
            current = False
            return SimpleNamespace(text='late native segment', start=0, end=1)

        def close(self):
            calls.append('closed')

    monkeypatch.setattr(stt, 'transcribe_wav_segments', lambda *args, **kwargs: (Segments(), SimpleNamespace()))

    def unavailable(*args, **kwargs):
        raise OSError('provider unavailable')

    monkeypatch.setattr(stt, 'lmstudio_transcribe_wav', unavailable)
    with pytest.raises(RequestCancelledError):
        stt._multimodal_payload(Path('unused.wav'), device='cpu', model=None, llm_model=None,
                                started=0, metadata={}, is_current=lambda: current)
    assert calls == ['native step', 'closed']
