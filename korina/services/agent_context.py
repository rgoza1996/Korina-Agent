"""Pure preparation of the exact bounded context sent to the agent model."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

from korina.schemas import AgentStateRequest, AgentTranscriptRequest

MAX_AGENT_TURNS = 64
MAX_AGENT_TURN_CHARS = 2000
MAX_AGENT_PREVIOUS_CHARS = 4000
MAX_QUEUED_CONTEXT_CHARS = 6000


class AgentContext(TypedDict):
    previous_report: str
    recent_transcript: list[dict[str, str]]


def _valid_unicode(value: str) -> str:
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise ValueError('Agent context must contain valid Unicode') from exc
    return value


def build_agent_context(req: AgentStateRequest, config: Mapping) -> AgentContext:
    """Keep the existing last-turns, first-content, and previous-tail rules."""
    try:
        count = int(config.get('agent_max_turns') or 16)
    except (ValueError, TypeError, OverflowError):
        count = 16
    count = max(1, min(MAX_AGENT_TURNS, count))
    turns = []
    for turn in req.transcript[-count:]:
        role, content = turn.get('role'), turn.get('content')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            turns.append({'role': role, 'content': _valid_unicode(content.strip()[:MAX_AGENT_TURN_CHARS])})
    return {
        'previous_report': _valid_unicode(req.previous_report[-MAX_AGENT_PREVIOUS_CHARS:]),
        'recent_transcript': turns,
    }


@dataclass(frozen=True)
class QueuedInjection:
    """A serialized suffix, never a retained full transcript request."""
    json_tail: str


def prepare_queued_injection(req: AgentTranscriptRequest, created_at: float) -> QueuedInjection:
    record = {'transcript': req.transcript, 'reason': req.reason,
              'turn_count': req.turn_count, 'created_at': created_at}
    # Each typed input is already bounded. Serialize it once at admission,
    # retaining only what can survive the existing previous-report trim.
    text = json.dumps(record, ensure_ascii=False, allow_nan=False)
    return QueuedInjection(_valid_unicode(text[-MAX_QUEUED_CONTEXT_CHARS:]))


def previous_with_injections(previous: str, injections: Sequence[QueuedInjection]) -> str:
    if not injections:
        return previous[-MAX_QUEUED_CONTEXT_CHARS:]
    # A discarded prefix cannot affect this suffix. This is byte-for-byte the
    # old JSON-list tail, including separators, quotes, escapes, and Unicode.
    tail = ('[' + ', '.join(value.json_tail for value in injections) + ']')[-MAX_QUEUED_CONTEXT_CHARS:]
    return (previous + '\n\nQueued injection while busy:\n' + tail)[-MAX_QUEUED_CONTEXT_CHARS:]
