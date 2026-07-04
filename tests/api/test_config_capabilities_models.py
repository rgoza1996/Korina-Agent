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


def test_models_endpoint_adds_model_status_fields(monkeypatch, client, tmp_path):
    from korina.routes import models as models_route

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    good = good_dir / "good-audio.gguf"
    good.write_text("gguf")
    (good_dir / "good-audio-mmproj.gguf").write_text("mmproj")
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = bad_dir / "bad-text-only.gguf"
    bad.write_text("gguf")

    monkeypatch.setattr(models_route, "llm_models_for", lambda base, api_env: [str(good), str(bad), "endpoint-only"])
    monkeypatch.setattr(models_route, "discover_local_gguf_models", lambda: [str(good), str(bad)])
    monkeypatch.setattr(models_route, "discover_lmstudio_catalog_models", lambda: ["endpoint-only"])

    from korina.config import audio_unsupported_key, load_config, save_config
    cfg = load_config()
    cfg["audio_unsupported"] = {
        audio_unsupported_key("llama.cpp", "http://127.0.0.1:8080/v1", str(good)): {
            "reason": "body_match:audio.*invalid",
            "since": "2026-06-26T00:00:00+00:00",
            "last_error": "bad",
        }
    }
    save_config(cfg)

    response = client.get(
        "/api/models",
        params={
            "llm_base_url": "http://127.0.0.1:8080/v1",
            "stt_llm_base_url": "http://127.0.0.1:8080/v1",
            "llm_provider": "llama.cpp",
            "stt_llm_provider": "llama.cpp",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "llm_models_status" in payload
    assert "stt_llm_models_status" in payload
    assert payload["llm_models_status"]["endpoint-only"]["usable"] is False
    assert "endpoint-loaded ids" in " ".join(payload["llm_models_status"]["endpoint-only"]["reasons"])
    assert payload["llm_models_status"][str(good)]["usable"] is True
    assert payload["stt_llm_models_status"][str(good)]["usable"] is False
    assert "audio probe says unsupported" in " ".join(payload["stt_llm_models_status"][str(good)]["reasons"])
    assert payload["stt_llm_models_status"][str(bad)]["usable"] is False
    assert "missing mmproj" in " ".join(payload["stt_llm_models_status"][str(bad)]["reasons"])


def test_models_endpoint_marks_catalog_gguf_with_mmproj_sibling_red_for_stt(monkeypatch, client):
    from korina.routes import models as models_route

    main = "google/gemma-4-e2b-it-qat-q4_0-gguf/gemma-4-e2b_q4_0-it.gguf"
    mmproj = "google/gemma-4-e2b-it-qat-q4_0-gguf/gemma-4-e2b-it-mmproj.gguf"
    monkeypatch.setattr(models_route, "llm_models_for", lambda base, api_env: [main, mmproj])
    monkeypatch.setattr(models_route, "discover_local_gguf_models", lambda: [])
    monkeypatch.setattr(models_route, "discover_lmstudio_catalog_models", lambda: [main, mmproj])
    monkeypatch.setattr(models_route, "provider_supports_model", lambda provider, model: (True, "ok"))

    response = client.get(
        "/api/models",
        params={
            "llm_base_url": "http://127.0.0.1:1234/v1",
            "stt_llm_base_url": "http://127.0.0.1:1234/v1",
            "llm_provider": "lmstudio",
            "stt_llm_provider": "lmstudio",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_models_status"][main]["usable"] is True
    assert payload["stt_llm_models_status"][main]["usable"] is False
    assert "paired mmproj sidecar" in " ".join(payload["stt_llm_models_status"][main]["reasons"])
    assert payload["stt_llm_models_status"][mmproj]["usable"] is False


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
