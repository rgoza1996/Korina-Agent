"""Lazy default publication must not overwrite an explicit adapter selection."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from korina.agents import gateway
from korina.agents.korina_adapter import KorinaAgentAdapter
from korina.schemas import AgentCapabilities


class CustomAdapter:
    def __init__(self, name):
        self.id = name
        self.capabilities = AgentCapabilities(adapter_id=name)

    def submit_turn(self, turn):
        pytest.fail('Gateway lookup must not submit a model turn')

    def poll_events(self, cursor):
        pytest.fail('Gateway lookup must not poll the adapter')

    def answer_permission(self, answer):
        pytest.fail('Gateway lookup must not dispatch permissions')

    def reset(self):
        pytest.fail('Gateway lookup must not reset adapter state')

    def health_check(self):
        return {'ok': True, 'adapter': self.id, 'status': 'idle'}


class FalseyAdapter(CustomAdapter):
    def __bool__(self):
        return False


def test_registration_during_real_default_construction_wins_and_is_visible_over_http(client, monkeypatch):
    monkeypatch.setattr(gateway, '_active', None)
    entered, release = Event(), Event()
    constructed = []

    def held_new(cls):
        entered.set()
        assert release.wait(3)
        result = object.__new__(cls)
        constructed.append(result)
        return result

    monkeypatch.setattr(KorinaAgentAdapter, '__new__', staticmethod(held_new))
    custom = CustomAdapter('selected-custom')
    with ThreadPoolExecutor(max_workers=2) as pool:
        lookup = pool.submit(gateway.agent_gateway)
        try:
            assert entered.wait(2)
            # Registration must complete while the real default constructor is
            # held, rather than waiting on imports or arbitrary adapter code.
            pool.submit(gateway.register_adapter, custom).result(timeout=1)
            response = client.get('/api/agent/capabilities')
            assert response.status_code == 200
            assert response.json()['adapter_id'] == custom.id
        finally:
            release.set()
        assert lookup.result(timeout=2) is custom
    assert len(constructed) == 1 and type(constructed[0]) is KorinaAgentAdapter
    assert gateway.agent_gateway() is custom
    assert client.get('/api/agent/capabilities').json()['adapter_id'] == custom.id


def test_concurrent_lazy_getters_publish_and_return_one_real_default(monkeypatch):
    monkeypatch.setattr(gateway, '_active', None)
    constructors = Barrier(4, timeout=3)
    constructed = []

    def concurrent_new(cls):
        result = object.__new__(cls)
        constructed.append(result)
        constructors.wait()
        return result

    monkeypatch.setattr(KorinaAgentAdapter, '__new__', staticmethod(concurrent_new))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: gateway.agent_gateway(), range(4)))
    assert len(constructed) == 4
    assert type(results[0]) is KorinaAgentAdapter
    assert all(result is results[0] for result in results)
    assert gateway.agent_gateway() is results[0]


@pytest.mark.parametrize('adapter_type', [CustomAdapter, FalseyAdapter])
def test_registration_is_idempotent_last_selection_wins_and_never_constructs_default(client, monkeypatch, adapter_type):
    monkeypatch.setattr(gateway, '_active', None)

    def unexpected_new(cls):
        pytest.fail('An explicitly registered adapter must not construct a default')

    monkeypatch.setattr(KorinaAgentAdapter, '__new__', staticmethod(unexpected_new))
    first, second = adapter_type('first'), adapter_type('second')
    gateway.register_adapter(first)
    assert gateway.agent_gateway() is first
    gateway.register_adapter(first)
    assert gateway.agent_gateway() is first
    gateway.register_adapter(second)
    assert gateway.agent_gateway() is second
    assert client.get('/api/agent/capabilities').json()['adapter_id'] == second.id
