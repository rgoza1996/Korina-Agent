from __future__ import annotations

import io
import tempfile
import threading
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


def test_default_probe_forwards_timeout_without_leaking_configured_secret(monkeypatch, config_module, mock_provider_http):
    import httpx
    from korina.services import active_probe

    config_module.update_config_values({
        'stt_llm_base_url': 'https://configured.test/v1',
        'stt_llm_provider': 'openai-compatible',
        'stt_llm_api_key_env': 'PROBE_CONFIGURED_KEY',
    })
    monkeypatch.setenv('PROBE_CONFIGURED_KEY', 'configured-secret')
    calls = []

    def request_handler(request):
        calls.append((request, request.extensions['timeout']['read']))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}]})

    mock_provider_http(request_handler)
    result = active_probe.probe_audio_support(
        'openai-compatible', 'https://candidate.test/v1', 'audio-model', timeout=0.25,
    )
    assert result.supported is True
    assert str(calls[0][0].url) == 'https://candidate.test/v1/chat/completions'
    assert calls[0][0].headers.get('Authorization') is None
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
    active_probe.probe_audio_support('openai-compatible', 'https://candidate.test/v1', 'a', api_env='CANDIDATE_ENV', transcribe_fn=legacy_callback)
    active_probe.probe_audio_support('openai-compatible', 'https://candidate.test/v1', 'b', api_env='CANDIDATE_ENV', transcribe_fn=legacy_callback)
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


def test_retired_probe_does_not_create_audio_or_invoke_legacy_callback(monkeypatch, config_module):
    from korina.runtime.json_http import RequestCancelledError
    from korina.services import active_probe

    def unexpected(*args, **kwargs):
        pytest.fail('retired probe must not create audio or call the provider')

    monkeypatch.setattr(active_probe, '_build_synthetic_wav', unexpected)
    with pytest.raises(RequestCancelledError):
        active_probe.probe_audio_support('llama.cpp', 'http://fixture.test/v1', 'model',
                                         transcribe_fn=unexpected, is_current=lambda: False)
    assert config_module.config_audio_unsupported() == {}


@pytest.mark.parametrize('outcome', ['success', 'empty', 'error', 'cancelled'])
def test_probe_retirement_skips_cache_publication_and_cleans_audio(config_module, outcome):
    from korina.runtime.json_http import RequestCancelledError
    from korina.services import active_probe

    provider, base, model = 'llama.cpp', 'http://fixture.test/v1', 'model'
    config_module.set_audio_unsupported(provider, base, model, 'original', 'keep this entry')
    cache_before = config_module.config_audio_unsupported()
    current = [True]
    paths = []
    cancelled = RequestCancelledError('cancelled fixture')

    # The injected callback deliberately keeps its original four-argument API.
    def legacy_callback(wav, *, model, base_url, api_env):
        paths.append(wav)
        assert wav.is_file()
        current[0] = False
        if outcome == 'cancelled':
            raise cancelled
        if outcome == 'error':
            raise RuntimeError('HTTP 415: audio input is not supported')
        return {'text': 'hello' if outcome == 'success' else '', 'finish_reason': 'length'}

    with pytest.raises(RequestCancelledError) as caught:
        active_probe.probe_audio_support(provider, base, model, transcribe_fn=legacy_callback,
                                         is_current=lambda: current[0])
    if outcome == 'cancelled':
        assert caught.value is cancelled
    assert config_module.config_audio_unsupported() == cache_before
    assert len(paths) == 1 and not paths[0].exists()


def test_default_probe_forwards_owner_but_preserves_unowned_calls(monkeypatch, config_module):
    from korina.services import active_probe, multimodal_stt

    calls = []

    def transcribe(wav, *, model, base_url, api_env, timeout, is_current):
        calls.append((timeout, is_current))
        return {'text': 'hello'}

    monkeypatch.setattr(multimodal_stt, 'lmstudio_transcribe_wav', transcribe)
    current = lambda: True
    for predicate in (None, current):
        result = active_probe.probe_audio_support('llama.cpp', 'http://fixture.test/v1', 'model',
                                                  timeout=.25, is_current=predicate)
        assert result.supported is True
    assert calls == [(.25, None), (.25, current)]


@pytest.mark.parametrize('outcome', ['success', 'empty', 'error'])
def test_probe_waiting_for_config_lock_does_not_publish_after_retirement(monkeypatch, config_module, outcome):
    from korina.runtime.json_http import RequestCancelledError
    from korina.services import active_probe

    provider, base, model = 'llama.cpp', 'http://fixture.test/v1', 'model'
    config_module.update_config_values({'audio_unsupported': {}})
    if outcome == 'success':
        config_module.set_audio_unsupported(provider, base, model, 'original', 'keep this entry')
    saved_before = config_module.CONFIG_PATH.read_bytes()
    cache_before = config_module.config_audio_unsupported()
    callback_entered, return_provider, waiting_for_config = (threading.Event() for _ in range(3))
    current = threading.Event()
    current.set()
    owner_thread = threading.get_ident()
    original_lock = config_module._CONFIG_LOCK

    class ObservedLock:
        def __enter__(self):
            if threading.get_ident() != owner_thread and return_provider.is_set():
                waiting_for_config.set()
            original_lock.acquire()
            return self

        def __exit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(config_module, '_CONFIG_LOCK', ObservedLock())
    paths, failures, results = [], [], []

    def legacy_callback(wav, *, model, base_url, api_env):
        paths.append(wav)
        callback_entered.set()
        assert return_provider.wait(3)
        if outcome == 'error':
            raise RuntimeError('HTTP 415: audio input is not supported')
        return {'text': 'hello' if outcome == 'success' else '', 'finish_reason': 'length'}

    def run_probe():
        try:
            results.append(active_probe.probe_audio_support(provider, base, model,
                                                            transcribe_fn=legacy_callback, is_current=current.is_set))
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=run_probe, daemon=True)
    worker.start()
    try:
        assert callback_entered.wait(3)
        with config_module._CONFIG_LOCK:
            return_provider.set()
            assert waiting_for_config.wait(3), 'cache mutation must be waiting on the held config lock'
            current.clear()
        worker.join(3)
        assert not worker.is_alive()
        assert not results
        assert len(failures) == 1 and isinstance(failures[0], RequestCancelledError)
        assert config_module.CONFIG_PATH.read_bytes() == saved_before
        assert config_module.config_audio_unsupported() == cache_before
        assert len(paths) == 1 and not paths[0].exists()
    finally:
        return_provider.set()
        current.clear()
        worker.join(3)


@pytest.mark.parametrize('phase', ['generation', 'write'])
def test_probe_preparation_failure_removes_real_temporary_file(monkeypatch, config_module, tmp_path, phase):
    from pathlib import Path
    from korina.services import active_probe

    config_module.update_config_values({'audio_unsupported': {}})
    saved_before = config_module.CONFIG_PATH.read_bytes()
    named_temporary_file = tempfile.NamedTemporaryFile
    files, paths, written = [], [], []

    def controlled_file(**kwargs):
        output = named_temporary_file(dir=tmp_path, **kwargs)
        files.append(output)
        paths.append(Path(output.name))
        if phase == 'write':
            def fail_write(data):
                written.append(output.file.write(data[:16]))
                raise OSError('controlled temporary WAV write failure')
            output.write = fail_write
        return output

    def fail_generation():
        raise RuntimeError('controlled synthetic WAV generation failure')

    def unexpected_provider(wav, *, model, base_url, api_env):
        pytest.fail('a preparation failure must not call the provider')

    monkeypatch.setattr(tempfile, 'NamedTemporaryFile', controlled_file)
    if phase == 'generation':
        monkeypatch.setattr(active_probe, '_build_synthetic_wav', fail_generation)
    result = active_probe.probe_audio_support('llama.cpp', 'http://fixture.test/v1', 'model',
                                             transcribe_fn=unexpected_provider)
    assert result.supported is False
    assert result.reason == 'transient_runtime_error'
    assert 'controlled' in result.error
    assert len(files) == 1 and files[0].closed
    assert len(paths) == 1 and not paths[0].exists()
    assert list(tmp_path.iterdir()) == []
    assert written == ([16] if phase == 'write' else [])
    assert config_module.CONFIG_PATH.read_bytes() == saved_before
