"""Regression tests for the silent-empty STT fallback (Phase 5, 2026-06-27).

The user's reported bug: when the multimodal STT model is text-only
(e.g. google/gemma-4-e4b), the upstream call returns HTTP 200 with
empty content and a non-'stop' finish_reason (typically 'length' after
the model spends its budget on reasoning without producing audio-derived
text). The audio-probe previously punted on this case ("200 + empty
content = transient_or_content_failure"), so every utterance repeated
the same ~4-second multimodal round-trip instead of falling back to
Whisper.

These tests lock in:
  1. classify_failure(200, '', finish_reason='length') -> (True, 'silent_200_empty_content')
  2. classify_failure(200, '', finish_reason='')       -> legacy (False, 'transient...')
  3. classify_failure(200, 'some text', finish_reason='length') -> (False, transient)
  4. maybe_fallback_to_whisper persists 'silent_200_empty_content' on first hit
  5. Subsequent calls to maybe_fallback_to_whisper hit the cache and
     skip the multimodal call entirely (verified by whisper_fallback_fn
     being called even with stt_status_code=0 indicating no upstream call)
  6. lmstudio_transcribe_wav now returns finish_reason alongside text
  7. Existing 415/400/422 patterns still classify as audio-unsupported
     (no regression).
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------- classify_failure direct cases ----------


def test_classify_failure_silent_empty_with_length_finish_reason():
    """The exact shape from the user's log: 200 + empty body + 'length' finish_reason."""
    from korina.services.audio_probe import classify_failure
    unsupported, reason = classify_failure(200, '', finish_reason='length')
    assert unsupported is True
    assert reason == 'silent_200_empty_content'


def test_classify_failure_silent_empty_with_tool_calls_finish_reason():
    """finish_reason='tool_calls' with empty content is also silent-empty."""
    from korina.services.audio_probe import classify_failure
    unsupported, _ = classify_failure(200, '', finish_reason='tool_calls')
    assert unsupported is True


def test_classify_failure_silent_empty_with_content_filter_finish_reason():
    from korina.services.audio_probe import classify_failure
    unsupported, _ = classify_failure(200, '', finish_reason='content_filter')
    assert unsupported is True


def test_classify_failure_stop_finish_reason_is_not_silent_empty():
    """finish_reason='stop' means the model finished cleanly, even with empty content
    (could be silence / no speech detected). We don't cache that."""
    from korina.services.audio_probe import classify_failure
    unsupported, reason = classify_failure(200, '', finish_reason='stop')
    assert unsupported is False
    assert reason == 'transient_or_content_failure'


def test_classify_failure_blank_finish_reason_preserves_legacy_behavior():
    """When the caller doesn't pass finish_reason, we preserve the pre-fix
    'transient' classification. This is what test_probe_audio_support_does_not_cache_empty_200
    relies on."""
    from korina.services.audio_probe import classify_failure
    unsupported, reason = classify_failure(200, '')
    assert unsupported is False
    assert reason == 'transient_or_content_failure'


def test_classify_failure_200_with_text_and_length_finish_reason_is_transient():
    """If there IS content but finish_reason is non-stop (e.g. truncated mid-sentence),
    that's a content / length issue, not silent-empty."""
    from korina.services.audio_probe import classify_failure
    unsupported, reason = classify_failure(200, '"some transcript text"', finish_reason='length')
    assert unsupported is False
    assert reason == 'transient_or_content_failure'


def test_classify_failure_415_still_unsupported():
    """Regression: existing 415 path is unchanged."""
    from korina.services.audio_probe import classify_failure
    unsupported, reason = classify_failure(415, 'anything')
    assert unsupported is True
    assert reason == 'http_415_unsupported_media_type'


def test_classify_failure_400_audio_body_still_unsupported():
    from korina.services.audio_probe import classify_failure
    unsupported, _ = classify_failure(400, 'audio input not supported by this model')
    assert unsupported is True


# ---------- maybe_fallback_to_whisper persistence ----------


def test_maybe_fallback_persists_silent_empty_to_audio_unsupported(tmp_path, monkeypatch):
    """First silent-empty hit → classify → set_audio_unsupported → return whisper payload."""
    from korina.services import audio_probe
    from korina import config as korina_config
    captured = {'whisper_calls': 0, 'persist_calls': []}
    cache = {}

    def fake_whisper():
        captured['whisper_calls'] += 1
        return {'text': 'whisper transcript', 'engine': 'whisper'}

    def fake_set(provider, base_url, model, reason, body):
        captured['persist_calls'].append({
            'provider': provider, 'base_url': base_url, 'model': model,
            'reason': reason, 'body': body,
        })
        cache[audio_probe.triple_key(provider, base_url, model)] = {
            'reason': reason, 'since': 'now', 'last_error': body,
        }

    def fake_config_audio_unsupported(c=None):
        return dict(cache)

    with patch.object(korina_config, 'set_audio_unsupported', side_effect=fake_set), \
         patch.object(korina_config, 'config_audio_unsupported', side_effect=fake_config_audio_unsupported):
        used, payload = audio_probe.maybe_fallback_to_whisper(
            provider='llama.cpp',
            base_url='http://127.0.0.1:8080/v1',
            model='google/gemma-4-e4b',
            stt_status_code=200,
            stt_response_body=json.dumps({
                'choices': [{'finish_reason': 'length', 'message': {'content': ''}}],
            }),
            whisper_fallback_fn=fake_whisper,
            finish_reason='length',
        )

    assert used is True
    assert payload['fallback_reason'] == 'silent_200_empty_content'
    assert captured['whisper_calls'] == 1
    assert len(captured['persist_calls']) == 1
    assert captured['persist_calls'][0]['reason'] == 'silent_200_empty_content'
    assert captured['persist_calls'][0]['model'] == 'google/gemma-4-e4b'


def test_maybe_fallback_cache_hit_skips_classification(tmp_path, monkeypatch):
    """Second hit on the same triple should hit the cache and call whisper
    without re-running the multimodal request (stt_status_code=0 sentinel)."""
    from korina.services import audio_probe
    from korina import config as korina_config
    captured = {'whisper_calls': 0, 'classify_calls': 0}

    triple = audio_probe.triple_key('llama.cpp', 'http://127.0.0.1:8080/v1', 'google/gemma-4-e4b')
    seeded_cache = {triple: {
        'reason': 'silent_200_empty_content',
        'since': '2026-06-27T00:00:00Z',
        'last_error': '',
    }}

    def fake_whisper():
        captured['whisper_calls'] += 1
        return {'text': 'whisper transcript', 'engine': 'whisper'}

    def fake_config_audio_unsupported(c=None):
        return dict(seeded_cache)

    def should_not_persist(*a, **kw):
        raise AssertionError('cache hit must not re-persist')

    def counting_classify(*a, **kw):
        captured['classify_calls'] += 1
        return (False, 'should_not_reach')

    with patch.object(korina_config, 'config_audio_unsupported', side_effect=fake_config_audio_unsupported), \
         patch.object(korina_config, 'set_audio_unsupported', side_effect=should_not_persist), \
         patch.object(audio_probe, 'classify_failure', side_effect=counting_classify):
        used, payload = audio_probe.maybe_fallback_to_whisper(
            provider='llama.cpp',
            base_url='http://127.0.0.1:8080/v1',
            model='google/gemma-4-e4b',
            stt_status_code=0,    # <-- cache-only sentinel from the caller
            stt_response_body='',
            whisper_fallback_fn=fake_whisper,
            finish_reason='',      # <-- empty because caller knows cache is sufficient
        )

    assert used is True
    assert payload['fallback_reason'] == 'silent_200_empty_content'
    assert payload['audio_unsupported_since'] == '2026-06-27T00:00:00Z'
    assert captured['whisper_calls'] == 1
    assert captured['classify_calls'] == 0, 'classify_failure must not run on cache hit'


def test_maybe_fallback_legacy_empty_text_no_finish_reason_is_transient():
    """If finish_reason is unknown (legacy caller path) and the response is empty,
    we keep the pre-fix transient classification so this isn't a behavioral change."""
    from korina.services import audio_probe
    from korina import config as korina_config

    captured = {'persist_calls': 0}

    def fake_whisper():
        return {'text': 'whisper transcript', 'engine': 'whisper'}

    def fake_config_audio_unsupported(c=None):
        return {}

    def counting_persist(*a, **kw):
        captured['persist_calls'] += 1

    with patch.object(korina_config, 'config_audio_unsupported', side_effect=fake_config_audio_unsupported), \
         patch.object(korina_config, 'set_audio_unsupported', side_effect=counting_persist):
        used, payload = audio_probe.maybe_fallback_to_whisper(
            provider='llama.cpp',
            base_url='http://127.0.0.1:8080/v1',
            model='some/model',
            stt_status_code=200,
            stt_response_body='empty_text',
            whisper_fallback_fn=fake_whisper,
            finish_reason='',     # <-- unknown / legacy
        )

    assert used is False, 'must NOT cache when finish_reason is unknown'
    assert captured['persist_calls'] == 0


# ---------- lmstudio_transcribe_wav returns finish_reason ----------


def test_lmstudio_transcribe_wav_returns_finish_reason_in_result(monkeypatch):
    """The new contract: lmstudio_transcribe_wav now includes finish_reason
    in the returned dict so the audio-probe / stt route can detect silent-empty."""
    from korina.services import multimodal_stt

    fake_response_body = {
        'choices': [{
            'finish_reason': 'length',
            'message': {'role': 'assistant', 'content': ''},
        }],
        'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
    }

    class FakeResp:
        def __init__(self, body):
            self._body = body
        def read(self):
            return json.dumps(self._body).encode('utf-8')
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    # Patch urlopen + the wav.read_bytes + config so the call doesn't touch disk/network
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: FakeResp(fake_response_body))
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_chat_url', lambda c: 'http://127.0.0.1:8080/v1/chat/completions')
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_api_env', lambda c: '')
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_model', lambda c: 'google/gemma-4-e4b')
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_reasoning', lambda c: 'on')

    class FakeWav:
        def __init__(self, _path):
            self._path = Path(_path)
        def read_bytes(self):
            return bfake-wav-bytes

    result = multimodal_stt.lmstudio_transcribe_wav(
        FakeWav('/tmp/fake.wav'),
        model='google/gemma-4-e4b',
        base_url='http://127.0.0.1:8080/v1/chat/completions',
    )

    assert 'finish_reason' in result, 'lmstudio_transcribe_wav must return finish_reason'
    assert result['finish_reason'] == 'length'
    assert result['text'] == ''
    assert result['backend'] == 'multimodal-stt'


def test_lmstudio_transcribe_wav_stop_finish_reason():
    """When the model stops cleanly, finish_reason='stop' is captured."""
    from korina.services import multimodal_stt

    fake_response_body = {
        'choices': [{
            'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': 'hello world'},
        }],
    }

    class FakeResp:
        def read(self):
            return json.dumps(self._body).encode('utf-8')
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def __init__(self, body):
            self._body = body

    monkeypatch = __import__('pytest').MonkeyPatch()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: FakeResp(fake_response_body))
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_chat_url', lambda c: 'http://127.0.0.1:8080/v1/chat/completions')
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_api_env', lambda c: '')
    monkeypatch.setattr(multimodal_stt, 'config_stt_llm_model', lambda c: 'good-model')
    class FakeWav:
        def __init__(self, _path):
            self._path = Path(_path)
        def read_bytes(self):
            return bfake
        def read_bytes(self_inner):
            return b'fake'

    result = multimodal_stt.lmstudio_transcribe_wav(
        FakeWav('/tmp/fake.wav'), model='good-model',
    )
    assert result['finish_reason'] == 'stop'
    assert result['text'] == 'hello world'
    monkeypatch.undo()