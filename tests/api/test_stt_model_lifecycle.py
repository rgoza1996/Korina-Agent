"""Streaming disconnect cleanup and retryable model switches use real leases."""
from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace

import pytest

from korina.runtime.state import AsrState
from korina.services import whisper_service as whisper


@pytest.fixture
def model(monkeypatch):
    from korina.routes import stt

    owner = AsrState()
    monkeypatch.setattr(whisper.state, 'asr', owner)

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(text='hello', start=0, end=1)] * 20), SimpleNamespace(language='en')

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(whisper, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(whisper, 'audio_metadata', lambda path: {'duration': 1, 'samples': 16000, 'sample_rate': 16000})
    monkeypatch.setattr(stt, 'convert_to_16k_wav', lambda *args: None)
    monkeypatch.setattr(stt, 'audio_metadata', whisper.audio_metadata)
    return owner


@pytest.mark.parametrize('endpoint', ['/api/transcribe', '/api/transcribe/partial'])
def test_switch_during_transcription_is_retryable_and_existing_model_can_finish(client, model, endpoint):
    from pathlib import Path

    parts, _ = whisper.transcribe_wav_segments(Path('fake.wav'), device='cpu', model_id='first')
    try:
        response = client.post(endpoint + '?model=second&device=cpu', files={'audio': ('test.wav', b'fixture', 'audio/wav')})
        assert response.status_code == 503
        assert response.headers['retry-after'] == '1'
        assert 'another model' in response.json()['detail']
        assert len(list(parts)) == 20
    finally:
        parts.close()
    assert model.model_users == 0
    response = client.post(endpoint + '?model=second&device=cpu', files={'audio': ('test.wav', b'fixture', 'audio/wav')})
    assert response.status_code == 200
    assert model.model_users == 0
    assert len(model.models) == 1


@pytest.mark.parametrize('spec_version', ['2.0', '2.4'])
def test_stream_disconnect_after_first_segment_releases_model_and_audio_files(model, spec_version):
    from korina.routes import stt

    response = stt.SttStreamResponse(stt._stream_events(
        io.BytesIO(b'fixture'), '.wav', device='cpu', model='first', backend='whisper', llm_model=None,
    ))
    seen_segment = []

    async def run():
        disconnected = asyncio.Event()

        async def receive():
            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            if message['type'] == 'http.response.body' and b'event: segment' in message.get('body', b''):
                seen_segment.append(True)
                assert model.model_users == 1
                if spec_version == '2.4':
                    raise OSError('client disconnected')
                disconnected.set()

        await response({'type': 'http', 'asgi': {'spec_version': spec_version}}, receive, send)

    if spec_version == '2.4':
        with pytest.raises(Exception):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert seen_segment
    assert model.model_users == 0
    from pathlib import Path
    parts, _ = whisper.transcribe_wav_segments(Path('new.wav'), device='cpu', model_id='second')
    parts.close()
    assert list(model.models) == ['second:cpu:int8']


def test_multimodal_fallback_exhausts_and_releases_whisper_model(client, model, monkeypatch):
    monkeypatch.setattr(whisper, 'lmstudio_transcribe_wav', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('fixture LLM unavailable')))
    response = client.post('/api/transcribe/partial?model=first&device=cpu&backend=llm', files={'audio': ('test.wav', b'fixture', 'audio/wav')})
    assert response.status_code == 200
    assert response.json()['backend'] == 'whisper-fallback'
    assert model.model_users == 0
