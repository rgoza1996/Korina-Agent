from __future__ import annotations

import pytest


@pytest.mark.parametrize('provider', ['unknown', 'anthropic', '  '])
@pytest.mark.parametrize('model', [None, 'demo'])
def test_activation_rejects_unsupported_response_provider_before_save(
    client, config_module, monkeypatch, provider, model,
):
    from korina.routes import providers

    before = config_module.load_config()
    monkeypatch.setattr(providers, 'select_response_provider', lambda *args: pytest.fail('selection saved'))
    monkeypatch.setattr(providers, 'activate_llm_provider', lambda *args, **kwargs: pytest.fail('process managed'))
    response = client.post('/api/llm/provider/activate', json={'provider': provider, 'model': model})
    assert response.status_code == 400
    assert response.json()['detail'] == 'Unsupported response provider'
    assert config_module.load_config() == before


@pytest.mark.parametrize('conflict', [False, True])
def test_activation_failure_reports_saved_selection_and_releases_lock(
    client, config_module, monkeypatch, conflict,
):
    from korina.routes import providers

    error_type = providers.ProviderRoleConflict if conflict else RuntimeError

    def fail(*args, **kwargs):
        raise error_type('kept active STT model')

    monkeypatch.setattr(providers, 'activate_llm_provider', fail)
    response = client.post('/api/llm/provider/activate', json={'provider': 'openai-compatible', 'model': 'next'})
    assert response.status_code == (409 if conflict else 500)
    detail = response.json()['detail']
    assert 'Selection was saved' in detail
    assert 'no automatic rollback' in detail
    assert 'kept active STT model' in detail
    assert config_module.load_config()['lm_model'] == 'next'

    monkeypatch.setattr(providers, 'activate_llm_provider', lambda *args, **kwargs: {})
    assert client.post('/api/llm/provider/activate', json={'provider': 'openai-compatible'}).status_code == 200


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


def test_activation_preserves_settings_and_other_probe_updates_during_preflight(client, monkeypatch, config_module):
    from korina.routes import providers
    from korina.services import provider_manager

    def preflight(provider, model):
        config_module.update_config_values({'speed': 1.5})
        config_module.set_audio_unsupported('other-provider', 'http://other/v1', 'audio', 'unsupported', '')
        return True, 'user_provided_endpoint'

    monkeypatch.setattr(provider_manager, 'provider_supports_model', preflight)
    monkeypatch.setattr(providers, 'activate_llm_provider', lambda *args, **kwargs: {})
    response = client.post('/api/llm/provider/activate', json={'provider': 'openai-compatible', 'model': 'demo'})
    assert response.status_code == 200
    assert response.json()['saved']['speed'] == 1.5
    assert len(config_module.config_audio_unsupported()) == 1
