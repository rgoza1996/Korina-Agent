"""A response budget is deliberate, persisted, and distinct from STT silence."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize('value', [0, 8193, -1, True, '512', 1.5, None])
def test_invalid_response_budget_never_changes_saved_settings(client, app_dir, value):
    client.get('/api/config')
    before = (app_dir / 'config.json').read_bytes()
    response = client.post('/api/config', json={'llm_max_tokens': value, 'voice': 'af_bella'})
    assert response.status_code == 422
    assert any(error['loc'][:2] == ['body', 'llm_max_tokens'] for error in response.json()['detail'])
    assert (app_dir / 'config.json').read_bytes() == before


@pytest.mark.parametrize('value', [1, 2048, 8192])
def test_response_budget_persists_at_supported_limits(client, value):
    response = client.post('/api/config', json={'llm_max_tokens': value})
    assert response.status_code == 200
    assert response.json()['llm_max_tokens'] == value
    assert client.get('/api/config').json()['llm_max_tokens'] == value
    assert client.post('/api/config', json={'voice': 'af_bella'}).json()['llm_max_tokens'] == value


def test_response_budget_default_is_180_with_or_without_shipped_example(config_module, app_dir):
    example = Path(__file__).resolve().parents[2] / 'Korina/config/config.example.json'
    assert json.loads(example.read_text())['llm_max_tokens'] == 180
    assert config_module.load_config()['llm_max_tokens'] == 180
    (app_dir / 'config.json').unlink()
    (app_dir / 'config/config.example.json').unlink()
    assert config_module.load_config()['llm_max_tokens'] == 180
    assert config_module.load_config()['agent_max_tokens'] == 512


@pytest.mark.parametrize('saved,request_budget,expected', [
    (None, None, 180), (2048, None, 2048), (2048, 32, 32), (1, None, 1), (8192, None, 8192),
])
def test_chat_uses_saved_default_but_preserves_explicit_request_budget(
    client, config_module, mock_provider_http, saved, request_budget, expected,
):
    settings = {'llm_provider': 'meta', 'lm_model': 'team-fixture'}
    if saved is not None:
        settings['llm_max_tokens'] = saved
    config_module.update_config_values(settings)
    payloads = []

    def reply(request):
        assert str(request.url) == 'https://api.meta.ai/v1/chat/completions'
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ready.'}, 'finish_reason': 'stop'}]})

    mock_provider_http(reply)
    request = {'message': 'Hello'}
    if request_budget is not None:
        request['max_tokens'] = request_budget
    response = client.post('/api/chat', json=request)
    assert response.status_code == 200
    assert payloads[0]['max_tokens'] == expected
    assert 'reasoning' not in payloads[0]
    assert 'reasoning_effort' not in payloads[0]


@pytest.mark.parametrize('content', [None, '', '   '])
def test_budget_exhaustion_returns_an_actionable_error_before_any_blank_reply(
    client, config_module, mock_provider_http, content,
):
    config_module.update_config_values({'llm_provider': 'meta', 'lm_model': 'team-fixture'})
    mock_provider_http(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': content}, 'finish_reason': 'length'}],
        'usage': {'completion_tokens': 180, 'completion_tokens_details': {'reasoning_tokens': 180}},
    }))
    response = client.post('/api/chat', json={'message': 'Hello'})
    assert response.status_code == 500
    assert 'output-token budget' in response.json()['detail']
    assert 'Response token budget in Settings' in response.json()['detail']
    assert 'reply' not in response.json()


@pytest.mark.parametrize('content,reason,expected', [
    ('A partial but visible answer.', 'length', 'A partial but visible answer.'),
    (None, 'stop', ''),
])
def test_nonempty_truncation_and_other_empty_completion_contracts_stay_compatible(
    client, mock_provider_http, content, reason, expected,
):
    mock_provider_http(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': content}, 'finish_reason': reason}],
    }))
    response = client.post('/api/chat', json={'message': 'Hello'})
    assert response.status_code == 200
    assert response.json()['reply'] == expected


@pytest.mark.parametrize('legacy', [0, 8193, True, '2048', None])
def test_invalid_old_file_budget_uses_the_existing_safe_default(config_module, legacy):
    assert config_module.config_llm_max_tokens({'llm_max_tokens': legacy}) == 180
