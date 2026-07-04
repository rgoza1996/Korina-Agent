
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


def test_discover_lmstudio_hub_manifests_uses_temp_hub(reload_korina_modules, lmstudio_root):
    """The hub-only function is deterministic and reads manifest.json files
    under LMSTUDIO_HUB_ROOT only -- no lms ls, no subprocess."""
    manifest = lmstudio_root / "owner" / "repo" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"owner":"owner","name":"repo"}')
    invalid = lmstudio_root / "bad" / "manifest.json"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text('{"owner":"owner"}')
    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]

    assert model_catalog.discover_lmstudio_hub_manifests() == ["owner/repo"]


def test_discover_lmstudio_catalog_models_unions_hub_and_lms(reload_korina_modules, lmstudio_root, tmp_path, monkeypatch):
    """discover_lmstudio_catalog_models returns the union of hub manifests
    AND `lms ls --json` modelKeys, so user-imported community quants are
    visible to the activate pre-flight and /api/models."""
    manifest = lmstudio_root / "owner" / "repo" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"owner":"owner","name":"repo"}')

    fake_lms = tmp_path / "lms"
    fake_lms.write_text(
        '#!/usr/bin/env bash\n'
        'echo \'[{"type":"llm","modelKey":"qwen3.5-2b-uncensored-hauhaucs-aggressive"}]\'\n'
    )
    fake_lms.chmod(0o755)

    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]
    monkeypatch.setattr(model_catalog, "LMS_CLI_BIN", fake_lms)

    catalog = model_catalog.discover_lmstudio_catalog_models()
    assert "owner/repo" in catalog
    assert "qwen3.5-2b-uncensored-hauhaucs-aggressive" in catalog


def test_discover_lmstudio_local_models_handles_lms_preamble(reload_korina_modules, tmp_path, monkeypatch):
    """`lms ls --json` may print 'Waking up LM Studio service...' before
    the JSON array on first call. The parser must tolerate that preamble."""
    fake_lms = tmp_path / "lms"
    fake_lms.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'Waking up LM Studio service...'\n"
        "echo '[{\"type\":\"llm\",\"modelKey\":\"x/y\"},{\"type\":\"embedding\",\"modelKey\":\"emb\"}]'\n"
    )
    fake_lms.chmod(0o755)

    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]
    monkeypatch.setattr(model_catalog, "LMS_CLI_BIN", fake_lms)

    out = model_catalog.discover_lmstudio_local_models()
    assert out == ["x/y"]  # embedding filtered out


def test_discover_lmstudio_local_models_returns_empty_when_lms_missing(reload_korina_modules, tmp_path, monkeypatch):
    """If the lms CLI is not installed, the local-model discovery must
    gracefully return [] rather than raising."""
    fake_lms = tmp_path / "lms"  # does not exist
    model_catalog = reload_korina_modules("korina.services.model_catalog")[0]
    monkeypatch.setattr(model_catalog, "LMS_CLI_BIN", fake_lms)
    assert model_catalog.discover_lmstudio_local_models() == []


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
