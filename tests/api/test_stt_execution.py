"""STT API contracts shared by buffered, partial and streaming requests."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


def _events(response):
    return [
        (block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
        for block in response.text.strip().split('\n\n')
    ]


@pytest.mark.parametrize('endpoint', ['/api/transcribe', '/api/transcribe/partial'])
def test_slow_transcription_does_not_block_api_event_loop(client, monkeypatch, endpoint):
    from korina.routes import stt

    entered = threading.Event()
    release = threading.Event()

    def slow_transcription(*args, **kwargs):
        entered.set()
        assert release.wait(5), 'test never released the transcription worker'
        return {'text': 'hello', 'duration': 1.0, 'segments': []}

    monkeypatch.setattr(stt, 'transcribe_upload_file', slow_transcription)
    with ThreadPoolExecutor(max_workers=2) as pool:
        transcription = pool.submit(
            client.post, endpoint,
            files={'audio': ('sample.wav', b'fake audio', 'audio/wav')},
        )
        try:
            assert entered.wait(2)
            status = pool.submit(client.get, '/api/capabilities')
            assert status.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert transcription.result(timeout=2).status_code == 200


def test_stream_preserves_segment_order_and_done_metadata(client, monkeypatch):
    from korina.routes import stt

    monkeypatch.setattr(stt, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(stt, 'audio_metadata', lambda wav: {
        'samples': 32000, 'sample_rate': 16000, 'duration': 2.0,
    })
    monkeypatch.setattr(stt, 'transcribe_wav_segments', lambda *args, **kwargs: (
        [SimpleNamespace(start=0, end=1, text='Hello '),
         SimpleNamespace(start=1, end=2, text='world.')],
        SimpleNamespace(language='en', language_probability=0.99),
    ))
    response = client.post('/api/transcribe/stream?model=base.en', files={
        'audio': ('sample.wav', b'fake audio', 'audio/wav'),
    })
    events = _events(response)
    assert [event for event, _ in events] == ['status', 'status', 'segment', 'segment', 'done']
    done = events[-1][1]
    assert done['text'] == 'Hello world.'
    assert done['segments'] == 2
    assert done['samples'] == 32000
    assert done['model'] == 'base.en'
    assert done['language_probability'] == 0.99


def test_stream_caches_structural_empty_result_and_skips_next_llm_call(
    client, config_module, monkeypatch,
):
    from korina.routes import stt

    config_module.save_config({
        **config_module.load_config(),
        'stt_llm_provider': 'lmstudio',
        'stt_llm_base_url': 'http://127.0.0.1:1234/v1',
        'audio_unsupported': {},
    })
    calls = []
    monkeypatch.setattr(stt, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(stt, 'audio_metadata', lambda wav: {
        'samples': 16000, 'sample_rate': 16000, 'duration': 1.0,
    })

    def multimodal(*args, **kwargs):
        calls.append('llm')
        return {'text': '', 'finish_reason': 'length'}

    monkeypatch.setattr(stt, 'lmstudio_transcribe_wav', multimodal)
    monkeypatch.setattr(stt, 'transcribe_wav_segments', lambda *args, **kwargs: (
        [SimpleNamespace(start=0, end=1, text='Quiet speech.')],
        SimpleNamespace(language='en', language_probability=1),
    ))
    for _ in range(2):
        response = client.post('/api/transcribe/stream?backend=llm&llm_model=audio-model', files={
            'audio': ('sample.wav', b'fake audio', 'audio/wav'),
        })
        done = _events(response)[-1]
        assert done[0] == 'done'
        assert done[1]['text'] == 'Quiet speech.'
        assert done[1]['fallback_reason'] == 'silent_200_empty_content'
    assert calls == ['llm']


def test_stream_reports_upload_read_failure_as_sse():
    from korina.routes.stt import _stream_events

    class BrokenUpload:
        def read(self, size):
            raise OSError('upload unreadable')

    events = list(_stream_events(
        BrokenUpload(), '.wav', device=None, model=None,
        backend='whisper', llm_model=None,
    ))
    assert len(events) == 1
    assert events[0].startswith('event: error\n')
    assert 'upload unreadable' in events[0]
