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
    resolve to the absolute path and persist it in config without invoking
    the managed service (its preflight/start boundaries are mocked)."""
    gguf = model_root / 'qwen3.5-2b-fake.gguf'
    gguf.write_text('gguf')

    from korina.routes import model_roots

    called = {}
    def fake_start(model_path, config=None):
        called['started'] = model_path

    monkeypatch.setattr(model_roots, 'prepare_provider', lambda provider, config, model, **kwargs: (model, {}, []))
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
    monkeypatch.setattr(model_roots, 'prepare_provider', lambda provider, config, model, **kwargs: (model, {}, []))
    monkeypatch.setattr(model_roots, 'start_llama_server', lambda model, config: snapshots.append(config))
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
    monkeypatch.setattr(model_roots, 'prepare_provider', lambda provider, config, model, **kwargs: (model, {}, []))
    monkeypatch.setattr(model_roots, 'start_llama_server', lambda *args, **kwargs: calls.append('start'))
    response = client.post('/api/llm/llama/refresh')
    assert response.status_code == 409
    assert 'settings changed' in response.json()['detail']
    assert not calls
    assert all(config.load_config()[key] == value for key, value in changed.items())


def test_final_delete_keeps_concurrent_settings_and_does_not_reenable_agent(client, model_root, config_module, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from korina.routes import model_roots

    config_module.update_config_values({'agent_enabled': 'on', 'voice': 'af_heart'})
    config_module.set_local_model_roots([str(model_root)])
    entered, release = threading.Event(), threading.Event()
    remove = config_module.remove_local_model_root

    def paused_remove(target):
        entered.set()
        assert release.wait(2)
        return remove(target)

    monkeypatch.setattr(model_roots.config_store, 'remove_local_model_root', paused_remove)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.delete, '/api/models/roots', params={'path': str(model_root)})
        assert entered.wait(1)
        try:
            config_module.update_config_values({'voice': 'af_bella', 'agent_enabled': 'off'})
        finally:
            release.set()
        assert pending.result(2).json()['removed'] == 1
    saved = config_module.load_config()
    assert saved['voice'] == 'af_bella'
    assert saved['agent_enabled'] == 'off'
    assert saved['local_model_roots'] == []
    assert client.get('/api/models/roots').json()['roots'] == []


def test_simultaneous_delete_of_two_roots_cannot_resurrect_either(client, model_root, tmp_path, config_module, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from korina.routes import model_roots

    second = tmp_path / 'second'
    second.mkdir()
    config_module.set_local_model_roots([str(model_root), str(second)])
    barrier = threading.Barrier(2)
    remove = config_module.remove_local_model_root

    def simultaneous_remove(target):
        barrier.wait(timeout=2)
        return remove(target)

    monkeypatch.setattr(model_roots.config_store, 'remove_local_model_root', simultaneous_remove)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda path: client.delete('/api/models/roots', params={'path': str(path)}), [model_root, second]))
    assert [result.json()['removed'] for result in results] == [1, 1]
    assert config_module.config_local_model_roots() == []
    assert client.get('/api/models/roots').json() == {'roots': []}


def test_env_fallback_is_retained_until_an_explicit_empty_write(client, model_root, config_module):
    config_module.save_config(dict(config_module.load_config(), local_model_roots=[], local_model_roots_explicit=False))
    assert config_module.config_local_model_roots() == [str(model_root)]
    assert client.post('/api/models/roots', json={'roots': []}).status_code == 200
    assert config_module.config_local_model_roots() == []
    assert client.get('/api/models/roots').json()['roots'] == []
    assert config_module.load_config()['local_model_roots_explicit'] is True
    assert client.post('/api/config', json={'local_model_roots_explicit': False}).status_code == 200
    assert config_module.config_local_model_roots() == [], 'clients cannot undo the server-owned marker'


def test_explicit_empty_settings_roots_survive_save_reload_and_unrelated_updates(client, config_module):
    assert client.post('/api/config', json={'local_model_roots': []}).status_code == 200
    assert config_module.config_local_model_roots() == []
    config_module.update_config_values({'voice': 'af_bella'})
    assert config_module.config_local_model_roots() == []


def test_roots_post_append_keeps_existing_roots_and_replace_remains_supported(client, model_root, tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    client.post('/api/models/roots', json={'roots': [str(first)]})
    appended = client.post('/api/models/roots', json={'roots': [str(second)], 'append': True})
    assert appended.status_code == 200
    assert [root['path'] for root in appended.json()['roots']] == [str(first), str(second)]
    replaced = client.post('/api/models/roots', json={'roots': [str(model_root)]})
    assert [root['path'] for root in replaced.json()['roots']] == [str(model_root)]
    absent = client.delete('/api/models/roots', params={'path': str(first)}).json()
    assert absent['removed'] == 0
    assert absent['roots'][0]['path'] == str(model_root), 'DELETE returns root metadata consistently'


@pytest.mark.parametrize('kind', ['too_long', 'too_many', 'nul', 'blank', 'os_path', 'missing', 'file'])
@pytest.mark.parametrize('endpoint', ['/api/config', '/api/models/roots'])
def test_invalid_new_root_lists_do_not_mutate_config(kind, endpoint, client, model_root, app_dir):
    from korina.model_roots import MAX_MODEL_ROOTS, MAX_MODEL_ROOT_PATH_CHARS

    file = model_root / 'file.txt'
    file.write_text('file')
    roots = {
        'too_long': ['x' * (MAX_MODEL_ROOT_PATH_CHARS + 1)],
        'too_many': [str(model_root)] * (MAX_MODEL_ROOTS + 1),
        'nul': ['bad\x00path'], 'blank': ['   '],
        'os_path': [str(model_root / ('x' * 300))],
        'missing': [str(model_root / 'missing')], 'file': [str(file)],
    }[kind]
    client.get('/api/config')
    config_path = app_dir / 'config.json'
    before = config_path.read_bytes()
    payload = {'voice': 'af_bella', 'local_model_roots': roots} if endpoint == '/api/config' else {'roots': roots}
    result = client.post(endpoint, json=payload)
    assert result.status_code in {400, 422}, result.text
    assert config_path.read_bytes() == before


def test_append_limit_is_checked_against_latest_combined_list(client, tmp_path, config_module):
    from korina.model_roots import MAX_MODEL_ROOTS

    existing = []
    for index in range(MAX_MODEL_ROOTS):
        directory = tmp_path / str(index)
        directory.mkdir()
        existing.append(str(directory))
    config_module.set_local_model_roots(existing)
    extra = tmp_path / 'extra'
    extra.mkdir()
    before = config_module.load_config()
    result = client.post('/api/models/roots', json={'roots': [str(extra)], 'append': True})
    assert result.status_code == 400
    assert config_module.load_config() == before


def test_invalid_legacy_roots_report_status_without_breaking_models_or_mutating_disk(client, app_dir, monkeypatch):
    import json
    from korina.routes import models

    config_path = app_dir / 'config.json'
    cfg = client.get('/api/config').json()
    cfg['local_model_roots'] = ['x' * 5000, 'bad\x00path']
    config_path.write_text(json.dumps(cfg))
    before = config_path.read_bytes()
    roots = client.get('/api/models/roots')
    assert roots.status_code == 200
    assert all(root['reason'].startswith('invalid_path:') for root in roots.json()['roots'])
    monkeypatch.setattr(models, 'llm_models_for', lambda *args, **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    assert client.get('/api/models').status_code == 200
    assert config_path.read_bytes() == before
    removed = client.delete('/api/models/roots', params={'path': 'x' * 5000})
    assert removed.status_code == 200
    assert removed.json()['removed'] == 1


def test_simultaneous_appends_merge_against_latest_roots(client, tmp_path, config_module, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from korina.routes import model_roots

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    config_module.set_local_model_roots([])
    append = config_module.append_local_model_roots
    barrier = threading.Barrier(2)

    def simultaneous_append(roots):
        barrier.wait(timeout=2)
        return append(roots)

    monkeypatch.setattr(model_roots.config_store, 'append_local_model_roots', simultaneous_append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda path: client.post('/api/models/roots', json={'roots': [str(path)], 'append': True}), [first, second]))
    assert all(result.status_code == 200 for result in results)
    assert set(config_module.config_local_model_roots()) == {str(first), str(second)}


@pytest.mark.parametrize('body', [b'{"roots":["\\ud800"]}', b'{"roots":[NaN]}', b'{"roots":[],"append":Infinity}'])
def test_malformed_root_inputs_return_json_validation_errors_without_disk_write(body, client, app_dir):
    client.get('/api/config')
    before = (app_dir / 'config.json').read_bytes()
    result = client.post('/api/models/roots', content=body, headers={'Content-Type': 'application/json'})
    assert result.status_code == 422
    assert result.json()['detail']
    assert all(set(error) == {'type', 'loc', 'msg'} for error in result.json()['detail'])
    assert (app_dir / 'config.json').read_bytes() == before
