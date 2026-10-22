"""Buffered, partial and SSE requests share capability-cache/fallback policy."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from korina.runtime.json_http import HTTPStatusError
from korina.routes import stt
from korina.services import multimodal_stt, whisper_service

ENDPOINTS = ['/api/transcribe', '/api/transcribe/partial', '/api/transcribe/stream']


@pytest.fixture
def policy_runtime(config_module, monkeypatch):
    config_module.save_config({**config_module.load_config(), 'stt_llm_provider': 'openai-compatible',
                              'stt_llm_base_url': 'http://audio.test/v1', 'stt_llm_model': 'configured-model',
                              'audio_unsupported': {}})
    vad = []

    def decode(src, wav):
        wav.write_bytes(b'fixture wav')

    def parts(*args, **kwargs):
        vad.append(kwargs['vad_filter'])
        return [SimpleNamespace(start=0, end=1, text='quiet fallback speech')], SimpleNamespace(language='en')

    for module in (stt, whisper_service):
        monkeypatch.setattr(module, 'convert_to_16k_wav', decode)
        monkeypatch.setattr(module, 'audio_metadata', lambda wav: {'samples': 16000, 'sample_rate': 16000, 'duration': 1})
        monkeypatch.setattr(module, 'transcribe_wav_segments', parts)
    return SimpleNamespace(config=config_module, vad=vad)


def transcribe(client, endpoint, model=None):
    query = '?backend=llm' + (f'&llm_model={model}' if model is not None else '')
    response = client.post(endpoint + query, files={'audio': ('fixture.wav', b'fixture', 'audio/wav')})
    assert response.status_code == 200, response.text
    if endpoint.endswith('/stream'):
        events = [block.splitlines() for block in response.text.strip().split('\n\n')]
        assert events[-1][0] == 'event: done', response.text
        return json.loads(events[-1][1][6:])
    return response.json()


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_cached_configured_model_skips_provider_without_query_override(client, monkeypatch, policy_runtime, endpoint):
    policy_runtime.config.set_audio_unsupported('openai-compatible', 'http://audio.test/v1', 'configured-model', 'fixture-known-unsupported', '')

    def forbidden(*args, **kwargs):
        pytest.fail('a known incompatible model must not receive another audio request')

    monkeypatch.setattr(multimodal_stt, 'request_json', forbidden)
    payload = transcribe(client, endpoint)
    assert payload['text'] == 'quiet fallback speech'
    assert payload['fallback_reason'] == 'fixture-known-unsupported'
    assert payload['backend'] == 'whisper-fallback'
    assert policy_runtime.vad == [False]


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('failure,cached', [('unsupported-415', True), ('unsupported-400', True),
                                          ('empty-length', True), ('server-500', False), ('empty-stop', False)])
def test_all_paths_cache_only_structural_failures(client, monkeypatch, policy_runtime, endpoint, failure, cached):
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs['json_body']['model'])
        if failure.startswith('unsupported'):
            raise HTTPStatusError(415 if failure.endswith('415') else 400, 'audio input is not supported')
        if failure == 'server-500':
            raise HTTPStatusError(500, 'temporary server error')
        return {'choices': [{'message': {'content': ''}, 'finish_reason': 'length' if failure == 'empty-length' else 'stop'}]}

    monkeypatch.setattr(multimodal_stt, 'request_json', request)
    for _ in range(2):
        payload = transcribe(client, endpoint)
        assert payload['text'] == 'quiet fallback speech'
        assert payload['backend'] == 'whisper-fallback'
    assert calls == ['configured-model'] * (1 if cached else 2)
    assert policy_runtime.vad == [False, False]
    assert bool(policy_runtime.config.config_audio_unsupported()) is cached


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_cached_model_does_not_block_a_different_explicit_selection(client, monkeypatch, policy_runtime, endpoint):
    policy_runtime.config.set_audio_unsupported('openai-compatible', 'http://audio.test/v1', 'configured-model', 'fixture-known-unsupported', '')
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs['json_body']['model'])
        return {'choices': [{'message': {'content': 'actual multimodal speech'}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr(multimodal_stt, 'request_json', request)
    payload = transcribe(client, endpoint, 'different-model')
    assert payload['text'] == 'actual multimodal speech'
    assert payload['backend'] == 'multimodal-stt'
    assert calls == ['different-model'] and policy_runtime.vad == []
