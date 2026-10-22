from __future__ import annotations

import json
import httpx

import pytest

from korina.schemas import ChatRequest
from korina.services import response_llm
from korina.services.response_llm import format_voice_reply


def test_format_voice_reply_splits_multi_sentence_single_line():
    text = "First sentence. Second question? Third exclamation! Final ellipsis…"

    assert format_voice_reply(text) == (
        "First sentence.\n"
        "Second question?\n"
        "Third exclamation!\n"
        "Final ellipsis…"
    )


def test_format_voice_reply_preserves_existing_newline_boundaries():
    text = "Line one has two sentences. Split it.\nAlready separate."

    assert format_voice_reply(text) == (
        "Line one has two sentences.\n"
        "Split it.\n"
        "Already separate."
    )


def test_format_voice_reply_empty_input_returns_empty():
    assert format_voice_reply("") == ""
    assert format_voice_reply("   \n  ") == ""


@pytest.mark.parametrize('message', [
    'The current spoken user turn.',
    'Injection: Korina Agent state report for this next reply:\nReady.\n\n'
    'The user interrupted the previous reply.\n\nThe user now says: The current spoken user turn.',
])
def test_response_payload_appends_current_message_once_after_prior_history(monkeypatch, message, mock_provider_http):
    prior = [
        {'role': 'user' if index % 2 == 0 else 'assistant', 'content': f'Prior turn {index}.'}
        for index in range(20)
    ]
    payloads = []

    def request(http_request):
        assert http_request.extensions['timeout']['read'] == 90
        payloads.append(json.loads(http_request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'A reply.'}}]})

    monkeypatch.setattr(response_llm, 'load_config', lambda: {
        'llm_provider': 'openai-compatible', 'llm_base_url': 'http://local-fixture/v1', 'lm_model': 'fixture',
    })
    mock_provider_http(request)

    assert response_llm.response_llm_chat(ChatRequest(message=message, history=prior)) == 'A reply.'
    messages = payloads[0]['messages']
    assert messages[1:-1] == prior[-12:]
    assert messages[-1] == {'role': 'user', 'content': message}
    assert sum('The current spoken user turn.' in turn['content'] for turn in messages) == 1
