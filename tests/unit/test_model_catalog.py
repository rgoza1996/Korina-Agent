
from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_discover_local_gguf_models_uses_temp_roots(reload_korina_modules, model_root):
    keep = model_root / "family" / "model-a.gguf"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("gguf")
    (keep.parent / "model-a-mmproj.gguf").write_text("mmproj")
    (keep.parent / "model-a-assistant.gguf").write_text("assistant")
    other = model_root / "z-family" / "model-b.gguf"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("gguf")
    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]

    found = model_catalog.discover_local_gguf_models()

    assert str(keep) in found
    assert str(other) in found
    assert not any("mmproj" in path for path in found)
    assert not any(path.endswith("-assistant.gguf") for path in found)


def test_discover_lmstudio_catalog_models_uses_temp_hub(reload_korina_modules, lmstudio_root):
    manifest = lmstudio_root / "owner" / "repo" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"owner":"owner","name":"repo"}')
    invalid = lmstudio_root / "bad" / "manifest.json"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text('{"owner":"owner"}')
    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]

    assert model_catalog.discover_lmstudio_catalog_models() == ["owner/repo"]


def test_llm_models_for_combines_endpoint_and_local_gguf(monkeypatch, reload_korina_modules, model_root):
    local = model_root / "local" / "local-model.gguf"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("gguf")
    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]

    def fake_urlopen(req, timeout):
        assert req.full_url == "http://127.0.0.1:8080/v1/models"
        return FakeResponse({"data": [{"id": "endpoint-model"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    models = model_catalog.llm_models_for("http://127.0.0.1:8080/v1", "")

    assert models.count(str(local)) == 1
    assert models[0] == "endpoint-model"
    assert str(local) in models
