"""Context trims and queued JSON suffixes preserve the actual model input."""
from __future__ import annotations

import json

import pytest

from korina.schemas import AgentStateRequest, AgentTranscriptRequest
from korina.services.agent_context import (
    MAX_QUEUED_CONTEXT_CHARS, build_agent_context, prepare_queued_injection, previous_with_injections,
)


def test_context_uses_last_configured_turns_then_filters_and_trims():
    req = AgentStateRequest(transcript=[
        {'role': 'user', 'content': 'excluded'}, {'role': 'system', 'content': 'hidden'},
        {'role': 'user', 'content': '  ' + '😀' * 3000 + '  '},
        {'role': 'assistant', 'content': '  answer  '}, {'role': 'tool', 'content': 'tool'},
    ], previous_report='old' + '前' * 5000)
    assert build_agent_context(req, {'agent_max_turns': 4}) == {
        'previous_report': '前' * 4000,
        'recent_transcript': [{'role': 'user', 'content': '😀' * 2000}, {'role': 'assistant', 'content': 'answer'}],
    }
    assert len(req.transcript[2]['content']) > 2000  # The conversation is untouched.


@pytest.mark.parametrize('setting,expected', [(9999, 64), (-4, 1), ('3', 3), ('invalid', 16), (None, 16), (float('inf'), 16)])
def test_legacy_turn_setting_has_a_finite_safe_range(setting, expected):
    req = AgentStateRequest(transcript=[{'role': 'user', 'content': str(index)} for index in range(64)])
    assert len(build_agent_context(req, {'agent_max_turns': setting})['recent_transcript']) == expected


@pytest.mark.parametrize('size', [1, 100, 8192])
def test_queued_suffix_matches_original_json_list_exactly(size):
    records, compact = [], []
    previous = 'old report' * 500
    for index in range(5):
        content = ('😀界"\\\n\t' * 2000)[:size]
        req = AgentTranscriptRequest(transcript=[{'role': 'user', 'content': content}],
                                     reason=f'update {index}', turn_count=index)
        created = 1234.5 + index
        records.append({'transcript': req.transcript, 'reason': req.reason,
                        'turn_count': req.turn_count, 'created_at': created})
        compact.append(prepare_queued_injection(req, created))
        assert len(compact[-1].json_tail) <= MAX_QUEUED_CONTEXT_CHARS
        original = (previous + '\n\nQueued injection while busy:\n' + json.dumps(records, ensure_ascii=False))[-6000:]
        assert previous_with_injections(previous, compact) == original
    assert previous_with_injections(previous, []) == previous[-6000:]


def test_invalid_unicode_is_a_bounded_error_without_echoing_input():
    # HTTP/ordinary constructors reject this already; exercise the builder's
    # defensive guard against an invalid internal instance explicitly.
    req = AgentStateRequest.model_construct(transcript=[{'role': 'user', 'content': 'private-\ud800'}])
    with pytest.raises(ValueError, match='valid Unicode') as caught:
        build_agent_context(req, {})
    assert 'private' not in str(caught.value)
