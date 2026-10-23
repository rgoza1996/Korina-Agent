from __future__ import annotations


def test_probe_models_route_returns_results_and_updates_cache(monkeypatch, client, isolated_korina_app_dir):
    from korina.routes import providers as providers_route
    from korina.config import (
        audio_unsupported_key,
        load_config,
        set_audio_unsupported,
    )
    from korina.services.active_probe import ProbeResult

    captured = {}

    def fake_probe(provider, base_url, model, *, timeout=15.0, transcribe_fn=None, is_current=None):
        captured.setdefault('calls', []).append((provider, base_url, model))
        if 'good' in model:
            return ProbeResult(model=model, supported=True, reason='', latency_ms=42,
                               status=200, body_excerpt='', error='')
        # Real probe writes to cache on cacheable failure; mirror that here.
        set_audio_unsupported(provider, base_url, model,
                              'body_match:audio.*invalid', 'audio input is not supported')
        return ProbeResult(model=model, supported=False, reason='body_match:audio.*invalid',
                           latency_ms=99, status=400,
                           body_excerpt='audio input is not supported', error='')

    monkeypatch.setattr(providers_route, 'probe_audio_support', fake_probe)

    response = client.post(
        '/api/llm/probe-models',
        json={
            'provider': 'lmstudio',
            'base_url': 'http://127.0.0.1:1234/v1',
            'models': ['good-model', 'bad-model.gguf'],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['ok'] is True
    assert payload['results']['good-model']['supported'] is True
    assert payload['results']['bad-model.gguf']['supported'] is False
    assert payload['results']['bad-model.gguf']['cached'] is True
    assert captured['calls'] == [
        ('lmstudio', 'http://127.0.0.1:1234/v1', 'good-model'),
        ('lmstudio', 'http://127.0.0.1:1234/v1', 'bad-model.gguf'),
    ]

    cache = load_config()['audio_unsupported']
    triple = audio_unsupported_key('lmstudio', 'http://127.0.0.1:1234/v1', 'bad-model.gguf')
    assert triple in cache
    triple_good = audio_unsupported_key('lmstudio', 'http://127.0.0.1:1234/v1', 'good-model')
    assert triple_good not in cache


def test_probe_models_route_rejects_empty_models(client):
    response = client.post(
        '/api/llm/probe-models',
        json={'provider': 'lmstudio', 'base_url': 'http://127.0.0.1:1234/v1', 'models': []},
    )
    assert response.status_code == 400
    assert 'empty' in response.json()['detail'].lower() or 'model' in response.json()['detail'].lower()


def test_probe_models_route_rejects_missing_provider(client):
    response = client.post(
        '/api/llm/probe-models',
        json={'base_url': 'http://127.0.0.1:1234/v1', 'models': ['x']},
    )
    assert response.status_code == 400


def test_probe_models_route_marks_transient_as_uncached(monkeypatch, client, isolated_korina_app_dir):
    from korina.routes import providers as providers_route
    from korina.services.active_probe import ProbeResult
    from korina.config import audio_unsupported_key, load_config

    def fake_probe(provider, base_url, model, *, timeout=15.0, transcribe_fn=None, is_current=None):
        return ProbeResult(model=model, supported=False, reason='transient_runtime_error',
                           latency_ms=50, status=0, body_excerpt='connection reset',
                           error='connection reset by peer')

    monkeypatch.setattr(providers_route, 'probe_audio_support', fake_probe)

    response = client.post(
        '/api/llm/probe-models',
        json={'provider': 'lmstudio', 'base_url': 'http://127.0.0.1:1234/v1', 'models': ['x']},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['results']['x']['supported'] is False
    assert payload['results']['x']['cached'] is False
    triple = audio_unsupported_key('lmstudio', 'http://127.0.0.1:1234/v1', 'x')
    assert triple not in load_config()['audio_unsupported']


def test_probe_models_passes_explicit_auth_env(monkeypatch, client):
    from korina.routes import providers
    from korina.services.active_probe import ProbeResult

    calls = []

    def probe(provider, base_url, model, *, api_env, timeout, is_current=None):
        calls.append(api_env)
        return ProbeResult(model, True, '', 1, 200, '', '')

    monkeypatch.setattr(providers, 'probe_audio_support', probe)
    for env in ['CANDIDATE_KEY', '']:
        response = client.post('/api/llm/probe-models', json={
            'provider': 'openai-compatible', 'base_url': 'https://candidate.test/v1',
            'models': ['a'], 'api_key_env': env,
        })
        assert response.status_code == 200
    assert calls == ['CANDIDATE_KEY', '']


def test_probe_models_bounds_transport_and_model_identifiers(client):
    default = {'provider': 'openai-compatible', 'base_url': 'https://candidate.test/v1', 'models': ['a']}
    for extra in [
        {'provider': 'p' * 65}, {'base_url': 'x' * 4097},
        {'models': ['m' * 513]}, {'api_key_env': 'k' * 129},
    ]:
        assert client.post('/api/llm/probe-models', json={**default, **extra}).status_code == 422


def test_probe_batch_deadline_returns_completed_results_and_skips_remaining_without_caching(monkeypatch, client):
    from korina.routes import providers
    from korina.services.active_probe import ProbeResult
    from korina.config import config_audio_unsupported
    now = [100.0]
    calls = []
    monkeypatch.setattr(providers, 'monotonic', lambda: now[0])

    def probe(provider, base, model, *, timeout, is_current=None):
        calls.append((model, timeout))
        now[0] += timeout
        return ProbeResult(model, False, 'transient_runtime_error', int(timeout * 1000), 0, '', 'timeout')

    monkeypatch.setattr(providers, 'probe_audio_support', probe)
    result = client.post('/api/llm/probe-models', json={
        'provider': 'openai-compatible', 'base_url': 'https://fixture.test/v1',
        'models': [f'model-{i}' for i in range(10)],
    })
    assert result.status_code == 200
    body = result.json()
    assert calls == [(f'model-{i}', 15.0) for i in range(4)]
    assert set(body['results']) == {f'model-{i}' for i in range(4)}
    assert body['timed_out'] is True
    assert body['skipped_models'] == [f'model-{i}' for i in range(4, 10)]
    assert body['max_seconds'] == 60
    assert config_audio_unsupported() == {}


def test_final_probe_uses_only_remaining_time_and_deduplicates_ids(monkeypatch, client):
    from korina.routes import providers
    from korina.services.active_probe import ProbeResult
    now = [0.0]
    calls = []
    monkeypatch.setattr(providers, 'monotonic', lambda: now[0])

    def probe(provider, base, model, *, timeout, is_current=None):
        calls.append((model, timeout))
        now[0] += 23  # Include work outside the HTTP request as well.
        return ProbeResult(model, True, '', 1, 200, '', '')

    monkeypatch.setattr(providers, 'probe_audio_support', probe)
    result = client.post('/api/llm/probe-models', json={
        'provider': 'llama.cpp', 'base_url': 'http://fixture.test/v1',
        'models': [' first ', 'first', '', 'second', 'third', 'fourth'],
    }).json()
    assert calls == [('first', 15), ('second', 15), ('third', 14)]
    assert result['skipped_models'] == ['fourth']
