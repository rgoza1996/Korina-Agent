"""Tests for /api/health provider-load blocks.

Each LLM-facing role (response, agent, multimodal-stt) must report:
  provider, base_url, expected_model, ok, loaded, loaded_model, error

`ok=True` means the provider's /v1/models endpoint responded 2xx.
`loaded=True` means the expected model appears in the response.

We monkeypatch the network probe so the test is hermetic.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _install(monkeypatch, payload):
    """payload: dict returned for any base_url. The fake echoes the configured
    expected model back as the only loaded id so default tests show loaded=True.
    Pass `ids=[...]` to override; pass `error=...` to simulate a transport error.
    """
    from korina.routes import health

    def _fake(base_url, expected, timeout=1.5):
        block = {
            "provider": "",
            "base_url": base_url or "",
            "expected_model": (expected or "").strip(),
            "ok": False,
            "loaded": False,
            "loaded_model": None,
            "loaded_models": [],
            "error": None,
        }
        if payload.get("error"):
            block["error"] = payload["error"]
            return block
        ids = payload.get("ids") or []
        if not ids and expected:
            ids = [expected]
        block["loaded_models"] = list(ids)
        exp = (expected or "").strip().lower()
        loaded = None
        if exp:
            for mid in ids:
                ml = mid.lower()
                if (ml == exp
                    or ml.endswith("/" + exp)
                    or ml.endswith(exp)
                    or exp.endswith("/" + ml)
                    or exp.endswith(ml)
                    or exp in ml):
                    loaded = mid
                    break
        block["loaded_model"] = loaded
        block["loaded"] = loaded is not None
        block["ok"] = True
        return block

    monkeypatch.setattr(health, "_probe_llm_models", _fake)


def test_health_response_load_block_matches_expected_model(client, monkeypatch):
    _install(monkeypatch, {})
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert "response_llm_load" in j
    rl = j["response_llm_load"]
    assert rl["ok"] is True
    assert rl["loaded"] is True
    assert rl["loaded_model"] is not None
    assert rl["error"] is None


def test_health_response_load_block_marks_unloaded_when_model_missing(client, monkeypatch):
    _install(monkeypatch, {"ids": ["some-other-model.gguf"]})
    j = client.get("/api/health").json()
    rl = j["response_llm_load"]
    assert rl["ok"] is True
    assert rl["loaded"] is False
    assert rl["loaded_model"] is None
    assert rl["loaded_models"] == ["some-other-model.gguf"]


def test_health_response_load_block_marks_offline_when_endpoint_unreachable(client, monkeypatch):
    _install(monkeypatch, {"error": "ConnectionRefusedError: [Errno 111] Connection refused"})
    j = client.get("/api/health").json()
    rl = j["response_llm_load"]
    assert rl["ok"] is False
    assert rl["loaded"] is False
    assert "Connection refused" in (rl["error"] or "")


def test_health_emit_load_block_for_each_role(client, monkeypatch):
    _install(monkeypatch, {})
    j = client.get("/api/health").json()
    assert "response_llm_load" in j
    assert "multimodal_stt_load" in j
    assert "agent_load" in j
    for key in ("response_llm_load", "multimodal_stt_load", "agent_load"):
        block = j[key]
        assert set(block.keys()) >= {"provider", "base_url", "expected_model", "ok", "loaded", "loaded_model", "loaded_models", "error"}
