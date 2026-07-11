"""Tests for CORS middleware config-driven allowlist (WS0.2)."""
import korina.config
from fastapi.testclient import TestClient
from korina import app_factory


def test_cors_allows_listed_origin(monkeypatch):
    monkeypatch.setattr(
        korina.config, "load_config",
        lambda: {"converse": {"allowed_origins": ["http://allowed.test:9000"]}},
    )
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.options(
            "/api/converse/channels",
            headers={
                "Origin": "http://allowed.test:9000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "http://allowed.test:9000"


def test_cors_blocks_unlisted_origin(monkeypatch):
    monkeypatch.setattr(
        korina.config, "load_config",
        lambda: {"converse": {"allowed_origins": ["http://allowed.test:9000"]}},
    )
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.options(
            "/api/converse/channels",
            headers={
                "Origin": "http://evil.test:1234",
                "Access-Control-Request-Method": "GET",
            },
        )
        acao = r.headers.get("access-control-allow-origin", "")
        assert acao != "*"
        assert acao != "http://evil.test:1234"


def test_cors_uses_default_when_config_has_no_converse_block(monkeypatch):
    """Legacy configs without `converse` block should still work via DEFAULT_CONFIG."""
    monkeypatch.setattr(korina.config, "load_config", lambda: {})
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.options(
            "/api/converse/channels",
            headers={
                "Origin": "http://127.0.0.1:8001",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8001"