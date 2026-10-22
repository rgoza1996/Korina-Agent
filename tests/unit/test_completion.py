"""Provider success bodies must remain safe for subsequent conversation turns."""
from __future__ import annotations

import httpx
import pytest

from korina.runtime.completion import (
    InvalidCompletionError,
    anthropic_text_completion,
    completion_text,
    openai_text_completion,
)
from korina.schemas import AgentStateRequest, ChatRequest, MAX_TEXT_LENGTH
from korina.services import agent_service, multimodal_stt, response_llm


def body(content, reason='stop'):
    return {'choices': [{'message': {'content': content}, 'finish_reason': reason}]}


def test_text_limit_counts_unicode_characters_and_fits_history():
    text = '🦊' * MAX_TEXT_LENGTH
    assert openai_text_completion(body(text)).text == text
    assert ChatRequest(message='next', history=[{'role': 'assistant', 'content': text}])
    with pytest.raises(InvalidCompletionError, match='8192 characters'):
        completion_text(text + 'x')
    with pytest.raises(InvalidCompletionError, match='invalid Unicode'):
        completion_text('provider-\ud800')


@pytest.mark.parametrize('payload', [
    [], {'choices': []}, {'choices': {}}, {'choices': [None]},
    {'choices': [{'message': []}]}, {'choices': [{'message': {}}]},
    body(False), body(['unrequested-block']), body('text', False),
])
def test_malformed_completion_diagnostics_never_dump_provider_fields(payload):
    if isinstance(payload, dict):
        payload['private'] = 'private-provider-marker' + 'x' * 100_000
    with pytest.raises(InvalidCompletionError) as caught:
        openai_text_completion(payload)
    assert len(str(caught.value)) < 128
    assert 'private-provider-marker' not in str(caught.value)


def test_null_and_empty_content_preserve_silent_completion_reason():
    for value in (None, ''):
        parsed = openai_text_completion(body(value, 'length'))
        assert parsed.text == ''
        assert parsed.finish_reason == 'length'
    assert openai_text_completion(body(' words ', None)).text == 'words'
    assert openai_text_completion(body('', None)).finish_reason == ''


def test_anthropic_visible_blocks_exclude_thinking_and_tools():
    assert anthropic_text_completion({'content': [
        {'type': 'thinking', 'thinking': 'private reasoning', 'text': 'not visible'},
        {'type': 'text', 'text': 'Priority: normal\n'},
        {'type': 'tool_use', 'name': 'fixture', 'input': {'secret': 'not visible'}},
        {'type': 'text', 'text': 'Ready.'},
    ]}) == 'Priority: normal\nReady.'
    with pytest.raises(InvalidCompletionError, match='8192 characters'):
        anthropic_text_completion({'content': [{'type': 'text', 'text': 'x' * 4097}] * 2})
    with pytest.raises(InvalidCompletionError, match='must contain text'):
        anthropic_text_completion({'content': [{'type': 'text', 'text': False}]})
    with pytest.raises(InvalidCompletionError, match='invalid Unicode'):
        anthropic_text_completion({'content': [{'type': 'text', 'text': '\udfff'}]})


@pytest.mark.parametrize('role', ['response', 'agent', 'stt'])
def test_services_reject_oversized_text_and_recover_with_next_valid_reply(
    role, monkeypatch, tmp_path, mock_provider_http,
):
    config = {
        'llm_base_url': 'https://fixture.test/v1',
        'agent_base_url': 'https://fixture.test/v1',
        'stt_llm_base_url': 'https://fixture.test/v1', 'stt_llm_model': 'fixture',
    }
    for module in (response_llm, agent_service, multimodal_stt):
        monkeypatch.setattr(module, 'load_config', lambda: config)
    replies = iter([body('x' * (MAX_TEXT_LENGTH + 1)), body('Recovered.')])
    mock_provider_http(lambda request: httpx.Response(200, json=next(replies)))
    wav = tmp_path / 'fixture.wav'
    wav.write_bytes(b'fixture bytes; audio inference is outside this contract')

    def invoke():
        if role == 'response':
            return response_llm.response_llm_chat(ChatRequest(message='hello'))
        if role == 'agent':
            return agent_service.generate_agent_state_report(AgentStateRequest())
        return multimodal_stt.lmstudio_transcribe_wav(wav, model='fixture')['text']

    with pytest.raises(InvalidCompletionError, match='8192 characters'):
        invoke()
    recovered = invoke()
    assert recovered == 'Recovered.'
    assert ChatRequest(message='next', history=[{'role': 'assistant', 'content': recovered}])


def test_final_voice_formatting_still_fits_conversation_schema(monkeypatch, mock_provider_http):
    monkeypatch.setattr(response_llm, 'load_config', lambda: {'llm_base_url': 'https://fixture.test/v1'})
    # Sentence splitting must retain a boundary-sized reply that fits history.
    text = 'a.\t' * 2730 + 'xx'
    assert len(text) == MAX_TEXT_LENGTH
    mock_provider_http(lambda request: httpx.Response(200, json=body(text)))
    reply = response_llm.response_llm_chat(ChatRequest(message='hello'))
    assert len(reply) <= MAX_TEXT_LENGTH
    assert ChatRequest(message='next', history=[{'role': 'assistant', 'content': reply}])
