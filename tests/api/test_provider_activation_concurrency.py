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
                refresh = await http.post('/api/llm/llama/refresh')
                assert refresh.status_code == 409
            finally:
                release.set()
            assert (await first).status_code == 200

    asyncio.run(run())


def test_refresh_rejects_overlapping_activation_and_refresh(client, monkeypatch):
    from korina import config
    from korina.routes import model_roots

    config.update_config_values({'llm_provider': 'llama.cpp', 'lm_model': '/models/demo.gguf'})
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(model_roots, '_resolve_llama_cpp_model_id', lambda model: model)
    monkeypatch.setattr(model_roots, 'discover_local_gguf_models', lambda: [])
    monkeypatch.setattr(model_roots, 'prepare_provider', lambda provider, config, model, **kwargs: (model, {}, []))

    def start(*args, **kwargs):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(model_roots, 'start_llama_server', start)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://test') as http:
            first = asyncio.create_task(http.post('/api/llm/llama/refresh'))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                assert (await http.post('/api/llm/llama/refresh')).status_code == 409
                assert (await http.post('/api/llm/provider/activate', json={'provider': 'openai-compatible'})).status_code == 409
                assert (await asyncio.wait_for(http.get('/api/capabilities'), .5)).status_code == 200
            finally:
                release.set()
            assert (await first).status_code == 200

    asyncio.run(run())
