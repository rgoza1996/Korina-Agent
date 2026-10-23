"""Chat observations describe one original request, never newer saved settings."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from korina.schemas import ChatRequest
from korina.services import response_llm


def completion(usage=None):
    return {
        'choices': [{'message': {'content': 'Ready. Next sentence.'}, 'finish_reason': 'stop'}],
        'usage': usage,
        'model': 'provider-resolved-model-not-the-request-label',
        'private': 'provider data is not returned',
    }


@pytest.mark.parametrize('usage,expected', [
    (None, None),
    ({}, None),
    ({'prompt_tokens': 100, 'completion_tokens': 20,
      'completion_tokens_details': {'reasoning_tokens': 15},
      'prompt_tokens_details': {'cached_tokens': 0}},
     {'prompt_tokens': 100, 'completion_tokens': 20, 'reasoning_tokens': 15, 'cached_tokens': 0}),
    ({'prompt_tokens_details': {'cached_tokens': 7}},
     {'prompt_tokens': None, 'completion_tokens': None, 'reasoning_tokens': None, 'cached_tokens': 7}),
    ({'prompt_tokens': 2, 'completion_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 3}}, None),
    ({'completion_tokens': True}, None),
    ({'prompt_tokens': 100, 'completion_tokens_details': []}, None),
])
def test_usage_is_additive_allowlisted_and_never_invalidates_valid_reply(client, mock_provider_http, usage, expected):
    mock_provider_http(lambda request: httpx.Response(200, json=completion(usage)))
    response = client.post('/api/chat', json={'message': 'Hello', 'model': 'requested-model'})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'reply', 'seconds', 'model', 'usage', 'request_settings'}
    assert body['reply'] == 'Ready.\nNext sentence.'
    assert body['model'] == body['request_settings']['model'] == 'requested-model'
    assert isinstance(body['seconds'], (int, float)) and body['seconds'] >= 0
    assert body['usage'] == expected
    assert set(body['request_settings']) == {'provider', 'model', 'max_tokens', 'reasoning_effort', 'reasoning'}
    assert 'private' not in response.text and 'provider-resolved-model' not in response.text


@pytest.mark.parametrize('provider,reasoning,effort,chat_fields,expected_reasoning,expected_effort', [
    ('meta', 'on', 'low', {}, None, 'low'),
    ('meta', 'on', 'low', {'reasoning_effort': ''}, None, ''),
    ('meta', 'off', '', {}, None, ''),
    ('meta', 'off', 'low', {'reasoning_effort': 'minimal'}, None, 'minimal'),
    ('openai-compatible', 'auto', 'high', {}, 'auto', None),
    ('openai-compatible', 'off', '', {'reasoning': 'CuStOm'}, 'custom', None),
    ('llama.cpp', 'off', 'high', {'reasoning': 'ON'}, 'on', None),
])
def test_request_settings_describe_exact_effective_payload(
    client, config_module, mock_provider_http,
    provider, reasoning, effort, chat_fields, expected_reasoning, expected_effort,
):
    config_module.update_config_values({
        'llm_provider': provider, 'lm_model': 'saved-model', 'llm_max_tokens': 2048,
        'llm_reasoning': reasoning, 'llm_reasoning_effort': effort,
    })
    payloads = []

    def reply(upstream):
        payloads.append(json.loads(upstream.content))
        return httpx.Response(200, json=completion())

    mock_provider_http(reply)
    response = client.post('/api/chat', json={'message': 'Hello', **chat_fields})
    assert response.status_code == 200, response.text
    snapshot = response.json()['request_settings']
    assert snapshot == {
        'provider': provider, 'model': 'saved-model', 'max_tokens': 2048,
        'reasoning': expected_reasoning, 'reasoning_effort': expected_effort,
    }
    assert payloads[0]['model'] == snapshot['model']
    assert payloads[0]['max_tokens'] == snapshot['max_tokens']
    assert payloads[0].get('reasoning') == snapshot['reasoning']
    assert payloads[0].get('reasoning_effort', '' if provider == 'meta' else None) == snapshot['reasoning_effort']


def test_original_settings_survive_config_save_while_provider_reply_is_pending(client, mock_provider_http):
    assert client.post('/api/config', json={
        'llm_provider': 'meta', 'lm_model': 'original-model',
        'llm_max_tokens': 2048, 'llm_reasoning_effort': 'low',
    }).status_code == 200
    entered, release = threading.Event(), threading.Event()
    payloads = []

    def reply(upstream):
        payloads.append(json.loads(upstream.content))
        if len(payloads) == 1:
            entered.set()
            assert release.wait(3), 'test must release original provider reply'
        return httpx.Response(200, json=completion({'completion_tokens': 12}))

    mock_provider_http(reply)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/chat', json={'message': 'Original request'})
        try:
            assert entered.wait(3)
            saved = client.post('/api/config', json={
                'llm_provider': 'openai-compatible', 'llm_base_url': 'https://fixture.invalid/v1',
                'lm_model': 'new-model', 'llm_max_tokens': 32, 'llm_reasoning': 'on',
                'llm_reasoning_effort': 'high',
            })
            assert saved.status_code == 200
        finally:
            release.set()
        old = pending.result(timeout=3)
    assert old.status_code == 200, old.text
    assert old.json()['model'] == 'original-model'
    assert old.json()['request_settings'] == {
        'provider': 'meta', 'model': 'original-model', 'max_tokens': 2048,
        'reasoning_effort': 'low', 'reasoning': None,
    }
    fresh = client.post('/api/chat', json={'message': 'Fresh request', 'model': 'explicit-model', 'max_tokens': 16})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()['request_settings'] == {
        'provider': 'openai-compatible', 'model': 'explicit-model', 'max_tokens': 16,
        'reasoning_effort': None, 'reasoning': 'on',
    }
    assert payloads[0]['model'] == 'original-model' and payloads[1]['model'] == 'explicit-model'


@pytest.mark.parametrize('usage', [None, {'completion_tokens': 0}, {'completion_tokens': False}])
def test_public_response_service_still_returns_only_string(monkeypatch, mock_provider_http, usage):
    monkeypatch.setattr(response_llm, 'load_config', lambda: {'llm_base_url': 'https://fixture.invalid/v1'})
    mock_provider_http(lambda request: httpx.Response(200, json=completion(usage)))
    assert response_llm.response_llm_chat(ChatRequest(message='Hello')) == 'Ready.\nNext sentence.'
