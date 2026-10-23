"""Optional Meta effort is explicit, independently saved, and provider-scoped."""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from korina.agents.state import agent_state
from korina.agents import gateway
from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.schemas import AgentStateRequest
from korina.services import agent_service, multimodal_stt
from korina.services.provider_manager import provider_activation_lock

EFFORTS = ['', 'minimal', 'low', 'medium', 'high', 'xhigh']


@pytest.fixture(autouse=True)
def no_real_meta_key(monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY', raising=False)


@pytest.fixture
def payloads(mock_provider_http):
    calls = []

    def reply(request):
        calls.append(json.loads(request.content))
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'content': [{'type': 'text', 'text': 'Ready.'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ready.'}, 'finish_reason': 'stop'}]})

    mock_provider_http(reply)
    return calls


@pytest.mark.parametrize('field', ['llm_reasoning_effort', 'agent_reasoning_effort'])
@pytest.mark.parametrize('value', EFFORTS)
def test_effort_persists_without_changing_the_other_role(client, field, value):
    opposite = 'agent_reasoning_effort' if field.startswith('llm_') else 'llm_reasoning_effort'
    assert client.post('/api/config', json={opposite: 'medium'}).status_code == 200
    response = client.post('/api/config', json={field: value})
    assert response.status_code == 200
    assert response.json()[field] == value
    assert response.json()[opposite] == 'medium'
    assert client.get('/api/config').json()[field] == value
    assert client.post('/api/config', json={'voice': 'af_bella'}).json()[field] == value


@pytest.mark.parametrize('field', ['llm_reasoning_effort', 'agent_reasoning_effort'])
@pytest.mark.parametrize('value', ['none', 'max', 'auto', 'LOW', ' low ', None, True, 1, [], {}])
def test_invalid_effort_rejects_the_entire_settings_write(client, config_module, field, value):
    before = config_module.CONFIG_PATH.read_bytes()
    response = client.post('/api/config', json={field: value, 'voice': 'af_bella'})
    assert response.status_code == 422
    assert any(error['loc'][:2] == ['body', field] for error in response.json()['detail'])
    assert config_module.CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize('value,expected', [
    (None, ''), (False, ''), (12, ''), ([], ''), ({}, ''), ('none', ''), ('max', ''),
    ('arbitrary', ''), (' HIGH ', 'high'), ('xhigh', 'xhigh'), ('', ''),
])
def test_historical_effort_has_a_safe_read_only_fallback(config_module, value, expected):
    saved = config_module.load_config()
    saved.update(llm_reasoning_effort=value, agent_reasoning_effort=value)
    config_module.CONFIG_PATH.write_text(json.dumps(saved))
    before = config_module.CONFIG_PATH.read_bytes()
    assert config_module.config_llm_reasoning_effort() == expected
    assert config_module.config_agent_reasoning_effort() == expected
    assert config_module.CONFIG_PATH.read_bytes() == before


def test_effort_defaults_remain_blank_and_edits_do_not_activate_or_reset(client, config_module):
    assert config_module.load_config()['llm_reasoning_effort'] == ''
    assert config_module.load_config()['agent_reasoning_effort'] == ''
    generation = agent_state.generation
    # An inference parameter is safe to save during a provider lifecycle
    # operation; it must not trigger that operation or retire active context.
    assert provider_activation_lock.acquire(blocking=False)
    try:
        response = client.post('/api/config', json={'llm_reasoning_effort': 'minimal', 'agent_reasoning_effort': 'high'})
        assert response.status_code == 200
        assert agent_state.generation == generation
        assert not provider_activation_lock.acquire(blocking=False)
    finally:
        provider_activation_lock.release()


@pytest.mark.parametrize('saved,override,expected', [
    ('', None, None), ('minimal', None, 'minimal'), ('high', '', None), ('high', 'low', 'low'),
    ('xhigh', None, 'xhigh'), ('minimal', 'medium', 'medium'),
])
def test_meta_response_omission_inherits_and_explicit_blank_uses_model_default(
    client, config_module, payloads, saved, override, expected,
):
    config_module.update_config_values({'llm_provider': 'meta', 'llm_reasoning_effort': saved,
                                        'agent_reasoning_effort': 'high', 'lm_model': 'fixture-model'})
    request = {'message': 'Remember my appointment.', 'reasoning': 'on'}
    if override is not None:
        request['reasoning_effort'] = override
    response = client.post('/api/chat', json=request)
    assert response.status_code == 200
    assert payloads[0].get('reasoning_effort') == expected
    assert ('reasoning_effort' in payloads[0]) is (expected is not None)
    assert 'reasoning' not in payloads[0]
    assert config_module.load_config()['llm_reasoning_effort'] == saved


@pytest.mark.parametrize('value', ['none', 'max', 'auto', 'LOW', None, False, 1, []])
def test_invalid_per_request_effort_is_422_before_model_admission(client, payloads, value):
    response = client.post('/api/chat', json={'message': 'Hello', 'reasoning_effort': value})
    assert response.status_code == 422
    assert payloads == []


@pytest.mark.parametrize('provider', ['openai-compatible', 'llama.cpp', 'lmstudio', 'ollama'])
def test_generic_and_local_response_payloads_keep_the_existing_toggle(client, config_module, payloads, provider):
    config_module.update_config_values({'llm_provider': provider, 'llm_reasoning_effort': 'high', 'llm_reasoning': 'off'})
    assert client.post('/api/chat', json={'message': 'Hello', 'reasoning': 'on', 'reasoning_effort': 'minimal'}).status_code == 200
    assert payloads[0]['reasoning'] == 'on'
    assert 'reasoning_effort' not in payloads[0]


@pytest.mark.parametrize('effort', EFFORTS)
def test_meta_agent_uses_only_its_own_captured_setting(config_module, payloads, effort):
    snapshot = config_module.update_config_values({
        'agent_provider': 'meta', 'agent_reasoning_effort': effort,
        'llm_provider': 'meta', 'llm_reasoning_effort': 'high',
        'agent_thinking_level': 'medium', 'agent_model': 'fixture-agent',
    })
    config_module.update_config_values({'agent_reasoning_effort': 'minimal', 'llm_reasoning_effort': 'low'})
    assert agent_service.generate_agent_state_report(AgentStateRequest(), config=snapshot) == 'Ready.'
    assert payloads[0].get('reasoning_effort') == (effort or None)
    assert ('reasoning_effort' in payloads[0]) is bool(effort)
    assert 'reasoning' not in payloads[0]


@pytest.mark.parametrize('provider', ['openai-compatible', 'anthropic'])
def test_other_agent_providers_do_not_receive_meta_effort(config_module, payloads, provider):
    config_module.update_config_values({
        'agent_provider': provider, 'agent_base_url': 'https://agent-fixture.test/v1',
        'agent_reasoning_effort': 'high', 'llm_reasoning_effort': 'minimal',
    })
    assert agent_service.generate_agent_state_report(AgentStateRequest()) == 'Ready.'
    assert 'reasoning_effort' not in payloads[0]
    assert 'reasoning' not in payloads[0]


def test_background_agent_keeps_its_effort_snapshot_and_next_report_uses_new_setting(
    monkeypatch, client, config_module, payloads,
):
    agent_service.shutdown_agent_worker()
    monkeypatch.setattr(gateway, '_active', KorinaAgentAdapter())
    config_module.update_config_values({'agent_enabled': 'on', 'agent_provider': 'meta',
                                        'agent_reasoning_effort': 'low', 'llm_reasoning_effort': 'minimal'})
    original = agent_service.generate_agent_state_report
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(agent_service, 'generate_agent_state_report', held)

    def submit(text):
        response = client.post('/api/agent/transcript', json={
            'transcript': [{'role': 'user', 'content': text}], 'delivery_mode': 'prompt',
        })
        assert response.status_code == 200 and response.json()['accepted'] is True

    def await_idle():
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = client.get('/api/agent/status').json()
            if not status['busy']:
                assert status['last_error'] is None
                return
            time.sleep(.005)
        pytest.fail('Agent fixture did not finish')

    submit('First snapshot.')
    try:
        assert entered.wait(3)
        generation = agent_state.generation
        changed = client.post('/api/config', json={'agent_reasoning_effort': 'high'})
        assert changed.status_code == 200
        assert agent_state.generation == generation
    finally:
        release.set()
    await_idle()
    assert payloads[0]['reasoning_effort'] == 'low'
    submit('Second snapshot.')
    await_idle()
    assert payloads[1]['reasoning_effort'] == 'high'
    assert config_module.load_config()['llm_reasoning_effort'] == 'minimal'


def test_effort_does_not_change_multimodal_stt(config_module, payloads, tmp_path):
    config_module.update_config_values({
        'llm_reasoning_effort': 'minimal', 'agent_reasoning_effort': 'high',
        'stt_llm_provider': 'openai-compatible', 'stt_llm_base_url': 'https://audio-fixture.test/v1',
        'stt_llm_reasoning': 'on', 'stt_llm_model': 'audio-fixture',
    })
    wav = tmp_path / 'fixture.wav'
    wav.write_bytes(b'fixture audio')
    assert multimodal_stt.lmstudio_transcribe_wav(wav)['text'] == 'Ready.'
    assert payloads[0]['reasoning'] == 'on'
    assert 'reasoning_effort' not in payloads[0]


@pytest.mark.parametrize('response_provider,agent_provider,expected', [
    ('meta', 'meta', ('minimal', 'high')), ('meta', 'openai-compatible', ('minimal', '')),
    ('openai-compatible', 'meta', ('', 'high')), ('openai-compatible', 'anthropic', ('', '')),
])
def test_health_exposes_only_effective_saved_meta_effort(
    monkeypatch, client, config_module, response_provider, agent_provider, expected,
):
    from korina.routes import health

    config_module.update_config_values({
        'llm_provider': response_provider, 'llm_reasoning_effort': 'minimal',
        'agent_provider': agent_provider, 'agent_reasoning_effort': 'high',
    })
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': True, 'loaded': True})
    monkeypatch.setattr(health, '_aggregate_tts', lambda config: {'ok': True})
    result = client.get('/api/health')
    assert result.status_code == 200
    body = result.json()
    assert (body['response_llm_reasoning_effort'], body['agent_reasoning_effort']) == expected
