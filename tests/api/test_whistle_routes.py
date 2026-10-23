import json

import pytest

from korina.services import whistle_service, whisper_service
from korina.routes import stt


@pytest.mark.parametrize('endpoint', ['/api/transcribe', '/api/transcribe/partial', '/api/transcribe/stream'])
def test_whistle_uses_shared_decode_without_whisper_or_cloud(client, monkeypatch, endpoint):
    calls = []
    def decode(src, wav):
        calls.append('decode')
        wav.write_bytes(b'bounded decoded audio')
    metadata = {'duration': 1, 'sample_rate': 16000, 'samples': 16000}
    def transcribe(wav, **kwargs):
        assert kwargs['is_current']()
        calls.append('whistle')
        return {**metadata, 'text': 'Whistle speech', 'backend': 'whistle', 'device': 'cpu',
                'model': 'Cactus-Compute/whistle', 'segments': [{'text': 'Whistle speech'}]}
    for module in (stt, whisper_service):
        monkeypatch.setattr(module, 'convert_to_16k_wav', decode)
        monkeypatch.setattr(module, 'audio_metadata', lambda _: metadata)
        monkeypatch.setattr(module, 'transcribe_wav_segments', lambda *a, **k: pytest.fail('unexpected Whisper fallback'))
    monkeypatch.setattr(whistle_service, 'transcribe_wav', transcribe)
    response = client.post(endpoint + '?backend=whistle', files={'audio': ('clip.wav', b'audio', 'audio/wav')})
    assert response.status_code == 200
    if endpoint.endswith('/stream'):
        assert 'event: segment' in response.text
        payload = json.loads(response.text.strip().split('\n\n')[-1].split('data: ')[1])
    else:
        payload = response.json()
    assert payload['backend'] == 'whistle' and payload['text'] == 'Whistle speech'
    assert calls == ['decode', 'whistle']


def test_whistle_readiness_does_not_depend_on_whisper(client, config_module, monkeypatch):
    config_module.update_config_values({'stt_backend': 'whistle'})
    monkeypatch.setattr(whistle_service, 'status', lambda: {'available': True, 'loaded': False})
    assert client.get('/api/health').json()['readiness']['checks']['stt'] is True
    monkeypatch.setattr(whistle_service, 'status', lambda: {'available': False, 'loaded': False})
    assert client.get('/api/health').json()['readiness']['checks']['stt'] is False


@pytest.mark.parametrize('endpoint', ['/api/transcribe', '/api/transcribe/partial'])
def test_whistle_busy_is_retryable(client, monkeypatch, endpoint):
    def busy(*args, **kwargs):
        raise whistle_service.WhistleBusyError('Whistle is busy')
    monkeypatch.setattr(whisper_service, 'transcribe_upload_file', busy)
    monkeypatch.setattr(stt, 'transcribe_upload_file', busy)
    response = client.post(endpoint + '?backend=whistle', files={'audio': ('clip.wav', b'audio')})
    assert response.status_code == 503 and response.headers['retry-after'] == '1'
