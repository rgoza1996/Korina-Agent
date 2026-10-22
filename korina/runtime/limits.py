"""Reject oversized bodies before JSON or multipart parsing allocates them."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RequestSizeLimit:
    """Enforce the actual streamed byte count as well as Content-Length.

    Chunks are replayed only after validation, so endpoints cannot turn a
    partially consumed oversized upload into a different error or side effect.
    """

    def __init__(self, app: ASGIApp, *, json_bytes: int = 2 * 1024 * 1024,
                 audio_bytes: int = 50 * 1024 * 1024):
        self.app = app
        self.json_bytes = json_bytes
        self.audio_bytes = audio_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
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
