"""One discovery request reuses only identical provider inputs and local scans."""
from __future__ import annotations

import httpx
import pytest

from korina.model_ids import ModelIds
from korina.routes import models
from korina.services import model_catalog


@pytest.fixture
def discovery(monkeypatch, config_module, model_root):
    selected = str(model_root / 'selected.gguf')
    (model_root / 'selected.gguf').touch()
    config_module.update_config_values({
        'llm_provider': 'llama.cpp', 'lm_model': selected, 'llm_api_key_env': '',
        'stt_llm_provider': '', 'stt_llm_base_url': '', 'stt_llm_model': '',
        'stt_llm_api_key_env': '',
    })
    scans = []

    def local_models(**kwargs):
        scans.append(kwargs)
        return ModelIds([selected], info={'visited_entries': 7})

    monkeypatch.setattr(model_catalog, 'discover_local_gguf_models', local_models)
    monkeypatch.setattr(models, 'discover_local_gguf_models', local_models)
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'llm_models_for', model_catalog.llm_models_for)
    return selected, scans


@pytest.mark.parametrize('params', [{}, {'stt_llm_base_url': 'http://127.0.0.1:8080/v1/chat/completions/'}])
def test_identical_roles_use_one_provider_attempt_and_local_walk_per_request(
    client, discovery, mock_provider_http, params,
):
    selected, scans = discovery
    calls = []

    def provider(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'data': [{'id': 'endpoint-model'}]})

    mock_provider_http(provider)
    for request_number in (1, 2):
        response = client.get('/api/models', params=params)
        assert response.status_code == 200
        body = response.json()
        assert len(calls) == request_number, 'sharing is scoped to this request, including chat URL aliases'
        assert len(scans) == request_number, 'provider fallback and display reuse one local snapshot'
        assert body['llm_models'] == body['stt_llm_models'] == ['endpoint-model', selected]
        assert body['llama_cpp_local_models'] == [selected]
        assert body['llm_error'] is body['stt_llm_error'] is None
        assert body['catalog_info']['local_gguf']['visited_entries'] == 7


@pytest.mark.parametrize('difference', ['environment', 'pin', 'endpoint'])
def test_different_provider_inputs_stay_independent(
    client, discovery, config_module, monkeypatch, mock_provider_http, difference,
):
    selected, scans = discovery
    other = 'model-0599'
    updates = {}
    if difference == 'environment':
        # Names remain distinct even if both currently contain the same value.
        updates = {'llm_api_key_env': 'REUSE_RESPONSE', 'stt_llm_api_key_env': 'REUSE_STT'}
        monkeypatch.setenv('REUSE_RESPONSE', 'fixture-token')
        monkeypatch.setenv('REUSE_STT', 'fixture-token')
    elif difference == 'pin':
        updates = {'stt_llm_model': other}
    else:
        updates = {'stt_llm_base_url': 'https://separate.fixture/v1'}
    config_module.update_config_values(updates)
    calls = []

    def provider(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'data': [{'id': f'model-{i:04d}'} for i in range(600)]})

    mock_provider_http(provider)
    body = client.get('/api/models').json()
    assert len(calls) == 2
    assert len(scans) == 1
    assert selected in body['llm_models']
    assert len(body['llm_models']) <= 512 and len(body['stt_llm_models']) <= 512
    if difference == 'pin':
        assert other not in body['llm_models'] and other in body['stt_llm_models']
    if difference == 'endpoint':
        assert calls == ['http://127.0.0.1:8080/v1/models', 'https://separate.fixture/v1/models']


@pytest.mark.parametrize('params', [{}, {'stt_llm_base_url': 'http://127.0.0.1:8080/v1/chat/completions/'}])
def test_shared_failure_keeps_local_fallback_and_retries_on_next_request(client, discovery, mock_provider_http, params):
    selected, scans = discovery
    calls = []

    def provider(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(503, text='fixture unavailable')
        return httpx.Response(200, json={'data': [{'id': 'recovered'}]})

    mock_provider_http(provider)
    first = client.get('/api/models', params=params).json()
    assert len(calls) == len(scans) == 1
    assert first['llm_error'] == first['stt_llm_error']
    assert '503' in first['llm_error']
    assert first['llm_models'] == first['stt_llm_models'] == [selected]
    second = client.get('/api/models', params=params).json()
    assert len(calls) == len(scans) == 2
    assert second['llm_error'] is second['stt_llm_error'] is None
    assert second['llm_models'] == second['stt_llm_models'] == ['recovered', selected]


def test_same_endpoint_with_distinct_credentials_keeps_errors_independent(
    client, discovery, config_module, monkeypatch, mock_provider_http,
):
    selected, scans = discovery
    config_module.update_config_values({'llm_api_key_env': 'REUSE_RESPONSE', 'stt_llm_api_key_env': 'REUSE_STT'})
    monkeypatch.setenv('REUSE_RESPONSE', 'response-fixture')
    monkeypatch.setenv('REUSE_STT', 'audio-fixture')
    calls = []

    def provider(request):
        auth = request.headers.get('Authorization')
        calls.append(auth)
        if auth == 'Bearer response-fixture':
            return httpx.Response(503, text='response fixture unavailable')
        return httpx.Response(200, json={'data': [{'id': 'audio-only'}]})

    mock_provider_http(provider)
    body = client.get('/api/models').json()
    assert calls == ['Bearer response-fixture', 'Bearer audio-fixture']
    assert len(scans) == 1
    assert '503' in body['llm_error'] and body['stt_llm_error'] is None
    assert body['llm_models'] == [selected]
    assert body['stt_llm_models'] == ['audio-only', selected]


def test_explicit_empty_local_snapshot_does_not_trigger_an_implicit_walk(monkeypatch, mock_provider_http):
    monkeypatch.setattr(model_catalog, 'discover_local_gguf_models',
                        lambda **kwargs: pytest.fail('a supplied empty snapshot is authoritative'))
    mock_provider_http(lambda request: httpx.Response(200, json={'data': [{'id': 'endpoint-only'}]}))
    assert model_catalog.llm_models_for('http://127.0.0.1:8080/v1', '', local_models=[]) == ['endpoint-only']
