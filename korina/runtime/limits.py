"""Resolve audio budgets and reject oversized bodies before parsing."""

import math
import os
from typing import TypedDict

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_JSON_BODY_BYTES = 2 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_AUDIO_SECONDS = 300
MAX_AUDIO_SECONDS = 1800


class AudioLimits(TypedDict):
    max_duration_seconds: float
    max_upload_bytes: int


def bounded_seconds(name: str, default: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be between 1 and {maximum:g} seconds') from exc
    if not math.isfinite(value) or not 1 <= value <= maximum:
        raise ValueError(f'{name} must be between 1 and {maximum:g} seconds')
    return value


def resolve_audio_limits(*, max_upload_bytes: int = MAX_AUDIO_UPLOAD_BYTES) -> AudioLimits:
    """Shared decoded duration and effective multipart request budget."""
    return {
        'max_duration_seconds': bounded_seconds('KORINA_MAX_AUDIO_SECONDS', DEFAULT_MAX_AUDIO_SECONDS, MAX_AUDIO_SECONDS),
        'max_upload_bytes': max_upload_bytes,
    }


class RequestSizeLimit:
    """Enforce the actual streamed byte count as well as Content-Length.

    Chunks are replayed only after validation, so endpoints cannot turn a
    partially consumed oversized upload into a different error or side effect.
    """

    def __init__(self, app: ASGIApp, *, json_bytes: int = MAX_JSON_BODY_BYTES,
                 audio_bytes: int = MAX_AUDIO_UPLOAD_BYTES):
        self.app = app
        self.json_bytes = json_bytes
        self.audio_bytes = audio_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] == 'http':
            # Health must describe this ASGI stack's actual budget, including
            # deployments with a tighter middleware override.
            scope['korina.audio_max_upload_bytes'] = min(
                scope.get('korina.audio_max_upload_bytes', self.audio_bytes), self.audio_bytes,
            )
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)
        limit = self.audio_bytes if scope['path'].startswith('/api/transcribe') else self.json_bytes
        headers = dict(scope.get('headers', []))
        try:
            length = int(headers.get(b'content-length', b'0'))
            if length < 0:
                raise ValueError
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, status_code=400)(scope, receive, send)
        if length > limit:
            return await self._reject(scope, receive, send, limit)

        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > limit:
                return await self._reject(scope, receive, send, limit)
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        offset = 0
        complete = False

        async def replay():
            nonlocal offset, complete
            if complete:
                return await receive()
            chunk = bytes(body[offset:offset + 65536])
            offset += len(chunk)
            complete = offset >= len(body)
            return {'type': 'http.request', 'body': chunk, 'more_body': not complete}

        await self.app(scope, replay, send)

    async def _reject(self, scope, receive, send, limit):
        response = JSONResponse({'detail': f'Request body exceeds {limit} bytes'}, status_code=413)
        await response(scope, receive, send)
