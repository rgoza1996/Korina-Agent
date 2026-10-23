"""FastAPI routes for Kokoro SSE PCM, live WAV, and buffered WAV speech."""
from __future__ import annotations

import base64
import json
import os
import threading
from typing import Literal
from functools import partial

from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, Field
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from korina.runtime.limits import RequestSizeLimit
from korina.runtime.json_http import RequestCancelledError
from korina.runtime.request_owner import RequestOwner, check_current
from korina.routes.validation import sanitized_validation_error
from korina.tts.runtime import SAMPLE_RATE, SynthesisBusy, SynthesisRuntime, SynthesisSession, wav_header

VOICES = [
    'af_heart', 'af_bella', 'af_nicole', 'af_sarah', 'af_sky',
    'am_adam', 'am_michael', 'bf_emma', 'bf_george',
]


class SpeechRequest(BaseModel):
    input: str = Field('', validation_alias=AliasChoices('input', 'text'), max_length=8192)
    voice: str = Field('af_heart', min_length=1, max_length=128)
    speed: float = Field(1.0, ge=0.25, le=4.0)
    device: Literal['auto', 'cpu', 'cuda', 'gpu'] | None = None
    model: str | None = Field(None, max_length=512)
    provider: str | None = Field(None, max_length=64)
    response_format: Literal['wav'] = 'wav'


class SpeechStreamResponse(StreamingResponse):
    """Own preparation and playback, with one consumer of ASGI disconnects."""

    def __init__(self, content, session: SynthesisSession | None = None, *, prepare=None, **kwargs):
        self._session = session
        self._prepare = prepare
        self._preparation_lock = threading.Lock()
        self._formatter = content if prepare is not None else None
        super().__init__(() if prepare is not None else content, **kwargs)

    async def __call__(self, scope, receive, send):
        owner = RequestOwner()
        started = False

        def prepare_owned():
            with self._preparation_lock:
                session = self._prepare(is_current=owner.is_current)
                # Store ownership in the worker too: a raw ASGI task cancellation
                # can abandon its await before an active native step returns.
                self._session = session
                try:
                    check_current(owner.is_current)
                except BaseException:
                    session.close()
                    raise
                return session

        def close_owned():
            # A cancelled ASGI task must also drain an abandoned prepare await.
            with self._preparation_lock:
                if self._session is not None:
                    self._session.close()

        async def owned_body(iterator):
            while True:
                check_current(owner.is_current)
                try:
                    chunk = await iterator.__anext__()
                except StopAsyncIteration:
                    return
                check_current(owner.is_current)
                yield chunk

        async def tracked_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)

        async with owner.watch(Request(scope, receive)):
            try:
                if self._prepare is not None:
                    session = await run_in_threadpool(prepare_owned)
                    self.body_iterator = iterate_in_threadpool(self._formatter(session))
                self.body_iterator = owned_body(self.body_iterator)
                check_current(owner.is_current)
                # RequestOwner is the sole receive consumer, across preparation
                # and playback; StreamingResponse's second listener is skipped.
                await self.stream_response(tracked_send)
            except RequestCancelledError as exc:
                if not started:
                    raise HTTPException(499, detail='Speech request was cancelled') from exc
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            finally:
                owner.retire()
                with CancelScope(shield=True):
                    await run_in_threadpool(close_owned)
        if self.background is not None:
            await self.background()


def sse_chunks(session: SynthesisSession):
    for chunk in session.chunks():
        yield 'data: ' + json.dumps({
            'gs': chunk.gs, 'ps': chunk.ps, 'sample_rate': SAMPLE_RATE,
            'device': session.device, 'samples': chunk.samples,
            'audio': base64.b64encode(chunk.pcm).decode('ascii'),
        }) + '\n\n'


def wav_chunks(session: SynthesisSession):
    # One continuous WAV stream: a header once, each new PCM frame once.
    yield wav_header(None)
    for chunk in session.chunks():
        yield chunk.pcm


def create_app(runtime: SynthesisRuntime | None = None) -> FastAPI:
    runtime = runtime or SynthesisRuntime()
    app = FastAPI(title='Kokoro TTS Streaming Server')
    app.add_exception_handler(RequestValidationError, sanitized_validation_error)
    app.state.synthesis = runtime
    app.add_middleware(RequestSizeLimit, json_bytes=2 * 1024 * 1024)
    origins = os.environ.get('KORINA_TTS_ALLOWED_ORIGINS', 'http://127.0.0.1:8001,http://localhost:8001')
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in origins.split(',') if origin.strip()],
        allow_credentials=False,
        allow_methods=['GET', 'POST'],
        allow_headers=['Content-Type'],
    )

    def open_session(request: SpeechRequest, *, is_current=None) -> SynthesisSession:
        if not request.input.strip():
            raise HTTPException(400, detail='No text provided')
        try:
            return runtime.open_session(request.input, request.voice, request.speed, request.device,
                                        is_current=is_current)
        except SynthesisBusy as exc:
            raise HTTPException(503, detail=str(exc), headers={'Retry-After': '1'}) from exc
        except RequestCancelledError:
            raise
        except Exception as exc:
            raise HTTPException(500, detail=str(exc)) from exc

    @app.post('/stream/speech')
    async def stream_speech(request: SpeechRequest):
        return SpeechStreamResponse(
            sse_chunks, prepare=partial(open_session, request), media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'},
        )

    @app.post('/stream/wav')
    async def stream_wav(request: SpeechRequest):
        return SpeechStreamResponse(
            wav_chunks, prepare=partial(open_session, request), media_type='audio/wav',
            headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'},
        )

    def buffered_response(payload: SpeechRequest, owner: RequestOwner):
        session = open_session(payload, is_current=owner.is_current)
        try:
            pcm = b''.join(chunk.pcm for chunk in session.chunks())
            check_current(owner.is_current)
            return Response(wav_header(len(pcm) // 2) + pcm, media_type='audio/wav')
        except RequestCancelledError:
            raise
        except Exception as exc:
            raise HTTPException(500, detail=str(exc)) from exc
        finally:
            session.close()

    @app.post('/v1/audio/speech')
    async def buffered_speech(payload: SpeechRequest, request: Request):
        owner = RequestOwner()
        try:
            async with owner.watch(request):
                response = await run_in_threadpool(buffered_response, payload, owner)
                check_current(owner.is_current)
                return response
        except RequestCancelledError as exc:
            raise HTTPException(499, detail='Speech request was cancelled') from exc

    @app.get('/health')
    def health():
        return runtime.health()

    @app.get('/voices')
    def voices():
        return {'voices': VOICES}

    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run(app, host=os.environ.get('KORINA_TTS_HOST', '127.0.0.1'),
                port=int(os.environ.get('KORINA_TTS_PORT', '8880')), reload=False)
