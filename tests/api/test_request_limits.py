from __future__ import annotations

import asyncio

import pytest
from starlette.responses import JSONResponse

from korina.runtime.limits import RequestSizeLimit


@pytest.mark.parametrize('declared,actual,status', [(999, 0, 413), (0, 9, 413), (2, 9, 413), (8, 8, 200)])
def test_declared_and_streamed_body_limits(declared, actual, status):
    received = []

    async def endpoint(scope, receive, send):
        while True:
            message = await receive()
            received.append(message.get('body', b''))
            if not message.get('more_body'):
                break
        await JSONResponse({'ok': True})(scope, receive, send)

    messages = iter([
        {'type': 'http.request', 'body': b'x' * (actual // 2), 'more_body': True},
        {'type': 'http.request', 'body': b'x' * (actual - actual // 2)},
    ])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'method': 'POST', 'path': '/api/chat', 'headers': [(b'content-length', str(declared).encode())]}
    asyncio.run(RequestSizeLimit(endpoint, json_bytes=8)(scope, receive, send))
    assert sent[0]['status'] == status
    assert sum(map(len, received)) == (actual if status == 200 else 0)


def test_app_rejects_large_json_before_validation(client):
    response = client.post('/api/chat', content=b'x' * (2 * 1024 * 1024 + 1), headers={'Origin': 'http://localhost:8001'})
    assert response.status_code == 413
    assert response.headers['access-control-allow-origin'] == 'http://localhost:8001'
