from __future__ import annotations


def test_provider_activate_rejects_incompatible_provider_model(client):
    response = client.post(
        "/api/llm/provider/activate",
        json={"provider": "llama.cpp", "model": "endpoint-loaded-id"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "incompatible_provider_model"
    assert detail["provider"] == "llama.cpp"
    assert "endpoint-loaded" in detail["reason"]


def test_audio_probe_list_and_delete_contract(client, config_module):
    config_module.set_audio_unsupported(
        "llama.cpp",
        "http://127.0.0.1:8080/v1",
        "bad-audio.gguf",
        "body_match:audio",
        "audio unsupported",
    )

    listed = client.get("/api/audio-probe")
    assert listed.status_code == 200
    entries = listed.json()["entries"]
    assert "llama.cpp::http://127.0.0.1:8080/v1::bad-audio.gguf" in entries

    deleted = client.delete(
        "/api/audio-probe",
        params={
            "provider": "llama.cpp",
            "base_url": "http://127.0.0.1:8080/v1",
            "model": "bad-audio.gguf",
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["removed"] is True
    assert client.get("/api/audio-probe").json()["entries"] == {}


def test_provider_activation_clears_probe_cache_and_uses_mocked_lifecycle(monkeypatch, client, config_module):
    from korina.routes import providers as providers_route

    config_module.set_audio_unsupported(
        "openai-compatible",
        "http://127.0.0.1:9999/v1",
        "remote-model",
        "body_match:audio",
        "audio unsupported",
    )

    def fake_activate(provider, config, model=None):
        return {"provider": provider, "model": model or config.get("lm_model"), "started": [], "stopped": []}

    monkeypatch.setattr(providers_route, "activate_llm_provider", fake_activate)

    response = client.post(
        "/api/llm/provider/activate",
        json={"provider": "openai-compatible", "model": "remote-model"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["activation"]["provider"] == "openai-compatible"
    assert config_module.config_audio_unsupported() == {}
