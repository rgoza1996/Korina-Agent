from __future__ import annotations


def test_config_get_and_post_round_trip_through_api(client):
    got = client.get("/api/config")
    assert got.status_code == 200
    assert got.json()["voice"] == "af_heart"

    posted = client.post("/api/config", json={"voice": "af_bella", "unknown_key": "drop"})
    assert posted.status_code == 200
    payload = posted.json()
    assert payload["voice"] == "af_bella"
    assert "unknown_key" not in payload

    assert client.get("/api/config").json()["voice"] == "af_bella"


def test_capabilities_endpoint_returns_provider_sections(client):
    response = client.get("/api/capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert "providers" in payload
    assert "agent_providers" in payload
    assert "openai-compatible" in payload["providers"]
    assert "anthropic" in payload["agent_providers"]
    assert "editable_base_url" in payload["providers"]["openai-compatible"]


def test_models_endpoint_adds_capability_fields_and_labels(monkeypatch, client, tmp_path):
    from korina.routes import models as models_route

    gguf = tmp_path / "Gemma-4-E2B_q4_0-it.gguf"
    gguf.write_text("gguf")
    monkeypatch.setattr(models_route, "llm_models_for", lambda base, api_env: ["endpoint-model"])
    monkeypatch.setattr(models_route, "discover_local_gguf_models", lambda: [str(gguf)])
    monkeypatch.setattr(models_route, "discover_lmstudio_catalog_models", lambda: ["owner/repo"])

    response = client.get(
        "/api/models",
        params={
            "llm_base_url": "http://127.0.0.1:8080/v1",
            "stt_llm_base_url": "http://127.0.0.1:8080/v1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "llm_models_capabilities" in payload
    assert "stt_llm_models_capabilities" in payload
    assert "endpoint-model" in payload["llm_models_capabilities"]
    assert str(gguf) in payload["llm_models_capabilities"]
    assert payload["llama_cpp_local_models"] == [str(gguf)]
    assert payload["lmstudio_catalog_models"] == ["owner/repo"]
    assert payload["labels"][str(gguf)].startswith("Gemma-4-E2B_q4_0-it.gguf")


def test_models_endpoint_reports_endpoint_errors_without_raising(monkeypatch, client):
    from korina.routes import models as models_route

    def boom(base, api_env):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(models_route, "llm_models_for", boom)
    monkeypatch.setattr(models_route, "discover_local_gguf_models", lambda: [])
    monkeypatch.setattr(models_route, "discover_lmstudio_catalog_models", lambda: [])

    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_models"] == []
    assert "endpoint down" in payload["llm_error"]
