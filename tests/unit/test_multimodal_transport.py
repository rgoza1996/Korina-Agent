"""Candidate endpoints must not receive another provider's credentials."""
from __future__ import annotations

import io
import json

import pytest

from korina.services import multimodal_stt


@pytest.fixture
def capture_transport(monkeypatch, tmp_path):
    config = {
        'stt_llm_provider': 'openai-compatible',
        'stt_llm_base_url': 'https://configured.test/v1',
        'stt_llm_api_key_env': 'CONFIGURED_STT_KEY',
        'stt_llm_model': 'configured-model',
    }
    monkeypatch.setenv('CONFIGURED_STT_KEY', 'configured-test-secret')
    monkeypatch.setenv('CANDIDATE_KEY', 'candidate-test-secret')
    monkeypatch.setattr(multimodal_stt, 'load_config', lambda: config)
    calls = []

    def urlopen(request, timeout):
        calls.append({'request': request, 'timeout': timeout, 'payload': json.loads(request.data)})
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'hello'}, 'finish_reason': 'stop'}]}).encode())

    monkeypatch.setattr(multimodal_stt.urllib.request, 'urlopen', urlopen)
    wav = tmp_path / 'input.wav'
    wav.write_bytes(b'fake audio')
    return config, wav, calls


def test_empty_api_env_disables_configured_auth_and_timeout_is_forwarded(capture_transport):
    _, wav, calls = capture_transport
    result = multimodal_stt.lmstudio_transcribe_wav(
        wav, model='audio-model', base_url='https://configured.test/v1/chat/completions',
        api_env='', timeout=2.5,
    )
    assert result['text'] == 'hello'
    assert calls[0]['request'].get_header('Authorization') is None
    assert calls[0]['timeout'] == 2.5


@pytest.mark.parametrize('base_url,expected_auth', [
    ('https://configured.test/v1/chat/completions', 'Bearer configured-test-secret'),
    ('https://candidate.test/v1/chat/completions', None),
])
def test_omitted_auth_inherits_only_matching_configured_endpoint(capture_transport, base_url, expected_auth):
    _, wav, calls = capture_transport
    multimodal_stt.lmstudio_transcribe_wav(wav, model='audio-model', base_url=base_url)
    assert calls[0]['request'].get_header('Authorization') == expected_auth


def test_explicit_candidate_env_name_is_used_for_candidate(capture_transport):
    _, wav, calls = capture_transport
    multimodal_stt.lmstudio_transcribe_wav(
        wav, model='audio-model', base_url='https://candidate.test/v1/chat/completions',
        api_env='CANDIDATE_KEY',
    )
    assert calls[0]['request'].get_header('Authorization') == 'Bearer candidate-test-secret'


def test_model_discovery_uses_selected_endpoint_and_auth(capture_transport, monkeypatch):
    config, wav, calls = capture_transport
    config['stt_llm_model'] = ''
    discovered = []
    monkeypatch.setattr(multimodal_stt, 'llm_models_for', lambda base, env: discovered.append((base, env)) or ['candidate-model'])
    multimodal_stt.lmstudio_transcribe_wav(
        wav, base_url='https://candidate.test/custom/chat/completions', api_env='CANDIDATE_KEY',
    )
    assert discovered == [('https://candidate.test/custom', 'CANDIDATE_KEY')]
    assert calls[0]['payload']['model'] == 'candidate-model'


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan'), 121])
def test_runtime_timeout_rejects_unbounded_values(capture_transport, timeout):
    _, wav, calls = capture_transport
    with pytest.raises(ValueError, match='timeout'):
        multimodal_stt.lmstudio_transcribe_wav(wav, model='audio-model', timeout=timeout)
    assert calls == []
