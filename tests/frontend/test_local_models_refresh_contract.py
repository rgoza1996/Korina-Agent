"""Contracts for the Local GGUF model refresh UI."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_MODELS = REPO / "Korina" / "js" / "local-models.js"


def test_refresh_local_models_is_llama_cpp_only():
    src = LOCAL_MODELS.read_text()
    assert "llmProvider" in src
    assert "effectiveSttLlmProvider" in src
    assert "responseProvider !== 'llama.cpp' && sttProvider !== 'llama.cpp'" in src
    assert "Refresh local models only applies to llama.cpp" in src


def test_active_probe_does_not_probe_lmstudio_or_endpoint_providers():
    src = LOCAL_MODELS.read_text()
    assert "provider !== 'llama.cpp'" in src
    assert "provider !== 'lmstudio' && provider !== 'llama.cpp'" not in src
