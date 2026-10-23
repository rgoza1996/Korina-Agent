"""Successful explicit disable retires only transient built-in agent state."""
from __future__ import annotations

import pytest

from korina.agents.state import agent_state
from korina.services import agent_service
from korina.schemas import AgentTranscriptRequest


def test_disable_resets_builtin_context_only_after_persistence(config_module, monkeypatch):
    config_module.update_config_values({'agent_enabled': 'on'})
    calls = []
    monkeypatch.setattr(agent_service, 'reset_agent_state', lambda: calls.append(config_module.load_config()['agent_enabled']))
    config_module.update_config_values({'agent_enabled': 'off'})
    assert calls == ['off']
    config_module.update_config_values({'voice': 'new-voice'})
    config_module.update_config_values({'agent_enabled': 'off'})
    config_module.update_config_values({'agent_enabled': 'on'})
    assert calls == ['off'], 'ordinary settings and enable do not reset an agent'


def test_failed_disable_write_preserves_existing_work_and_can_retry(config_module, monkeypatch):
    config_module.update_config_values({'agent_enabled': 'on'})
    calls = []
    monkeypatch.setattr(agent_service, 'reset_agent_state', lambda: calls.append('reset'))
    original_save = config_module.save_config
    monkeypatch.setattr(config_module, 'save_config', lambda value: (_ for _ in ()).throw(OSError('disk unavailable')))
    with pytest.raises(OSError, match='disk unavailable'):
        config_module.update_config_values({'agent_enabled': 'off'})
    assert calls == []
    assert config_module.load_config()['agent_enabled'] == 'on'
    monkeypatch.setattr(config_module, 'save_config', original_save)
    config_module.update_config_values({'agent_enabled': 'off'})
    assert calls == ['reset']


def test_disable_clears_builtin_pending_generation_and_context(config_module):
    agent_service.shutdown_agent_worker()
    config_module.update_config_values({'agent_enabled': 'on'})
    with agent_state.lock:
        generation = agent_state.generation
        agent_state.last_report = 'Retired report'
        agent_service._queue_injection(AgentTranscriptRequest(transcript=[]))
        agent_service.push_agent_event({'type': 'state_report'})
    config_module.update_config_values({'agent_enabled': 'off'})
    with agent_state.lock:
        assert agent_state.generation == generation + 1
        assert agent_state.last_report == ''
        assert agent_state.events == []
        assert agent_state.pending_injections == []
    agent_service.shutdown_agent_worker()


@pytest.mark.parametrize('mode', ['prompt', 'injection'])
def test_admission_started_before_disable_cannot_queue_in_the_new_generation(mode, config_module, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from korina.schemas import AgentTranscriptRequest

    agent_service.shutdown_agent_worker()
    config_module.update_config_values({'agent_enabled': 'on'})
    read, release = threading.Event(), threading.Event()
    enabled_config = config_module.load_config()

    def paused_read():
        read.set()
        assert release.wait(2)
        return enabled_config

    monkeypatch.setattr(agent_service, 'load_config', paused_read)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(agent_service.submit_agent_transcript, AgentTranscriptRequest(
            transcript=[{'role': 'user', 'content': 'Old admission'}], delivery_mode=mode,
        ))
        assert read.wait(1)
        try:
            config_module.update_config_values({'agent_enabled': 'off'})
        finally:
            release.set()
        result = pending.result(timeout=1)
    assert result['accepted'] is False
    assert result['stale'] is True
    config_module.update_config_values({'agent_enabled': 'on'})
    with agent_state.lock:
        assert agent_state.pending_injections == []
        assert agent_state.events == []
    assert agent_service._queued_prompt is None
    assert agent_service._worker_running is False
    agent_service.shutdown_agent_worker()
