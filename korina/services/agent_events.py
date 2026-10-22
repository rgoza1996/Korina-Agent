"""UTF-8 JSON budgets for retained events and their HTTP envelope."""
from __future__ import annotations

import json

from starlette.responses import JSONResponse

MAX_AGENT_EVENTS = 100
MAX_AGENT_EVENT_BYTES = 4 * 1024 * 1024
MAX_AGENT_RESPONSE_BYTES = 4 * 1024 * 1024


class AgentEventLimitError(ValueError):
    """A diagnostic cannot fit the finite event protocol budget."""


def agent_json_size(value: object, *, limit: int = MAX_AGENT_EVENT_BYTES) -> int:
    try:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise AgentEventLimitError('Agent diagnostics must contain valid finite UTF-8 JSON') from exc
    if len(body) > limit:
        raise AgentEventLimitError('Agent diagnostics exceed the 4 MiB JSON limit')
    return len(body)


def bounded_events_response(cursor: int, events: list[dict], snapshot: dict) -> JSONResponse:
    """Keep the newest whole events; never alter their context or metadata."""
    payload = {'ok': True, **snapshot, 'events': [], 'last_event_id': cursor,
               'events_truncated': False, 'events_omitted': len(events)}
    envelope_size = agent_json_size(payload, limit=MAX_AGENT_RESPONSE_BYTES)
    selected: list[dict] = []
    used = envelope_size
    for event in reversed(events):
        size = agent_json_size(event, limit=MAX_AGENT_RESPONSE_BYTES)
        required = size + (1 if selected else 0)
        if used + required > MAX_AGENT_RESPONSE_BYTES or len(selected) >= MAX_AGENT_EVENTS:
            if not selected:
                raise AgentEventLimitError('An agent event cannot fit the 4 MiB response envelope')
            break
        selected.append(event)
        used += required
    selected.reverse()
    omitted = len(events) - len(selected)
    payload.update(events=selected, events_truncated=omitted > 0, events_omitted=omitted)
    response = JSONResponse(payload)
    if len(response.body) > MAX_AGENT_RESPONSE_BYTES:
        raise AgentEventLimitError('Agent diagnostics exceed the 4 MiB response limit')
    return response
