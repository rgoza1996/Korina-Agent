"""Internal built-in epochs must not alter or leak across adapter contracts."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from korina.agents import AgentHttpBridge
from korina.agents.state import bind_builtin_admission, expected_builtin_generation
from korina.schemas import AgentCapabilities, AgentTranscriptRequest


def test_nested_bindings_restore_outer_epoch_even_after_exception():
    assert expected_builtin_generation() is None
    with bind_builtin_admission(10):
        assert expected_builtin_generation() == 10
        with pytest.raises(RuntimeError, match='nested failure'):
            with bind_builtin_admission(20):
                assert expected_builtin_generation() == 20
                raise RuntimeError('nested failure')
        assert expected_builtin_generation() == 10
    assert expected_builtin_generation() is None


def test_bindings_are_isolated_between_concurrent_threads_and_reused_workers():
    barrier = threading.Barrier(2)

    def dispatch(generation):
        assert expected_builtin_generation() is None
        with bind_builtin_admission(generation):
            barrier.wait(timeout=2)
            actual = expected_builtin_generation()
            barrier.wait(timeout=2)
            assert actual == generation == expected_builtin_generation()
        assert expected_builtin_generation() is None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [executor.submit(dispatch, generation) for generation in (11, 22)]
        for result in results:
            result.result(timeout=3)
        assert executor.submit(expected_builtin_generation).result(timeout=1) is None


def test_custom_adapter_exception_restores_binding_without_changing_signature(config_module):
    config_module.update_config_values({'agent_enabled': 'on'})

    class Adapter:
        id = 'custom'
        capabilities = AgentCapabilities(adapter_id='custom')

        def submit_turn(self, turn):
            assert isinstance(expected_builtin_generation(), int)
            raise RuntimeError('custom failure')

        def health_check(self):
            return {'ok': True}

    with bind_builtin_admission(-100):
        with pytest.raises(RuntimeError, match='custom failure'):
            AgentHttpBridge(Adapter()).submit_transcript(AgentTranscriptRequest(
                transcript=[{'role': 'user', 'content': 'hello'}],
            ))
        assert expected_builtin_generation() == -100
    assert expected_builtin_generation() is None
