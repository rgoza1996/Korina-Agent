from __future__ import annotations

import io
import wave

import pytest


def _silent_wav_bytes(sample_rate: int = 16000, seconds: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * (sample_rate * seconds))
    return buf.getvalue()


def test_probe_audio_support_marks_model_unsupported_on_400_body(monkeypatch, isolated_korina_app_dir):
    """When the runtime raises an HTTP 400 with an audio-not-supported body,
    the probe must classify it as audio_unsupported and write to the cache."""
    from korina.services import active_probe
    from korina.config import audio_unsupported_key, load_config

    def boom(wav, *, model, base_url, api_env):
        raise RuntimeError(
            'Multimodal STT model bad rejected audio transcription request: HTTP 400: '
            '{"error":"audio input is not supported by this model"}'
        )

    result = active_probe.probe_audio_support(
        'llama.cpp',
        'http://127.0.0.1:8080/v1',
        'bad-audio.gguf',
        transcribe_fn=boom,
    )

    assert result.supported is False
    assert 'audio' in result.reason.lower() or 'body_match' in result.reason
    assert result.status == 400
    triple = audio_unsupported_key('llama.cpp', 'http://127.0.0.1:8080/v1', 'bad-audio.gguf')
    cache = load_config()['audio_unsupported']
    assert triple in cache


def test_probe_audio_support_clears_cache_on_success(monkeypatch, isolated_korina_app_dir):
    """When the probe returns text, any stale audio_unsupported cache entry
    must be removed."""
    from korina.services import active_probe
    from korina.config import (
        audio_unsupported_key,
        load_config,
        set_audio_unsupported,
    )

    set_audio_unsupported(
        "lmstudio",
        "http://127.0.0.1:1234/v1",
        "good-model",
        "body_match:audio.*invalid",
        "stale",
    )
    assert (
        audio_unsupported_key("lmstudio", "http://127.0.0.1:1234/v1", "good-model")
        in load_config()["audio_unsupported"]
    )

    def ok(wav, *, model, base_url, api_env):
        return {"text": "hello", "segments": []}

    result = active_probe.probe_audio_support(
        "lmstudio",
        "http://127.0.0.1:1234/v1",
        "good-model",
        transcribe_fn=ok,
    )

    assert result.supported is True
    assert result.reason == ""
    triple = audio_unsupported_key("lmstudio", "http://127.0.0.1:1234/v1", "good-model")
    assert triple not in load_config()["audio_unsupported"]


def test_probe_audio_support_treats_415_as_unsupported(monkeypatch, isolated_korina_app_dir):
    from korina.services import active_probe
    from korina.config import audio_unsupported_key, load_config

    def boom(wav, *, model, base_url, api_env):
        raise RuntimeError(
            "Multimodal STT model rejected audio transcription request: HTTP 415: unsupported media"
        )

    result = active_probe.probe_audio_support(
        "lmstudio", "http://127.0.0.1:1234/v1", "x", transcribe_fn=boom,
    )

    assert result.supported is False
    assert result.reason == "http_415_unsupported_media_type"
    triple = audio_unsupported_key("lmstudio", "http://127.0.0.1:1234/v1", "x")
    assert triple in load_config()["audio_unsupported"]


def test_probe_audio_support_does_not_cache_transient_runtime_error(monkeypatch, isolated_korina_app_dir):
    """A generic non-audio error (no body_match for audio) must not be persisted
    to the cache -- it is transient and should not poison the dropdown."""
    from korina.services import active_probe
    from korina.config import audio_unsupported_key, load_config

    def boom(wav, *, model, base_url, api_env):
        raise RuntimeError("connection reset by peer")

    result = active_probe.probe_audio_support(
        "llama.cpp", "http://127.0.0.1:8080/v1", "x", transcribe_fn=boom,
    )

    assert result.supported is False
    assert "transient" in result.reason
    triple = audio_unsupported_key("llama.cpp", "http://127.0.0.1:8080/v1", "x")
    assert triple not in load_config()["audio_unsupported"]


def test_probe_audio_support_does_not_cache_empty_200(monkeypatch, isolated_korina_app_dir):
    """An empty 200 response is treated as transient (silent failure) and
    must NOT be persisted, since it could be a model that just crashed."""
    from korina.services import active_probe
    from korina.config import audio_unsupported_key, load_config

    def empty(wav, *, model, base_url, api_env):
        return {"text": "", "segments": []}

    result = active_probe.probe_audio_support(
        "lmstudio", "http://127.0.0.1:1234/v1", "x", transcribe_fn=empty,
    )

    assert result.supported is False
    triple = audio_unsupported_key("lmstudio", "http://127.0.0.1:1234/v1", "x")
    assert triple not in load_config()["audio_unsupported"]


def test_probe_many_returns_results_in_order(monkeypatch, isolated_korina_app_dir):
    from korina.services import active_probe

    def ok(wav, *, model, base_url, api_env):
        return {"text": "ok"}

    results = active_probe.probe_many(
        "lmstudio",
        "http://127.0.0.1:1234/v1",
        ["a", "b", "c"],
        transcribe_fn=ok,
    )
    assert [r.model for r in results] == ["a", "b", "c"]
    assert all(r.supported for r in results)


def test_probe_audio_support_synthetic_wav_is_valid():
    """Sanity: the probe's synthetic WAV should round-trip via stdlib wave."""
    from korina.services.active_probe import _build_synthetic_wav
    wav_bytes = _build_synthetic_wav()
    assert isinstance(wav_bytes, (bytes, bytearray))
    assert len(wav_bytes) > 44  # more than just the WAV header
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf, "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 16000


def test_default_probe_forwards_timeout_without_leaking_configured_secret(monkeypatch, config_module):
    import json
    from korina.services import active_probe, multimodal_stt

    config_module.update_config_values({
        'stt_llm_base_url': 'https://configured.test/v1',
        'stt_llm_provider': 'openai-compatible',
        'stt_llm_api_key_env': 'PROBE_CONFIGURED_KEY',
    })
    monkeypatch.setenv('PROBE_CONFIGURED_KEY', 'configured-secret')
    calls = []

    def urlopen(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}]}).encode())

    monkeypatch.setattr(multimodal_stt.urllib.request, 'urlopen', urlopen)
    result = active_probe.probe_audio_support(
        'openai-compatible', 'https://candidate.test/v1', 'audio-model', timeout=0.25,
    )
    assert result.supported is True
    assert calls[0][0].full_url == 'https://candidate.test/v1/chat/completions'
    assert calls[0][0].get_header('Authorization') is None
    assert calls[0][1] == 0.25


def test_probe_auth_and_full_endpoint_normalization_preserve_legacy_callback(config_module):
    from korina.services import active_probe

    config_module.update_config_values({
        'stt_llm_base_url': 'https://configured.test/v1',
        'stt_llm_provider': 'openai-compatible',
        'stt_llm_api_key_env': 'CONFIGURED_ENV',
    })
    calls = []

    def legacy_callback(wav, *, model, base_url, api_env):
        calls.append((base_url, api_env))
        return {'text': 'hello'}

    active_probe.probe_audio_support('openai-compatible', 'https://configured.test/v1/chat/completions/', 'a', transcribe_fn=legacy_callback)
    active_probe.probe_audio_support('openai-compatible', 'https://configured.test/v1', 'a', api_env='', transcribe_fn=legacy_callback)
    active_probe.probe_many('openai-compatible', 'https://candidate.test/v1', ['a', 'b'], api_env='CANDIDATE_ENV', transcribe_fn=legacy_callback)
    assert calls == [
        ('https://configured.test/v1/chat/completions', 'CONFIGURED_ENV'),
        ('https://configured.test/v1/chat/completions', ''),
        ('https://candidate.test/v1/chat/completions', 'CANDIDATE_ENV'),
        ('https://candidate.test/v1/chat/completions', 'CANDIDATE_ENV'),
    ]


@pytest.mark.parametrize('finish_reason,cached', [('length', True), ('content_filter', True), ('stop', False), ('', False)])
def test_probe_empty_completion_matches_live_finish_reason_classification(config_module, finish_reason, cached):
    from korina.services import active_probe
    from korina.config import audio_unsupported_key

    def empty(wav, *, model, base_url, api_env):
        return {'text': '', 'finish_reason': finish_reason}

    result = active_probe.probe_audio_support('openai-compatible', 'https://probe.test/v1', 'model', transcribe_fn=empty)
    key = audio_unsupported_key('openai-compatible', 'https://probe.test/v1', 'model')
    assert (key in config_module.load_config()['audio_unsupported']) is cached
    assert result.supported is False
    if cached:
        assert result.reason == 'silent_200_empty_content'
    else:
        assert result.reason == 'transient_empty_text'
