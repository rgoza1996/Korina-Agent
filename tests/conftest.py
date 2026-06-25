
from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="korina-pytest-"))
_TEST_APP_DIR = _TEST_ROOT / "app"
_TEST_MODEL_ROOT = _TEST_ROOT / "models"
_TEST_LMSTUDIO_ROOT = _TEST_ROOT / "lmstudio-hub"
_TEST_LLAMA_BIN = _TEST_ROOT / "bin" / "llama-server"
_TEST_LLAMA_UNIT = _TEST_ROOT / "systemd" / "llama-server.service"

# korina.util.paths resolves constants at import time. Force test-safe paths
# before any test module imports korina.*.
os.environ["KORINA_APP_DIR"] = str(_TEST_APP_DIR)
os.environ["KORINA_LOCAL_MODEL_ROOTS"] = str(_TEST_MODEL_ROOT)
os.environ["KORINA_LMSTUDIO_HUB_ROOT"] = str(_TEST_LMSTUDIO_ROOT)
os.environ["KORINA_LLAMA_SERVER_BIN"] = str(_TEST_LLAMA_BIN)
os.environ["KORINA_LLAMA_SERVER_USER_UNIT"] = str(_TEST_LLAMA_UNIT)


MINIMAL_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <main id="app">Korina test app</main>
  <script type="module" src="./js/app.js"></script>
</body>
</html>
"""

MINIMAL_CONFIG = {
    "voice": "af_heart",
    "llm_provider": "openai-compatible",
    "llm_base_url": "http://127.0.0.1:9999/v1",
    "lm_model": "test-model",
    "stt_backend": "whisper",
    "agent_enabled": "off",
}


def _write_minimal_runtime(app_dir: Path = _TEST_APP_DIR) -> Path:
    if app_dir.exists():
        shutil.rmtree(app_dir)
    (app_dir / "js").mkdir(parents=True, exist_ok=True)
    (app_dir / "Ack").mkdir(parents=True, exist_ok=True)
    (app_dir / "config").mkdir(parents=True, exist_ok=True)
    app_dir.joinpath("index.html").write_text(MINIMAL_INDEX)
    app_dir.joinpath("styles.css").write_text("body { font-family: sans-serif; }\n")
    for name in ["app.js", "state.js", "providers-ui.js", "capability-filter.js"]:
        app_dir.joinpath("js", name).write_text(f"export const testModule = {name!r};\n")
    app_dir.joinpath("Ack", "ack_phrases.json").write_text(json.dumps({
        "phrases": [{"id": "ack_01", "text": "OK.", "tags": ["global"]}],
    }) + "\n")
    app_dir.joinpath("config", "config.example.json").write_text(
        json.dumps(MINIMAL_CONFIG, indent=2, sort_keys=True) + "\n"
    )
    return app_dir


_write_minimal_runtime(_TEST_APP_DIR)


@pytest.fixture(autouse=True)
def isolated_korina_app_dir(monkeypatch):
    """Keep every test pointed at a disposable Korina runtime tree."""
    monkeypatch.setenv("KORINA_APP_DIR", str(_TEST_APP_DIR))
    monkeypatch.setenv("KORINA_LOCAL_MODEL_ROOTS", str(_TEST_MODEL_ROOT))
    monkeypatch.setenv("KORINA_LMSTUDIO_HUB_ROOT", str(_TEST_LMSTUDIO_ROOT))
    monkeypatch.setenv("KORINA_LLAMA_SERVER_BIN", str(_TEST_LLAMA_BIN))
    monkeypatch.setenv("KORINA_LLAMA_SERVER_USER_UNIT", str(_TEST_LLAMA_UNIT))
    _write_minimal_runtime(_TEST_APP_DIR)
    _TEST_MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    _TEST_LMSTUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    _TEST_LLAMA_BIN.parent.mkdir(parents=True, exist_ok=True)
    yield _TEST_APP_DIR


@pytest.fixture
def app_dir() -> Path:
    return _TEST_APP_DIR


@pytest.fixture
def model_root() -> Path:
    _TEST_MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    return _TEST_MODEL_ROOT


@pytest.fixture
def lmstudio_root() -> Path:
    _TEST_LMSTUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    return _TEST_LMSTUDIO_ROOT


@pytest.fixture
def reload_korina_modules():
    """Reload path-sensitive korina modules after env/fixture changes."""
    def _reload(*module_names: str):
        loaded = []
        ordered = ["korina.util.paths", *module_names]
        for name in ordered:
            if name in sys.modules:
                loaded.append(importlib.reload(sys.modules[name]))
            else:
                loaded.append(importlib.import_module(name))
        return loaded[-len(module_names):] if module_names else loaded

    return _reload


@pytest.fixture
def config_module(reload_korina_modules):
    return reload_korina_modules("korina.config")[0]


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient with ACK startup generation disabled."""
    from fastapi.testclient import TestClient

    from korina import app_factory
    from korina.services import ack_service

    monkeypatch.setattr(ack_service, "enqueue_missing_acks", lambda *args, **kwargs: 0)
    monkeypatch.setattr(app_factory, "enqueue_missing_acks", lambda *args, **kwargs: 0)
    with TestClient(app_factory.create_app()) as test_client:
        yield test_client
