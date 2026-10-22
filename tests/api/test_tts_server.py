from __future__ import annotations

import asyncio
import io
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from korina.tts.runtime import SynthesisRuntime
from korina.tts.server import SpeechStreamResponse, create_app, sse_chunks


def make_runtime():
    def pipeline(text, voice, speed):
        yield text, voice, np.array([0, 100, -100], dtype=np.int16)
    return SynthesisRuntime(lambda device: pipeline)


@pytest.mark.parametrize('endpoint', ['/stream/speech', '/stream/wav', '/v1/audio/speech'])
def test_speech_routes_preserve_browser_input_schema(endpoint):
    with TestClient(create_app(make_runtime())) as client:
        response = client.post(endpoint, json={
            'input': 'Hello', 'voice': 'af_heart', 'speed': 1.0,
            'device': 'cpu', 'model': 'kokoro', 'provider': 'kokoro',
        })
        assert response.status_code == 200
        expected_type = 'text/event-stream' if endpoint == '/stream/speech' else 'audio/wav'
        assert expected_type in response.headers['content-type']
        if endpoint == '/v1/audio/speech':
            with wave.open(io.BytesIO(response.content)) as wav:
                assert wav.getnframes() == 3


def test_text_alias_and_invalid_inputs():
    with TestClient(create_app(make_runtime())) as client:
        assert client.post('/v1/audio/speech', json={'text': 'hello', 'device': 'cpu'}).status_code == 200
        assert client.post('/v1/audio/speech', json={'input': '  '}).status_code == 400
        for body in [
            {'input': 'x' * 8193}, {'input': 'hello', 'speed': 0},
            {'input': 'hello', 'device': 'typo'}, {'input': 'hello', 'voice': 'x' * 129},
        ]:
            assert client.post('/stream/speech', json=body).status_code == 422


def test_busy_model_is_retryable_and_health_stays_available(monkeypatch):
    engine = make_runtime()
    session = engine.open_session('hello', 'af_heart', 1, 'cpu')
    try:
        with TestClient(create_app(engine)) as client:
            response = client.post('/stream/speech', json={'input': 'hello', 'device': 'cpu'})
            assert response.status_code == 503
            assert response.headers['retry-after'] == '1'
            assert client.get('/health').json()['loaded'] is True
            assert 'af_heart' in client.get('/voices').json()['voices']
    finally:
        session.close()


def test_cors_uses_explicit_origins_without_credentials(monkeypatch):
    monkeypatch.setenv('KORINA_TTS_ALLOWED_ORIGINS', 'https://demo.example')
    with TestClient(create_app(make_runtime())) as client:
        response = client.options('/stream/speech', headers={
            'Origin': 'https://demo.example', 'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'Content-Type',
        })
        assert response.headers['access-control-allow-origin'] == 'https://demo.example'
        assert 'access-control-allow-credentials' not in response.headers
        denied = client.options('/stream/speech', headers={
            'Origin': 'https://other.example', 'Access-Control-Request-Method': 'POST',
        })
        assert denied.status_code == 400


def test_disconnect_before_first_chunk_releases_inference_slot():
    engine = make_runtime()
    session = engine.open_session('hello', 'af_heart', 1, 'cpu')
    response = SpeechStreamResponse(sse_chunks(session), session, media_type='text/event-stream')

    async def disconnected():
        return {'type': 'http.disconnect'}

    async def send(message):
        pass

    asyncio.run(response({'type': 'http', 'asgi': {'spec_version': '2.0'}}, disconnected, send))
    engine.open_session('again', 'af_heart', 1, 'cpu').close()


def test_send_error_releases_inference_slot():
    engine = make_runtime()
    session = engine.open_session('hello', 'af_heart', 1, 'cpu')
    response = SpeechStreamResponse(sse_chunks(session), session, media_type='text/event-stream')

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def broken_send(message):
        raise OSError('connection closed')

    with pytest.raises(Exception):
        asyncio.run(response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, broken_send))
    engine.open_session('again', 'af_heart', 1, 'cpu').close()
