"""Tests for persistent ConverseChannel selection (WS1).

The `test_channel` fixture is opt-in. Tests that need a non-default
channel register it; the "default korina" tests do not, so
ensure_default_registered() establishes the natural default.
"""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient

import korina.config
from korina import app_factory
from korina.converse import (
    ConverseChannel,
    ConverseRequest,
    ConverseResponse,
    register_converse_channel,
    reset_for_testing,
    get_active_channel_name,
)


class _TestChannel(ConverseChannel):
    name = "test-channel"

    async def send(self, req: ConverseRequest) -> ConverseResponse:
        return ConverseResponse(text="ok", finished=True)

    async def stream(self, req: ConverseRequest):
        yield ConverseResponse(text="ok", finished=True)

    async def cancel(self) -> None:
        return None


@pytest.fixture
def test_channel():
    """Opt-in: register a non-default channel for tests that need it."""
    reset_for_testing()
    register_converse_channel(_TestChannel())
    yield _TestChannel
    reset_for_testing()


@pytest.fixture(autouse=True)
def _clean_registry():
    """Autouse but minimal: just reset between tests so test-channel
    registrations don't leak."""
    reset_for_testing()
    yield
    reset_for_testing()


def test_startup_restores_saved_nondefault_channel(monkeypatch, test_channel):
    """Saved converse.channel=test-channel should make test-channel active after startup."""
    monkeypatch.setattr(
        korina.config, "load_config",
        lambda: {"converse": {"channel": "test-channel"}},
    )
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.get("/api/converse/channel")
        assert r.status_code == 200
        assert r.json()["channel"] == "test-channel"


def test_startup_uses_default_when_config_has_no_converse_block(monkeypatch):
    """Legacy config without converse block should default to korina."""
    monkeypatch.setattr(korina.config, "load_config", lambda: {})
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.get("/api/converse/channel")
        assert r.json()["channel"] == "korina"


def test_startup_falls_back_when_saved_channel_not_registered(monkeypatch):
    """A saved channel name that isn't in the registry should be ignored, not raise."""
    monkeypatch.setattr(
        korina.config, "load_config",
        lambda: {"converse": {"channel": "nonexistent-channel"}},
    )
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.get("/api/converse/channel")
        assert r.status_code == 200
        assert r.json()["channel"] == "korina"


def test_save_then_restore_round_trip(monkeypatch, test_channel):
    """End-to-end: switch to test-channel, save, re-create app, verify restored.

    Implementation note: fake_load returns a DEEP COPY of saved_state, and
    fake_save stores a DEEP COPY of the input. Without copying, the same
    object identity creates a self-referential dict whose clear()+update()
    pattern (used internally by korina.config.save_config) destroys the
    payload mid-write. Spy diagnostic 2026-07-11 confirmed this is the
    correct fix — the route's save_config call carries the right payload,
    the test's storage pattern was losing it.
    """
    saved_state = {}

    def fake_load():
        return copy.deepcopy(saved_state)

    def fake_save(cfg):
        # Store a deep copy so later route calls don't mutate the stored snapshot.
        saved_state.clear()
        saved_state.update(copy.deepcopy(cfg))

    monkeypatch.setattr(korina.config, "load_config", fake_load)
    monkeypatch.setattr(korina.config, "save_config", fake_save)

    app1 = app_factory.create_app()
    with TestClient(app1) as c1:
        r = c1.post("/api/converse/channel/test-channel")
        assert r.status_code == 200
        assert r.json()["channel"] == "test-channel"
    assert saved_state.get("converse", {}).get("channel") == "test-channel"

    app2 = app_factory.create_app()
    with TestClient(app2) as c2:
        r = c2.get("/api/converse/channel")
        assert r.json()["channel"] == "test-channel"


def test_failed_channel_save_keeps_previous_selection(monkeypatch, test_channel):
    from korina.routes import converse as route

    def fail_save(name):
        raise OSError("disk is full")

    app = app_factory.create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get('/api/converse/channel').json()['channel'] == 'korina'
        monkeypatch.setattr(route, 'save_converse_channel', fail_save)
        assert client.post('/api/converse/channel/test-channel').status_code == 500
        assert client.get('/api/converse/channel').json()['channel'] == 'korina'


def test_channel_switches_persist_in_selection_order(monkeypatch, test_channel):
    from korina.routes import converse as route
    from korina.converse import ensure_default_registered, set_active_converse_channel

    ensure_default_registered()
    set_active_converse_channel('korina')
    first_save_entered = threading.Event()
    finish_first_save = threading.Event()
    second_switch_started = threading.Event()
    second_save_entered = threading.Event()
    saved = []

    def controlled_save(name):
        if name == 'test-channel':
            first_save_entered.set()
            assert finish_first_save.wait(2)
        else:
            second_save_entered.set()
        saved.append(name)

    def switch_second():
        second_switch_started.set()
        return route.switch_active_channel('korina')

    monkeypatch.setattr(route, 'save_converse_channel', controlled_save)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(route.switch_active_channel, 'test-channel')
        try:
            assert first_save_entered.wait(2)
            second = pool.submit(switch_second)
            assert second_switch_started.wait(2)
            # A second save must not overtake the in-flight selection's save.
            assert not second_save_entered.wait(0.05)
        finally:
            finish_first_save.set()
        assert first.result(timeout=2)['ok']
        assert second.result(timeout=2)['ok']
    assert saved == ['test-channel', 'korina']
    assert get_active_channel_name() == saved[-1]
