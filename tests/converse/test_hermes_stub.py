"""Round-trip test for the Hermes stub adapter (Phase 5 Commit 5).

Scope: ONE round-trip test. Verifies the ConverseChannel protocol
shape against an external-agent-style adapter: register, send,
inspect the canned reply, unregister.

Autouse fixture mirrors ``test_routes.py``: reset registry +
``_DEFAULT_REGISTERED`` guard, then re-ensure the default so reads
of the registry look like the post-startup state.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the global ConverseChannel registry between tests."""
    from korina.converse import (
        ensure_default_registered,
        reset_default_registered,
        reset_for_testing,
    )
    from korina.adapters.hermes_stub import register_hermes_stub

    reset_for_testing()
    reset_default_registered()
    ensure_default_registered()
    yield
    register_hermes_stub(register=False)
    reset_for_testing()
    reset_default_registered()


def test_hermes_stub_round_trip_send():
    """Register the stub, send one request, verify the canned reply."""
    from korina.adapters.hermes_stub import register_hermes_stub
    from korina.converse import (
        ConverseRequest,
        get_active_channel_name,
        get_converse_channel,
        list_channels,
    )

    # 1. Register the stub under its declared name.
    register_hermes_stub()
    assert "hermes-stub" in list_channels()

    # 2. Resolve via the registry (named lookup, not active lookup).
    ch = get_converse_channel("hermes-stub")
    assert ch.name == "hermes-stub"

    # 3. Send() is async-def; wrap with asyncio.run.
    req = ConverseRequest(transcript=[{"role": "user", "text": "hello"}])
    resp = asyncio.run(ch.send(req))

    # 4. Canned reply is observable.
    assert resp.text == "[hermes-stub] ok"
    assert resp.finished is True
    assert resp.error is None

    # 5. Default channel (korina) remains active; "hermes-stub" is a
    #    registered alternative, not the active one.
    assert get_active_channel_name() == "korina"
