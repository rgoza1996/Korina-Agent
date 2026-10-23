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

    scope = {'type': 'http', 'method': 'POST', 'path': '/api/chat', 'headers': [
        (b'content-length', str(declared).encode()), (b'content-type', b'application/json'),
    ]}
    asyncio.run(RequestSizeLimit(endpoint, json_bytes=8)(scope, receive, send))
    assert sent[0]['status'] == status
    assert sum(map(len, received)) == (actual if status == 200 else 0)


def test_app_rejects_large_json_before_validation(client):
    response = client.post('/api/chat', content=b'x' * (2 * 1024 * 1024 + 1), headers={'Origin': 'http://localhost:8001'})
    assert response.status_code == 413
    assert response.headers['access-control-allow-origin'] == 'http://localhost:8001'


@pytest.mark.parametrize('content_types', [
    [], ['text/plain'], ['application/x-www-form-urlencoded'],
    ['application/json', 'text/plain'], ['text/plain', 'application/json'],
])
def test_non_json_settings_body_is_rejected_without_changing_disk(client, app_dir, content_types):
    before = client.get('/api/config').json()
    original = (app_dir / 'config.json').read_bytes()
    headers = [('Origin', 'http://untrusted.test')]
    headers.extend(('Content-Type', value) for value in content_types)
    response = client.post('/api/config', content=b'{"speed": 1.75}', headers=headers)
    assert response.status_code == 415
    assert response.json() == {'detail': 'JSON request bodies require Content-Type: application/json'}
    assert (app_dir / 'config.json').read_bytes() == original
    assert client.get('/api/config').json() == before


@pytest.mark.parametrize('content_type', ['application/json', 'application/json; charset=utf-8', 'Application/JSON', 'application/vnd.korina+json'])
def test_explicit_json_settings_media_types_remain_supported(client, content_type):
    response = client.post('/api/config', content=b'{"speed": 1.75}', headers={'Content-Type': content_type})
    assert response.status_code == 200
    assert response.json()['speed'] == 1.75


def test_bodyless_reset_keeps_its_existing_contract(client):
    response = client.post('/api/agent/reset')
    assert response.status_code == 200
    assert response.json()['ok'] is True


@pytest.mark.parametrize('path', ['/api/transcribe', '/api/transcribe/partial', '/api/transcribe/stream'])
def test_multipart_audio_keeps_its_canonical_slash_redirect(client, path):
    response = client.post(path + '/', files={'file': ('capture.wav', b'audio fixture', 'audio/wav')}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'].endswith(path)
