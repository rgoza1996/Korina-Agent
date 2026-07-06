"""Route-level tests for /api/converse/* (Phase 5 Commit 4).

Uses the FastAPI TestClient via the ``client`` fixture from
``tests/conftest.py``.

Each test runs against a cleaned registry: the autouse fixture calls
``reset_for_testing()`` and ``reset_default_registered()`` so destructive
tests (register/unregister) don't leak global state into the rest of
the suite.

NOTE: ordering between this file's tests and broader integration tests
matters -- if any other integration test imports ``korina.converse``
after a destructive test in this file ran, the default channel will
not be re-registered unless they too use the ``client`` fixture
(which restarts the app per request via FastAPI's TestClient). The
autouse fixture is a defensive layer.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_converse_state():
    """Reset the global ConverseChannel registry between tests."""
    from korina.converse import (
        ensure_default_registered,
        reset_default_registered,
        reset_for_testing,
    )

    reset_for_testing()
    reset_default_registered()
    # Re-ensure the default so reads see the post-startup state.
    ensure_default_registered()
    yield
    reset_for_testing()
    reset_default_registered()


def test_get_active_channel_returns_korina_by_default(client):
    """After startup, the default channel is `korina`."""
    resp = client.get("/api/converse/channel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "korina"


def test_list_channels_includes_korina_by_default(client):
    """Default channel list contains `korina` after startup wiring."""
    resp = client.get("/api/converse/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert "korina" in body["channels"]


def test_switch_to_unknown_channel_returns_404(client):
    """POST /api/converse/channel/<unregistered> -> 404 with detail."""
    resp = client.post("/api/converse/channel/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    detail = body.get("detail") or ""
    assert "does-not-exist" in detail
    assert "Registered" in detail


def test_switch_to_korina_returns_200(client):
    """Switching to the already-active channel succeeds and returns ok=True."""
    resp = client.post("/api/converse/channel/korina")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"channel": "korina", "ok": True}

    resp = client.get("/api/converse/channel")
    assert resp.status_code == 200
    assert resp.json()["channel"] == "korina"


def test_register_then_switch_then_unregister_via_registry(client):
    """End-to-end: register a fake, switch via route, verify, unregister.

    Tests the route GET/POST contract against a fresh registration
    without relying on order or the autouse fixture for cleanup.
    """
    from korina.converse import (
        ConverseChannel,
        ConverseRequest,
        ConverseResponse,
        register_converse_channel,
        unregister_converse_channel,
    )

    class _FakeChannel(ConverseChannel):
        name = "route-test-fake"

        async def send(self, req: ConverseRequest) -> ConverseResponse:
            return ConverseResponse(text="fake", finished=True)

        async def stream(self, req: ConverseRequest):
            yield ConverseResponse(text="fake", finished=True)

        async def cancel(self) -> None:
            return None

    register_converse_channel(_FakeChannel())

    # Switch via the route surface.
    resp = client.post("/api/converse/channel/route-test-fake")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify active.
    resp = client.get("/api/converse/channel")
    assert resp.status_code == 200
    assert resp.json()["channel"] == "route-test-fake"

    # Unregister while active; active should fall back to whatever else
    # is registered. The autouse fixture re-registers `korina` at setup,
    # so `korina` is the fallback.
    assert unregister_converse_channel("route-test-fake") is True
    resp = client.get("/api/converse/channel")
    assert resp.status_code == 200
    assert resp.json()["channel"] in {"korina", None}


def test_get_active_channel_after_fixture_ensure_returns_korina(client):
    """After the autouse fixture runs setup, the default is korina.

    The fixture calls ``ensure_default_registered()`` at setup so reads
    after the fixture has run return ``korina``, not ``None``. The
    fixture's post-yield reset then leaves the registry empty as
    defensive cleanup for the next test.
    """
    resp = client.get("/api/converse/channel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel"] == "korina"
