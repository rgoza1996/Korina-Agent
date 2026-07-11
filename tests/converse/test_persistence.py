"""Tests for persistent ConverseChannel selection (WS1).

The `test_channel` fixture is opt-in. Tests that need a non-default
channel register it; the "default korina" tests do not, so
ensure_default_registered() establishes the natural default.
"""
import copy
import pytest
from fastapi.testclient import TestClient

import korina.config
from korina import app_factory
from korina.converse import (
    ConverseChannel,
    ConverseRequest,
    ConverseResponse,
    register_converse_channel,
    reset_default_registered,
    reset_for_testing,
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
    reset_default_registered()
    register_converse_channel(_TestChannel())
    yield _TestChannel
    reset_for_testing()
    reset_default_registered()


@pytest.fixture(autouse=True)
def _clean_registry():
    """Autouse but minimal: just reset between tests so test-channel
    registrations don't leak."""
    reset_for_testing()
    reset_default_registered()
    yield
    reset_for_testing()
    reset_default_registered()


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