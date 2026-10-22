"""FastAPI routes for Kokoro SSE PCM, live WAV, and buffered WAV speech."""
from __future__ import annotations

import base64
import json
import os
from typing import Literal

from anyio import CancelScope
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import AliasChoices, BaseModel, Field
from starlette.concurrency import run_in_threadpool

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
    """Release synthesis even when the client disconnects before iteration."""

    def __init__(self, content, session: SynthesisSession, **kwargs):
        self._session = session
        super().__init__(content, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self._session.close)


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
    def stream_speech(request: SpeechRequest):
        session = open_session(request)
        return SpeechStreamResponse(
            sse_chunks(session), session, media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'},
        )

    @app.post('/stream/wav')
    def stream_wav(request: SpeechRequest):
        session = open_session(request)
        return SpeechStreamResponse(
            wav_chunks(session), session, media_type='audio/wav',
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
