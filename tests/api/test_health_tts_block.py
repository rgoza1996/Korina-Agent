"""Tests for the `tts` block added to /api/health.

Closes: #22 (server half of the fix; frontend consumes the block in #17/#20).

Behavior contract:
- /api/health response MUST include a top-level `tts_provider` (resolved
  from config.tts_provider, defaulting to 'kokoro').
- /api/health response MUST include a top-level `tts` object with keys:
    ok (bool), loaded (bool), device (str|null), cuda_available
    (bool), provider (str), base_url (str), error (str|null).
- When the TTS backend is unreachable, the endpoint MUST still return 200
  with tts.ok=False and tts.error describing the failure (degraded,
  not error — mirrors the existing whisper_loaded=False pattern).
- The aggregated probe MUST have a short timeout so /api/health stays
  fast even when TTS is hung.
"""
from __future__ import annotations

import socket
import threading
import time


def test_health_includes_tts_provider_top_level(client):
    """The resolved tts_provider from config must appear at top level."""
    resp = client.get('/api/health')
    assert resp.status_code == 200
    body = resp.json()
    assert 'tts_provider' in body, "tts_provider must be a top-level field"
    # Test config in conftest.py does not set tts_provider; default is 'kokoro'.
    assert body['tts_provider'] == 'kokoro'


def test_health_includes_tts_block(client):
    """The aggregated tts runtime block must be present with all required keys."""
    resp = client.get('/api/health')
    assert resp.status_code == 200
    body = resp.json()
    assert 'tts' in body, "tts block must be present in /api/health response"
    tts = body['tts']
    for key in ('ok', 'loaded', 'provider', 'base_url', 'error'):
        assert key in tts, f"tts.{key} must be present"
    for key in ('device', 'cuda_available'):
        assert key in tts, f"tts.{key} must be present (may be null/False)"


def test_health_tts_block_reports_unreachable_when_no_backend(client, monkeypatch):
    """When Kokoro is not running, tts.ok must be False and error populated."""
    from korina.routes import health as health_mod

    cfg = {
        'voice': 'af_heart',
        'tts_base_url': 'http://127.0.0.1:1',
        'tts_provider': 'kokoro',
    }
    monkeypatch.setattr(health_mod, 'load_config', lambda: cfg)

    resp = client.get('/api/health')
    assert resp.status_code == 200
    body = resp.json()
    tts = body['tts']
    assert tts['ok'] is False, "tts.ok must be False when backend is unreachable"
    assert tts['loaded'] is False
    assert tts['error'], "tts.error must be non-empty when probe fails"
    assert tts['base_url'] == 'http://127.0.0.1:1'


def test_health_tts_block_reports_loaded_when_backend_healthy(client, monkeypatch):
    """When a backend responds loaded=True, tts must reflect that."""
    from korina.routes import health as health_mod

    fake = {'status': 'ok', 'loaded': True, 'loaded_devices': ['cpu'], 'device': 'cpu', 'cuda_available': False}
    monkeypatch.setattr(health_mod, '_probe_tts_backend', lambda base_url, timeout=2.0: (True, fake, None))

    resp = client.get('/api/health')
    body = resp.json()
    tts = body['tts']
    assert tts['ok'] is True
    assert tts['loaded'] is True
    assert tts['device'] == 'cpu'
    assert tts['cuda_available'] is False
    assert tts['error'] is None


def test_health_tts_block_reports_lazy_when_backend_reports_not_loaded(client, monkeypatch):
    """Server responding but loaded=False must yield tts.loaded=False (not error)."""
    from korina.routes import health as health_mod
    fake = {'status': 'ok', 'loaded': False, 'loaded_devices': [], 'device': None, 'cuda_available': False}
    monkeypatch.setattr(health_mod, '_probe_tts_backend', lambda base_url, timeout=2.0: (True, fake, None))

    resp = client.get('/api/health')
    body = resp.json()
    tts = body['tts']
    assert tts['ok'] is True
    assert tts['loaded'] is False
    assert tts['error'] is None


def test_health_tts_probe_respects_timeout():
    """_probe_tts_backend must respect its timeout against a real hung socket."""
    from korina.routes import health as health_mod

    # Start a TCP server that accepts connections but never sends a response.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    port = server.getsockname()[1]

    def accept_loop():
        try:
            conn, _ = server.accept()
            # Hold the connection open without responding
            while True:
                try:
                    conn.recv(4096)
                except Exception:
                    break
        except Exception:
            pass

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()

    try:
        start = time.time()
        ok, body, error = health_mod._probe_tts_backend(f'http://127.0.0.1:{port}', timeout=1.0)
        elapsed = time.time() - start
        assert ok is False, "ok must be False when backend hangs"
        assert error and 'timeout' in error.lower(), f"error must mention timeout, got: {error!r}"
        assert elapsed < 3.0, f"probe took {elapsed:.1f}s - timeout=1s was not respected"
    finally:
        try:
            server.close()
        except Exception:
            pass


def test_health_preserves_all_existing_fields(client):
    """Adding tts block must not remove or rename any existing top-level fields."""
    resp = client.get('/api/health')
    body = resp.json()
    expected_existing = {
        'ok', 'service', 'whisper_backend', 'whisper_model',
        'whisper_loaded', 'cuda_available',
        'response_llm_provider', 'response_llm_base_url',
        'response_llm_model', 'multimodal_stt_provider',
        'multimodal_stt_base_url', 'multimodal_stt_model',
        'tts_base_url', 'ack_count', 'ack_status',
    }
    missing = expected_existing - set(body.keys())
    assert not missing, f"missing existing fields: {missing}"