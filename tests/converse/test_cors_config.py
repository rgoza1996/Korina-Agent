"""Tests for the converse config block (CORS allowed_origins, channel selection)."""
from korina.config import DEFAULT_CONFIG


def test_default_config_has_converse_block():
    assert "converse" in DEFAULT_CONFIG
    assert isinstance(DEFAULT_CONFIG["converse"], dict)


def test_default_config_has_allowed_origins():
    origins = DEFAULT_CONFIG["converse"]["allowed_origins"]
    assert isinstance(origins, list)
    assert "http://127.0.0.1:8001" in origins
    assert "http://localhost:8001" in origins


def test_default_config_has_converse_channel():
    assert DEFAULT_CONFIG["converse"]["channel"] == "korina"