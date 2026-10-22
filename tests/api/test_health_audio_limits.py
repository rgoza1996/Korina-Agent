"""Browser capture budgets must match the decoder and the effective ASGI cap."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from korina.routes import health
from korina.runtime.limits import MAX_AUDIO_UPLOAD_BYTES, RequestSizeLimit
from korina.services import whisper_service as whisper


@pytest.fixture(autouse=True)
def isolate_probes(monkeypatch):
    monkeypatch.setattr(health, '_probe_llm_models', lambda *args, **kwargs: {'ok': False, 'loaded': False})
    monkeypatch.setattr(health, '_probe_tts_backend', lambda *args, **kwargs: (False, {}, 'No model in test'))
    monkeypatch.setattr(health, 'cuda_available', lambda: False)
    monkeypatch.setenv('KORINA_FFMPEG_TIMEOUT_SECONDS', '60')


@pytest.mark.parametrize('configured,expected', [(None, 300), ('1', 1), ('90.5', 90.5), ('1800', 1800)])
def test_health_duration_matches_actual_decode_limit(client, tmp_path, monkeypatch, configured, expected):
    if configured is None:
        monkeypatch.delenv('KORINA_MAX_AUDIO_SECONDS', raising=False)
    else:
        monkeypatch.setenv('KORINA_MAX_AUDIO_SECONDS', configured)
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['audio_limits'] == {
        'max_duration_seconds': expected, 'max_upload_bytes': MAX_AUDIO_UPLOAD_BYTES,
    }
    assert response.json()['audio_limits_error'] is None
    assert response.json()['readiness']['checks']['audio_limits'] is True

    calls = []
    monkeypatch.setattr(whisper.subprocess, 'run', lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(whisper, 'audio_metadata', lambda path: {'duration': expected})
    whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')
    assert float(calls[0][calls[0].index('-t') + 1]) == expected + 1
    monkeypatch.setattr(whisper, 'audio_metadata', lambda path: {'duration': expected + 0.1})
    with pytest.raises(whisper.AudioDurationLimitError):
        whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')


@pytest.mark.parametrize('configured', ['0', '1801', 'nan', 'inf', 'forever', ''])
def test_invalid_audio_limit_is_visible_and_not_advertised_as_usable(client, tmp_path, monkeypatch, configured):
    monkeypatch.setenv('KORINA_MAX_AUDIO_SECONDS', configured)
    response = client.get('/api/health')
    assert response.status_code == 200, 'misconfiguration must not break backend liveness'
    assert response.json()['audio_limits'] is None
    assert 'KORINA_MAX_AUDIO_SECONDS' in response.json()['audio_limits_error']
    assert response.json()['readiness']['checks']['audio_limits'] is False
    ready = client.get('/api/ready')
    assert ready.status_code == 503
    assert ready.json()['checks']['audio_limits'] is False
    with pytest.raises(ValueError, match='KORINA_MAX_AUDIO_SECONDS'):
        whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')


def test_health_upload_budget_matches_default_multipart_enforcement(client, monkeypatch):
    monkeypatch.delenv('KORINA_MAX_AUDIO_SECONDS', raising=False)
    budget = client.get('/api/health').json()['audio_limits']['max_upload_bytes']
    assert budget == 52_428_800
    response = client.post('/api/transcribe', content=b'', headers={'Content-Length': str(budget + 1)})
    assert response.status_code == 413
    assert str(budget) in response.json()['detail']


def test_health_reports_the_tighter_effective_middleware_budget(monkeypatch):
    monkeypatch.delenv('KORINA_MAX_AUDIO_SECONDS', raising=False)
    app = FastAPI()
    app.include_router(health.router)
    app.add_middleware(RequestSizeLimit, audio_bytes=4096)
    app.add_middleware(RequestSizeLimit, audio_bytes=8192)
    with TestClient(app) as client:
        assert client.get('/api/health').json()['audio_limits']['max_upload_bytes'] == 4096
        response = client.post('/api/transcribe', content=b'', headers={'Content-Length': '4097'})
        assert response.status_code == 413
        assert '4096' in response.json()['detail']
