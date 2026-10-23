"""Provider-specific credentials and errors survive the shared transport."""
from __future__ import annotations

import json

import httpx
import pytest

from korina.schemas import AgentStateRequest, ChatRequest
from korina.services import active_probe, agent_service, response_llm


def test_anthropic_agent_keeps_native_headers_payload_and_reply(monkeypatch, mock_provider_http):
    monkeypatch.setattr(agent_service, 'load_config', lambda: {
        'agent_provider': 'anthropic', 'agent_base_url': 'https://anthropic.test/v1',
        'agent_api_key': 'native-agent-key', 'agent_model': 'claude-fixture',
    })
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'Priority: normal\nReady.'}]})

    mock_provider_http(respond)
    report = agent_service.generate_agent_state_report(AgentStateRequest(transcript=[]))
    assert report == 'Priority: normal\nReady.'
    assert str(calls[0].url) == 'https://anthropic.test/v1/messages'
    assert calls[0].headers['x-api-key'] == 'native-agent-key'
    assert calls[0].headers['anthropic-version'] == '2023-06-01'
    payload = json.loads(calls[0].content)
    assert payload['model'] == 'claude-fixture'
    assert isinstance(payload['system'], str)
    assert payload['messages'][0]['role'] == 'user'


@pytest.mark.parametrize('kind', ['response', 'agent'])
def test_provider_http_errors_keep_status_and_bounded_actionable_detail(kind, monkeypatch, mock_provider_http):
    mock_provider_http(lambda request: httpx.Response(429, content=b'quota exhausted ' + b'x' * 50000))
    if kind == 'response':
        monkeypatch.setattr(response_llm, 'load_config', lambda: {'llm_base_url': 'https://provider.test/v1'})
        invoke = lambda: response_llm.response_llm_chat(ChatRequest(message='hello'))
        prefix = 'Response LLM HTTP 429: quota exhausted '
    else:
        monkeypatch.setattr(agent_service, 'load_config', lambda: {'agent_base_url': 'https://provider.test/v1'})
        invoke = lambda: agent_service.generate_agent_state_report(AgentStateRequest(transcript=[]))
        prefix = 'Korina Agent '
    with pytest.raises(RuntimeError) as caught:
        invoke()
    assert str(caught.value).startswith(prefix)
    assert 'HTTP 429: quota exhausted ' in str(caught.value)
    assert str(caught.value).endswith(' [truncated]')
    assert len(str(caught.value)) < 17000


def test_audio_rejection_keeps_http_status_for_capability_classification(mock_provider_http):
    mock_provider_http(lambda request: httpx.Response(422, content=b'input_audio is not supported'))
    result = active_probe.probe_audio_support('openai-compatible', 'https://provider.test/v1', 'text-only-fixture')
    assert result.supported is False
    assert result.reason.startswith('body_match:')
    assert result.status == 422
