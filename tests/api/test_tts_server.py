from __future__ import annotations

import asyncio
import io
import threading
import wave

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from korina.tts import runtime as runtime_module
from korina.tts.runtime import SynthesisBusy, SynthesisRuntime
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


@pytest.mark.parametrize('endpoint', ['/stream/speech', '/stream/wav', '/v1/audio/speech'])
def test_speech_json_requires_a_media_type_before_loading_a_model(endpoint):
    def unexpected_load(device):
        pytest.fail('An untyped body must not load a speech model')

    with TestClient(create_app(SynthesisRuntime(unexpected_load))) as client:
        response = client.post(endpoint, content=b'{"input":"hello","device":"cpu"}')
        assert response.status_code == 415
        assert response.json() == {'detail': 'JSON request bodies require Content-Type: application/json'}


@pytest.mark.parametrize('endpoint', ['/stream/speech', '/stream/wav'])
@pytest.mark.parametrize('failure', ['empty_input', 'load', 'prime', 'busy'])
def test_stream_preparation_errors_preserve_json_status_before_headers(endpoint, failure):
    def factory(device):
        if failure == 'load':
            raise RuntimeError('fixture model load failed')

        def pipeline(text, voice, speed):
            if failure == 'prime':
                raise RuntimeError('fixture first inference failed')
            yield text, voice, np.zeros(1)
        return pipeline

    engine = SynthesisRuntime(factory)
    held = engine.open_session('held', 'af_heart', 1, 'cpu') if failure == 'busy' else None
    try:
        with TestClient(create_app(engine)) as client:
            response = client.post(endpoint, json={'input': '' if failure == 'empty_input' else 'hello', 'device': 'cpu'})
        assert response.status_code == {'empty_input': 400, 'load': 500, 'prime': 500, 'busy': 503}[failure]
        assert response.headers['content-type'] == 'application/json'
        assert isinstance(response.json()['detail'], str)
        if failure == 'busy':
            assert response.headers['retry-after'] == '1'
        else:
            # A preparation failure never leaks the slot; it reaches the same
            # validation/load/inference error on retry instead of becoming busy.
            with TestClient(create_app(engine)) as client:
                retry = client.post(endpoint, json={'input': '' if failure == 'empty_input' else 'hello', 'device': 'cpu'})
            assert retry.status_code == response.status_code
    finally:
        if held is not None:
            held.close()


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


@pytest.mark.parametrize('termination', ['disconnect', 'task_cancel'])
def test_stream_termination_retains_slot_until_active_native_inference_finishes(monkeypatch, termination):
    monkeypatch.setattr(runtime_module, 'cuda_available', lambda: False)
    entered, release, finalized = threading.Event(), threading.Event(), threading.Event()

    def pipeline(text, voice, speed):
        try:
            yield 'first', voice, np.zeros(1)
            entered.set()
            assert release.wait(3)
            yield 'late', voice, np.zeros(1)
        finally:
            finalized.set()

    engine = SynthesisRuntime(lambda device: pipeline)
    session = engine.open_session('hello', 'af_heart', 1, 'cpu')
    response = SpeechStreamResponse(sse_chunks(session), session, media_type='text/event-stream')
    closing_started, closing_finished = threading.Event(), threading.Event()
    close = session.close

    def observed_close():
        closing_started.set()
        try:
            close()
        finally:
            closing_finished.set()

    session.close = observed_close

    async def exercise():
        disconnect_sent = asyncio.Event()

        async def receive():
            # Match ASGI receive: an already available disconnect is returned
            # without an extra scheduling point, so nonblocking polls see it.
            if not entered.is_set():
                assert await asyncio.to_thread(entered.wait, 1)
            disconnect_sent.set()
            return {'type': 'http.disconnect'}

        async def send(message):
            pass

        spec = '2.0' if termination == 'disconnect' else '2.4'
        stream = asyncio.create_task(response({'type': 'http', 'asgi': {'spec_version': spec}}, receive, send))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            if termination == 'task_cancel':
                stream.cancel()
                assert await asyncio.to_thread(closing_started.wait, 1)
                assert not await asyncio.to_thread(closing_finished.wait, 0.02)
            else:
                await asyncio.wait_for(disconnect_sent.wait(), 1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url='http://tts.test') as client:
                assert (await client.get('/health')).json()['loaded'] is True
                with pytest.raises(SynthesisBusy):
                    engine.open_session('blocked', 'af_heart', 1, 'cpu')
                busy = await client.post('/stream/speech', json={'input': 'blocked', 'device': 'cpu'})
                assert busy.status_code == 503
                assert busy.headers['retry-after'] == '1'
            assert not finalized.is_set()
        finally:
            release.set()
            if termination == 'task_cancel':
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(stream, 2)
            else:
                await asyncio.wait_for(stream, 2)
    asyncio.run(exercise())
    assert finalized.is_set()
    engine.open_session('again', 'af_heart', 1, 'cpu').close()


def test_body_send_failure_keeps_slot_until_generator_cleanup_finishes(monkeypatch):
    monkeypatch.setattr(runtime_module, 'cuda_available', lambda: False)
    cleaning, release = threading.Event(), threading.Event()

    def pipeline(text, voice, speed):
        try:
            yield text, voice, np.zeros(1)
        finally:
            cleaning.set()
            assert release.wait(3)

    engine = SynthesisRuntime(lambda device: pipeline)
    session = engine.open_session('hello', 'af_heart', 1, 'cpu')
    response = SpeechStreamResponse(sse_chunks(session), session, media_type='text/event-stream')

    async def exercise():
        body_attempted = False

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        async def broken_send(message):
            nonlocal body_attempted
            if message['type'] == 'http.response.body':
                body_attempted = True
                raise OSError('connection closed while sending audio')

        stream = asyncio.create_task(response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, broken_send))
        try:
            assert await asyncio.to_thread(cleaning.wait, 1)
            assert body_attempted
            with pytest.raises(SynthesisBusy):
                engine.open_session('blocked', 'af_heart', 1, 'cpu')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)), base_url='http://tts.test') as client:
                assert (await client.get('/health')).json()['status'] == 'ok'
        finally:
            release.set()
            with pytest.raises(Exception):
                await asyncio.wait_for(stream, 2)
    asyncio.run(exercise())
    engine.open_session('again', 'af_heart', 1, 'cpu').close()
