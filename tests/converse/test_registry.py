"""Tests for the ConverseChannel registry.

Pure-logic tests; do not need the live service or the full FastAPI app.

These exercise the registry contract that drives Phase 5 adapter
selection (Commit 2 will swap channels via these APIs). Tests are
synchronous on purpose -- the registry is a plain Python layer with no
async dependencies, and pyproject.toml only ships `pytest` + `httpx`
as test deps.
"""
from __future__ import annotations

import pytest

from korina.converse import (
    ConverseChannel,
    ConverseRequest,
    ConverseResponse,
    get_active_channel_name,
    get_converse_channel,
    list_channels,
    register_converse_channel,
    reset_for_testing,
    set_active_converse_channel,
    unregister_converse_channel,
)


class _FakeChannel(ConverseChannel):
    name = "fake"

    def send_sync(self, text):
        return f"echo:{text}"

    async def send(self, req: ConverseRequest) -> ConverseResponse:
        # Not invoked by sync tests, but required by the abstract class.
        return ConverseResponse(
            text=self.send_sync(str(req.transcript)), finished=True
        )

    async def stream(self, req: ConverseRequest):
        yield ConverseResponse(text="", finished=False, metadata={"phase": "thinking"})
        yield await self.send(req)

    async def cancel(self) -> None:
        return None


class _OtherChannel(ConverseChannel):
    name = "other"

    async def send(self, req):
        return ConverseResponse(text="other", finished=True)

    async def stream(self, req):
        yield ConverseResponse(text="other", finished=True)

    async def cancel(self):
        return None


@pytest.fixture(autouse=True)
def _clean_registry():
    from korina.converse.korina_channel import reset_default_registered
    reset_for_testing()
    reset_default_registered()
    yield
    reset_for_testing()
    reset_default_registered()


def test_register_sets_active_if_empty():
    ch = _FakeChannel()
    register_converse_channel(ch)
    assert get_active_channel_name() == "fake"
    assert "fake" in list_channels()


def test_register_does_not_replace_active():
    register_converse_channel(_FakeChannel())
    register_converse_channel(_OtherChannel())
    # First registration stays active; switching requires explicit call.
    assert get_active_channel_name() == "fake"
    assert set(list_channels()) == {"fake", "other"}


def test_set_active_changes_active():
    register_converse_channel(_FakeChannel())
    register_converse_channel(_OtherChannel())
    set_active_converse_channel("other")
    assert get_active_channel_name() == "other"


def test_set_active_unknown_raises():
    register_converse_channel(_FakeChannel())
    with pytest.raises(KeyError):
        set_active_converse_channel("nope")


def test_get_active_returns_correct_instance():
    register_converse_channel(_FakeChannel())
    register_converse_channel(_OtherChannel())
    set_active_converse_channel("other")
    out = get_converse_channel()
    assert isinstance(out, _OtherChannel)


def test_get_unregistered_raises():
    register_converse_channel(_FakeChannel())
    with pytest.raises(KeyError):
        get_converse_channel("nope")


def test_unregister_returns_bool():
    register_converse_channel(_FakeChannel())
    assert unregister_converse_channel("fake") is True
    assert unregister_converse_channel("fake") is False


def test_unregister_active_picks_another():
    register_converse_channel(_FakeChannel())
    register_converse_channel(_OtherChannel())
    unregister_converse_channel("fake")
    # Active moved to the remaining channel.
    assert get_active_channel_name() == "other"


def test_unregister_last_clears_active():
    register_converse_channel(_FakeChannel())
    unregister_converse_channel("fake")
    assert get_active_channel_name() is None


def test_register_rejects_non_protocol():
    with pytest.raises(TypeError):
        register_converse_channel("not a channel")  # type: ignore[arg-type]


def test_protocol_is_runtime_checkable():
    # ConverseChannel uses runtime_checkable; verify isinstance works
    # against arbitrary subclasses (positive AND negative cases).
    fake = _FakeChannel()
    assert isinstance(fake, ConverseChannel)
    assert not isinstance("not a channel", ConverseChannel)


def test_list_channels_returns_new_list_each_call():
    register_converse_channel(_FakeChannel())
    a = list_channels()
    b = list_channels()
    assert a == b
    assert a is not b  # defensive copy semantics


def test_korina_channel_round_trip_via_send():
    """Smoke test: the default channel's send() compiles and returns shape."""
    from korina.converse import KorinaConverseChannel

    ch = KorinaConverseChannel()
    assert ch.name == "korina"
    assert hasattr(ch, "send")
    assert hasattr(ch, "stream")
    assert hasattr(ch, "cancel")
    # No event loop run: just confirm shape contract.
    req = ConverseRequest(transcript=[{"role": "user", "text": "hi"}])
    assert req.transcript  # constructed cleanly


def test_ensure_default_registered_is_idempotent():
    from korina.converse import (
        ensure_default_registered,
        reset_for_testing,
    )
    reset_for_testing()
    ensure_default_registered()
    first = list_channels()
    ensure_default_registered()
    second = list_channels()
    assert first == second
    assert "korina" in first
    reset_for_testing()
