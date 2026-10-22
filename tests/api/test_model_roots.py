from __future__ import annotations

import pytest

from korina.config import (
    _normalize_local_model_roots,
    config_local_model_roots,
    load_config,
    save_config,
    set_local_model_roots,
)


def test_normalize_local_model_roots_dedupes_and_strips():
    assert _normalize_local_model_roots(['a', 'a', '  b ', 1]) == ['a', 'b', '1']
    assert _normalize_local_model_roots('a:b:c') == ['a', 'b', 'c']
    assert _normalize_local_model_roots('a\nb:c') == ['a', 'b', 'c']
    assert _normalize_local_model_roots('') == []
    assert _normalize_local_model_roots(None) == []


def test_config_local_model_roots_round_trip(isolated_korina_app_dir, model_root):
    cfg = load_config()
    cfg['local_model_roots'] = [str(model_root)]
    save_config(cfg)
    assert config_local_model_roots() == [str(model_root)]


def test_set_local_model_roots_persists(isolated_korina_app_dir, model_root):
    persisted = set_local_model_roots([str(model_root), str(model_root)])
    assert persisted == [str(model_root)]  # deduped
    assert config_local_model_roots() == [str(model_root)]


def test_api_models_roots_get(isolated_korina_app_dir, model_root, client):
    set_local_model_roots([str(model_root)])
    r = client.get('/api/models/roots')
    assert r.status_code == 200
    body = r.json()
    assert len(body['roots']) == 1
    assert body['roots'][0]['path'] == str(model_root)
    assert body['roots'][0]['exists'] is True
    assert body['roots'][0]['is_directory'] is True


def test_api_models_roots_post_validates_and_persists(isolated_korina_app_dir, model_root, client):
    r = client.post('/api/models/roots', json={'roots': [str(model_root)]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['ok'] is True
    assert body['count'] == 1
    assert body['roots'][0]['path'] == str(model_root)
    assert body['roots'][0]['exists'] is True
    assert body['roots'][0]['is_directory'] is True
    r2 = client.get('/api/models/roots')
    assert r2.json()['roots'][0]['path'] == str(model_root)


def test_api_models_roots_post_rejects_nonexistent(isolated_korina_app_dir, client):
    r = client.post('/api/models/roots', json={'roots': ['/no/such/path/xyz']})
    assert r.status_code == 400
    assert 'not found' in r.json()['detail']


def test_api_models_roots_post_rejects_file(isolated_korina_app_dir, model_root, client):
    file = model_root / 'a.txt'
    file.write_text('hi')
    r = client.post('/api/models/roots', json={'roots': [str(file)]})
    assert r.status_code == 400
    assert 'not a directory' in r.json()['detail']


def test_api_models_roots_delete_removes_single(isolated_korina_app_dir, model_root, client):
    extra = isolated_korina_app_dir / 'extra_models'
    extra.mkdir(parents=True, exist_ok=True)
    set_local_model_roots([str(model_root), str(extra)])
    r = client.delete('/api/models/roots', params={'path': str(model_root)})
    assert r.status_code == 200
    body = r.json()
    assert body['removed'] == 1
    assert config_local_model_roots() == [str(extra)]
    assert body['roots'][0]['path'] == str(extra)


def test_api_llm_llama_refresh_when_inactive(isolated_korina_app_dir, model_root, client):
    """When llama.cpp is not active, refresh should still walk roots and return 200."""
    set_local_model_roots([str(model_root)])
    cfg = load_config()
    cfg['llm_provider'] = 'openai-compatible'
    save_config(cfg)
    r = client.post('/api/llm/llama/refresh')
    assert r.status_code == 200
    body = r.json()
    assert body['ok'] is True
    assert body['restarted'] is False
    assert body['active_provider'] == 'openai-compatible'
    assert 'discovered_count' in body


def test_api_llm_llama_refresh_persists_resolved_model(isolated_korina_app_dir, model_root, monkeypatch):
    """When llama.cpp is active and a known bare id is configured, refresh should
    resolve to the absolute path and persist it in config (without actually
    invoking systemd since the unit writer is monkeypatched)."""
    gguf = model_root / 'qwen3.5-2b-fake.gguf'
    gguf.write_text('gguf')

    from korina.routes import model_roots

    called = {}
    def fake_write(model_path, config=None):
        called['model'] = model_path
    def fake_start(model_path, config=None):
        called['started'] = model_path

    monkeypatch.setattr(model_roots, 'write_llama_server_unit', fake_write)
    monkeypatch.setattr(model_roots, 'start_llama_server', fake_start)

    set_local_model_roots([str(model_root)])
    cfg = load_config()
    cfg['llm_provider'] = 'llama.cpp'
    cfg['lm_model'] = 'qwen3.5-2b-fake'
    save_config(cfg)

    from fastapi.testclient import TestClient
    from korina import app_factory
    from korina.app_factory import create_app
    from korina.services import ack_service
    monkeypatch.setattr(ack_service, "enqueue_missing_acks", lambda *a, **k: 0)
    monkeypatch.setattr(app_factory, "enqueue_missing_acks", lambda *a, **k: 0)

    with TestClient(create_app()) as c:
        r = c.post('/api/llm/llama/refresh')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['restarted'] is True
        assert body['model'] == str(gguf)
        assert called.get('model') == str(gguf)
        assert called.get('started') == str(gguf)
        assert load_config()['lm_model'] == str(gguf)


def test_api_llm_llama_refresh_clears_llama_audio_probe_cache(isolated_korina_app_dir, model_root, client):
    from korina.config import audio_unsupported_key

    set_local_model_roots([str(model_root)])
    cfg = load_config()
    cfg["llm_provider"] = "openai-compatible"
    cfg["audio_unsupported"] = {
        audio_unsupported_key("llama.cpp", "http://127.0.0.1:8080/v1", "bad-model.gguf"): {
            "reason": "body_match:audio.*invalid",
            "since": "2026-06-26T00:00:00+00:00",
            "last_error": "bad",
        },
        audio_unsupported_key("lmstudio", "http://127.0.0.1:1234/v1", "keep-me"): {
            "reason": "body_match:audio.*invalid",
            "since": "2026-06-26T00:00:00+00:00",
            "last_error": "bad",
        },
    }
    save_config(cfg)

    r = client.post('/api/llm/llama/refresh')
    assert r.status_code == 200
    body = r.json()
    assert body['cleared_audio_probes'] == 1
    cache = load_config()['audio_unsupported']
    assert 'llama.cpp::http://127.0.0.1:8080/v1::bad-model.gguf' not in cache
    assert 'lmstudio::http://127.0.0.1:1234/v1::keep-me' in cache


def test_refresh_resolution_preserves_concurrent_settings_and_clears_current_cache(monkeypatch, client):
    from korina import config
    from korina.routes import model_roots

    config.update_config_values({'llm_provider': 'llama.cpp', 'lm_model': 'bare-demo'})
    config.set_audio_unsupported('llama.cpp', 'http://127.0.0.1:8080/v1', 'bad', 'unsupported', '')
    monkeypatch.setattr(model_roots, '_resolve_llama_cpp_model_id', lambda model: '/models/demo.gguf')

    def discover():
        # Discovery may take a long time; ordinary saves/probes may finish meanwhile.
        config.update_config_values({'voice': 'af_bella'})
        config.set_audio_unsupported('other', 'https://other.test/v1', 'keep', 'unsupported', '')
        return ['/models/demo.gguf']

    monkeypatch.setattr(model_roots, 'discover_local_gguf_models', discover)
    snapshots = []
    monkeypatch.setattr(model_roots, 'write_llama_server_unit', lambda model, config: snapshots.append(config))
    monkeypatch.setattr(model_roots, 'start_llama_server', lambda model, config: None)
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 200
    assert response.json()['cleared_audio_probes'] == 1
    saved = config.load_config()
    assert saved['voice'] == 'af_bella'
    assert saved['lm_model'] == '/models/demo.gguf'
    assert list(saved['audio_unsupported']) == ['other::https://other.test/v1::keep']
    assert snapshots[0]['voice'] == 'af_bella'


@pytest.mark.parametrize('changed', [{'llm_provider': 'openai-compatible'}, {'lm_model': 'newer-choice'}])
def test_refresh_rejects_a_changed_selection_before_restart(monkeypatch, client, changed):
    from korina import config
    from korina.routes import model_roots

    config.update_config_values({'llm_provider': 'llama.cpp', 'lm_model': 'bare-demo'})
    monkeypatch.setattr(model_roots, '_resolve_llama_cpp_model_id', lambda model: '/models/demo.gguf')
    monkeypatch.setattr(model_roots, 'discover_local_gguf_models', lambda: config.update_config_values(changed) and [])
    calls = []
    monkeypatch.setattr(model_roots, 'write_llama_server_unit', lambda *args, **kwargs: calls.append('write'))
    monkeypatch.setattr(model_roots, 'start_llama_server', lambda *args, **kwargs: calls.append('start'))
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 409
    assert 'settings changed' in response.json()['detail']
    assert not calls
    assert all(config.load_config()[key] == value for key, value in changed.items())
