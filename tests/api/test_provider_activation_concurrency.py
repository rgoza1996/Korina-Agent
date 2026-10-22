from __future__ import annotations

import asyncio
import threading

import httpx


def test_provider_activation_does_not_block_api_or_overlap(client, monkeypatch):
    from korina.routes import providers

    entered = threading.Event()
    release = threading.Event()

    def activate(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return {'provider': 'openai-compatible'}

    monkeypatch.setattr(providers, 'activate_llm_provider', activate)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://test') as http:
            first = asyncio.create_task(http.post('/api/llm/provider/activate', json={'provider': 'openai-compatible'}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                response = await asyncio.wait_for(http.get('/api/capabilities'), 0.5)
                assert response.status_code == 200
                second = await http.post('/api/llm/provider/activate', json={'provider': 'openai-compatible'})
                assert second.status_code == 409
            finally:
                release.set()
            assert (await first).status_code == 200

    asyncio.run(run())
