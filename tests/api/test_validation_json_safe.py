"""Invalid input must return useful JSON422 without leaking raw inputs."""
from __future__ import annotations

import json

import anyio
import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from korina.routes.validation import sanitized_validation_error
from korina.schemas import ChatRequest
from korina.tts.runtime import SynthesisRuntime
from korina.tts.server import create_app as create_tts_app


def assert_safe_error(response):
    assert response.status_code == 422, response.text
    response.content.decode('utf-8', errors='strict')
    detail = response.json()['detail']
    assert detail
    for error in detail:
        assert set(error) == {'type', 'loc', 'msg'}
        for value in [error['type'], error['msg'], *error['loc']]:
            if isinstance(value, str):
                value.encode('utf-8', errors='strict')


@pytest.mark.parametrize('endpoint,body', [
    ('/api/chat', b'{"message":NaN}'),
    ('/api/chat', b'{"message":Infinity}'),
    ('/api/chat', b'{"message":"hello","temperature":NaN}'),
    ('/api/chat', b'{"message":"hello","history":[{"\\ud800":2}]}'),
    ('/api/agent/transcript', b'{"transcript":[{"\\ud800":NaN}]}'),
    ('/api/agent/models', b'{"agent_api_key":NaN}'),
])
def test_main_factory_returns_safe_validation_for_default_and_protected_routes(client, endpoint, body):
    assert_safe_error(client.post(endpoint, content=body, headers={'Content-Type': 'application/json'}))


@pytest.mark.parametrize('endpoint,field', [
    ('/api/chat', 'history'),
    ('/api/agent/transcript', 'transcript'),
    ('/api/agent/permission-answer', 'transcript'),
    ('/api/agent/state-report', 'transcript'),
])
@pytest.mark.parametrize('invalid', ['\ud800', '\udfff'])
def test_invalid_nested_turn_text_is_rejected_before_chat_or_agent_admission(client, monkeypatch, endpoint, field, invalid):
    from korina.routes import agent, chat

    def forbidden(*args, **kwargs):
        pytest.fail('malformed conversation reached provider or adapter admission')

    monkeypatch.setattr(chat, 'response_llm_chat', forbidden)
    monkeypatch.setattr(agent, 'agent_gateway', forbidden)
    monkeypatch.setattr(agent, 'generate_agent_state_report', forbidden)
    body = {'message': 'valid current turn', field: [{'role': 'user', 'content': invalid}]}
    response = client.post(endpoint, content=json.dumps(body), headers={'Content-Type': 'application/json'})
    assert_safe_error(response)
    assert any('valid Unicode' in error['msg'] for error in response.json()['detail'])


def test_nested_turn_text_preserves_multilingual_characters_and_valid_json_surrogate_pairs():
    text = 'Café, नमस्ते, שלום, 🚀'
    request = ChatRequest.model_validate_json(json.dumps({
        'message': 'fresh turn', 'history': [{'role': 'user', 'content': text}],
    }))
    assert request.history == [{'role': 'user', 'content': text}]


@pytest.mark.parametrize('endpoint', ['/v1/audio/speech', '/stream/speech', '/stream/wav'])
@pytest.mark.parametrize('body', [b'{"input":NaN}', b'{"input":"valid","speed":Infinity}', b'{"input":"\\ud800"}'])
def test_tts_factory_returns_safe_validation_before_loading_models(endpoint, body):
    engine = SynthesisRuntime(lambda device: pytest.fail('invalid input reached model loading'))
    with TestClient(create_tts_app(engine)) as client:
        assert_safe_error(client.post(endpoint, content=body, headers={'Content-Type': 'application/json'}))


def test_shared_formatter_sanitizes_surrogate_property_locations_types_and_messages():
    error = RequestValidationError([{
        'type': 'invalid\ud801', 'loc': ('body', 'unknown\ud800', 0),
        'msg': 'Bad field\udfff', 'input': float('nan'),
    }])
    response = anyio.run(sanitized_validation_error, Request({'type': 'http', 'headers': []}), error)
    detail = json.loads(response.body)['detail'][0]
    assert detail == {'type': 'invalid\\ud801', 'loc': ['body', 'unknown\\ud800', 0], 'msg': 'Bad field\\udfff'}
    assert 'input' not in detail
