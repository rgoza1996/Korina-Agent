"""Retire synthesis between native steps, without force-cancelling one."""
from __future__ import annotations

import json
import asyncio
import socket
import threading
import time

import anyio
import httpx
import numpy as np
import pytest
import uvicorn
from starlette.responses import JSONResponse

from korina.tts import server as tts
from korina.tts.runtime import SynthesisRuntime


CASES = [(endpoint, stage, retirement)
         for endpoint in ('/v1/audio/speech', '/stream/speech', '/stream/wav')
         for stage in ('load', 'prime', 'later_step')
         for retirement in ('disconnect', 'scope_cancel')]
CASES += [(endpoint, stage, 'task_cancel')
          for endpoint in ('/stream/speech', '/stream/wav') for stage in ('load', 'prime', 'later_step')]


@pytest.mark.parametrize('endpoint,stage,retirement', CASES)
def test_retired_synthesis_finishes_active_work_then_frees_same_slot(monkeypatch, stage, retirement, endpoint):
    entered, release, retired, closed, finished = (threading.Event() for _ in range(5))
    steps = []
    loads = []
    active_receives = max_receives = 0

    def pipeline(text, voice, speed):
        if text == 'fresh':
            steps.append('fresh')
            yield text, voice, np.zeros(1)
            return
        try:
            steps.append('first')
            if stage == 'prime':
                entered.set()
                assert release.wait(5)
            yield 'first', voice, np.zeros(1)
            steps.append('second')
            if stage == 'later_step':
                entered.set()
                assert release.wait(5)
            yield 'second', voice, np.zeros(1)
            steps.append('third')
            yield 'third', voice, np.zeros(1)
        finally:
            closed.set()

    def factory(device):
        loads.append(device)
        if stage == 'load':
            entered.set()
            assert release.wait(5)
        return pipeline

    owner_type = tts.RequestOwner

    class ObservedOwner(owner_type):
        def retire(self):
            super().retire()
            retired.set()

    monkeypatch.setattr(tts, 'RequestOwner', ObservedOwner)
    engine = SynthesisRuntime(factory)
    app = tts.create_app(engine)
    active = {}

    @app.post('/cancel-inflight')
    async def cancel_inflight():
        active['task' if retirement == 'task_cancel' else 'scope'].cancel()
        return {'ok': True}

    async def observed_app(scope, receive, send):
        nonlocal active_receives, max_receives
        if scope['type'] != 'http' or b'old=1' not in scope['query_string']:
            return await app(scope, receive, send)
        started = complete = False

        async def tracked_send(message):
            nonlocal started, complete
            if message['type'] == 'http.response.start':
                started = True
            if message['type'] == 'http.response.body' and not message.get('more_body'):
                complete = True
            await send(message)

        async def tracked_receive():
            nonlocal active_receives, max_receives
            active_receives += 1
            max_receives = max(max_receives, active_receives)
            try:
                return await receive()
            finally:
                active_receives -= 1

        try:
            if retirement == 'disconnect':
                return await app(scope, tracked_receive, tracked_send)
            if retirement == 'task_cancel':
                active['task'] = asyncio.current_task()
                try:
                    await app(scope, tracked_receive, tracked_send)
                except asyncio.CancelledError:
                    cancelled = True
                else:
                    cancelled = False
            else:
                with anyio.CancelScope() as owner:
                    active['scope'] = owner
                    await app(scope, tracked_receive, tracked_send)
                cancelled = owner.cancel_called
            if cancelled:
                if not started:
                    await JSONResponse({'detail': 'test request cancelled'}, status_code=499)(scope, receive, send)
                elif not complete:
                    await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        finally:
            finished.set()

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(observed_app, log_level='error', ws='none'))
    serving = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    browser = None
    try:
        serving.start()
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        browser = socket.create_connection(('127.0.0.1', port), timeout=2)
        payload = json.dumps({'input': 'old', 'device': 'cpu'}).encode()
        browser.sendall(f'POST {endpoint}?old=1 HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: '.encode()
                        + str(len(payload)).encode() + b'\r\n\r\n' + payload)
        assert entered.wait(3), 'loading or a native step must already be active'
        if retirement == 'disconnect':
            browser.close()
            browser = None
        else:
            assert httpx.post(f'http://127.0.0.1:{port}/cancel-inflight', timeout=2).status_code == 200
        assert retired.wait(1), 'HTTP disconnect or outer cancellation must retire the request owner'
        assert not release.is_set()
        assert not closed.is_set()
        assert not finished.is_set(), 'active native work must drain before response cleanup finishes'
        busy = httpx.post(f'http://127.0.0.1:{port}/v1/audio/speech', json={'input': 'fresh', 'device': 'cpu'}, timeout=2)
        assert busy.status_code == 503
        assert busy.headers['retry-after'] == '1'
        release.set()
        assert finished.wait(2), 'retired synthesis must close without admitting another native step'
        assert max_receives == 1, 'body parsing and ownership must never race another disconnect receiver'
        assert steps == {'load': [], 'prime': ['first'], 'later_step': ['first', 'second']}[stage]
        assert closed.is_set() is (stage != 'load')
        fresh = httpx.post(f'http://127.0.0.1:{port}/v1/audio/speech', json={'input': 'fresh', 'device': 'cpu'}, timeout=2)
        assert fresh.status_code == 200
        assert fresh.headers['content-type'] == 'audio/wav'
        assert len(fresh.content) == 46
        assert steps[-1] == 'fresh'
        assert loads == ['cpu'], 'a cancelled model load may finish, but remains safely reusable'
    finally:
        if browser is not None:
            browser.close()
        release.set()
        server.should_exit = True
        serving.join(5)
        listener.close()
        assert not serving.is_alive()
