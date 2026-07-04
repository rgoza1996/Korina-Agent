
from __future__ import annotations

from pathlib import Path

import pytest


def test_classify_failure_audio_unsupported_bodies():
    from korina.services.audio_probe import classify_failure

    assert classify_failure(415, "anything") == (True, "http_415_unsupported_media_type")
    assert classify_failure(400, "audio input not supported by this model")[0] is True
    assert classify_failure(422, "no mmproj found for model")[0] is True
    assert classify_failure(400, "invalid API key") == (False, "transient_or_content_failure")
    assert classify_failure(500, "audio not supported maybe") == (False, "transient_or_content_failure")


def test_triple_key_normalizes_base_url(config_module):
    from korina.services.audio_probe import triple_key

    assert triple_key("llama.cpp", "http://127.0.0.1:8080/v1/chat/completions", "m.gguf") == (
        "llama.cpp::http://127.0.0.1:8080/v1::m.gguf"
    )
    assert triple_key("llama.cpp", "http://127.0.0.1:8080/v1/", "m.gguf") == (
        "llama.cpp::http://127.0.0.1:8080/v1::m.gguf"
    )


def test_maybe_fallback_persists_audio_unsupported_cache(config_module):
    from korina.services.audio_probe import maybe_fallback_to_whisper, triple_key

    calls = []

    def whisper_fallback():
        calls.append("fallback")
        return {"text": "fallback transcript", "engine": "whisper"}

    used, payload = maybe_fallback_to_whisper(
        "llama.cpp",
        "http://127.0.0.1:8080/v1",
        "model.gguf",
        400,
        "audio input not supported",
        whisper_fallback,
    )

    triple = triple_key("llama.cpp", "http://127.0.0.1:8080/v1", "model.gguf")
    assert used is True
    assert calls == ["fallback"]
    assert payload["text"] == "fallback transcript"
    assert payload["fallback_reason"].startswith("body_match:")
    assert triple in config_module.config_audio_unsupported()


def test_maybe_fallback_uses_cached_failure_without_reclassifying(config_module):
    from korina.services.audio_probe import maybe_fallback_to_whisper

    config_module.set_audio_unsupported(
        "llama.cpp", "http://127.0.0.1:8080/v1", "cached.gguf", "cached_reason", "old error"
    )

    used, payload = maybe_fallback_to_whisper(
        "llama.cpp",
        "http://127.0.0.1:8080/v1",
        "cached.gguf",
        0,
        "",
        lambda: {"text": "cached fallback"},
    )

    assert used is True
    assert payload["text"] == "cached fallback"
    assert payload["fallback_reason"] == "cached_reason"
    assert payload["audio_unsupported_since"]


def test_maybe_fallback_non_audio_failure_does_not_call_whisper():
    from korina.services.audio_probe import maybe_fallback_to_whisper

    def should_not_run():  # pragma: no cover - assertion below proves this path is not taken
        raise AssertionError("fallback should not run")

    used, payload = maybe_fallback_to_whisper(
        "openai-compatible",
        "http://api.example/v1",
        "remote-model",
        400,
        "rate limit exceeded",
        should_not_run,
    )

    assert used is False
    assert payload["error"] == "stt_failed"
    assert payload["status_code"] == 400


def test_probe_integration_pattern_with_mocked_multimodal_and_whisper(monkeypatch, tmp_path):
    from korina.services import multimodal_stt
    from korina.services.audio_probe import maybe_fallback_to_whisper

    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFFfake")

    def fake_multimodal(_wav: Path, **kwargs):
        raise RuntimeError("HTTP 400: audio input not supported")

    monkeypatch.setattr(multimodal_stt, "lmstudio_transcribe_wav", fake_multimodal)

    with pytest.raises(RuntimeError) as exc:
        multimodal_stt.lmstudio_transcribe_wav(wav, model="m")

    used, payload = maybe_fallback_to_whisper(
        "llama.cpp",
        "http://127.0.0.1:8080/v1",
        "m",
        400,
        str(exc.value),
        lambda: {"text": "from whisper"},
    )

    assert used is True
    assert payload["text"] == "from whisper"
