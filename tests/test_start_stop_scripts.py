"""Contracts for runtime start.sh / stop.sh."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
START = REPO / "Korina" / "start.sh"
STOP = REPO / "Korina" / "stop.sh"


def test_start_stop_scripts_exist_and_are_executable():
    for script in (START, STOP):
        assert script.exists(), script
        assert os.access(script, os.X_OK), f"{script} must be executable"


def test_start_sh_uses_config_and_provider_manager():
    src = START.read_text()
    assert "config.json" in src
    assert "activate_llm_provider" in src
    assert "llama.cpp|lmstudio|ollama" in src
    assert "openai-compatible" in src
    assert "korina-voice-lab.service" in src
    assert "kokoro-streaming-server.service" in src


def test_stop_sh_stops_all_korina_managed_servers():
    src = STOP.read_text()
    for needle in ["korina-voice-lab.service", "kokoro-streaming-server.service", "llama-server.service", "ollama.service", "stop_lmstudio_server", "stop_lmstudio", "stop_ollama", "llama-server.*--port (8080|1234)", "kokoro-streaming-server.py"]:
        assert needle in src
