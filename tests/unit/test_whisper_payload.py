"""Metadata inspection and shared Whisper payload construction."""
from types import SimpleNamespace


def test_audio_metadata_reads_header_without_decoding_samples(monkeypatch):
    from korina.services import whisper_service

    monkeypatch.setattr(whisper_service.sf, 'info', lambda path: SimpleNamespace(
        frames=16000 * 3600, samplerate=16000,
    ))
    monkeypatch.setattr(whisper_service.sf, 'read', lambda *args, **kwargs: (
        (_ for _ in ()).throw(AssertionError('must not allocate the full WAV'))
    ))
    assert whisper_service.audio_metadata('large.wav') == {
        'samples': 57600000, 'sample_rate': 16000, 'duration': 3600.0,
    }


def test_whisper_payload_accepts_lazy_segments_and_keeps_timestamps(monkeypatch):
    from korina.services import whisper_service

    monkeypatch.setattr(whisper_service, 'normalize_device', lambda *args, **kwargs: 'cpu')
    segments = iter([SimpleNamespace(text=' hello ', start=0.1, end=0.5)])
    result = whisper_service.whisper_payload(
        segments, SimpleNamespace(language='en', language_probability=0.9),
        metadata={'samples': 8000, 'sample_rate': 16000, 'duration': 0.5},
        started=0, model_id='tiny.en',
    )
    assert result['text'] == 'hello'
    assert result['segments'] == [{'start': 0.1, 'end': 0.5, 'text': 'hello'}]
    assert result['duration'] == 0.5
    assert result['requested_device'] == 'cpu'


def test_buffered_transcription_falls_back_on_network_timeout(monkeypatch):
    import io
    from korina.services import whisper_service

    monkeypatch.setattr(whisper_service, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(whisper_service, 'audio_metadata', lambda wav: {
        'samples': 16000, 'sample_rate': 16000, 'duration': 1.0,
    })

    def timeout(*args, **kwargs):
        raise TimeoutError('upstream timed out')

    monkeypatch.setattr(whisper_service, 'lmstudio_transcribe_wav', timeout)
    monkeypatch.setattr(whisper_service, 'transcribe_wav_segments', lambda *args, **kwargs: (
        [SimpleNamespace(text='Hello.', start=0, end=1)],
        SimpleNamespace(language='en', language_probability=1),
    ))
    result = whisper_service.transcribe_upload_file(io.BytesIO(b'audio'), '.wav', backend='llm')
    assert result['text'] == 'Hello.'
    assert result['backend'] == 'whisper-fallback'
    assert result['warning'] == 'upstream timed out'


def test_empty_multimodal_result_does_not_retry_failed_whisper_fallback(monkeypatch):
    import io
    import pytest
    from korina.services import whisper_service

    monkeypatch.setattr(whisper_service, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(whisper_service, 'audio_metadata', lambda wav: {
        'samples': 16000, 'sample_rate': 16000, 'duration': 1.0,
    })
    monkeypatch.setattr(whisper_service, 'lmstudio_transcribe_wav', lambda *args, **kwargs: {'text': ''})
    calls = []

    def unavailable(*args, **kwargs):
        calls.append('whisper')
        raise RuntimeError('whisper unavailable')

    monkeypatch.setattr(whisper_service, 'transcribe_wav_segments', unavailable)
    with pytest.raises(RuntimeError, match='whisper unavailable'):
        whisper_service.transcribe_upload_file(io.BytesIO(b'audio'), '.wav', backend='llm')
    assert calls == ['whisper']
