"""Client filenames cannot break decoding or become temporary filesystem names."""
from __future__ import annotations

import json
import shutil
import subprocess
import wave
from types import SimpleNamespace

import pytest


@pytest.fixture(scope='module')
def audio_uploads(tmp_path_factory):
    if shutil.which('ffmpeg') is None:
        pytest.skip('FFmpeg is needed for actual WAV/WebM decoding')
    directory = tmp_path_factory.mktemp('audio-upload-filenames')
    wav = directory / 'source.wav'
    with wave.open(str(wav), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b'\0\0' * 32000)
    webm = directory / 'source.webm'
    subprocess.run([
        'ffmpeg', '-y', '-nostdin', '-hide_banner', '-loglevel', 'error',
        '-i', str(wav), '-c:a', 'libopus', str(webm),
    ], check=True, capture_output=True, timeout=10)
    return {'wav': wav.read_bytes(), 'webm': webm.read_bytes()}


@pytest.mark.parametrize('format_name', ['wav', 'webm'])
@pytest.mark.parametrize('endpoint', ['/api/transcribe', '/api/transcribe/partial', '/api/transcribe/stream'])
def test_long_misleading_filename_decodes_by_content(
    client, monkeypatch, audio_uploads, format_name, endpoint,
):
    from korina.routes import stt
    from korina.services import whisper_service

    conversion = whisper_service.convert_to_16k_wav
    inputs = []

    def convert(src, dst):
        inputs.append(src)
        return conversion(src, dst)

    def inference(wav, **kwargs):
        assert whisper_service.audio_metadata(wav) == {
            'samples': 32000, 'sample_rate': 16000, 'duration': 2.0,
        }
        assert kwargs['vad_filter'] is (not endpoint.endswith('/partial'))
        return iter([SimpleNamespace(text='fixture transcript', start=0, end=2)]), SimpleNamespace(language='en')

    for module in (stt, whisper_service):
        monkeypatch.setattr(module, 'convert_to_16k_wav', convert)
        monkeypatch.setattr(module, 'transcribe_wav_segments', inference)

    suffix = 'a' * 500
    response = client.post(endpoint, params={'device': 'cpu', 'model': 'fixture-model'}, files={
        'audio': ('recording.' + suffix, audio_uploads[format_name], 'audio/' + format_name),
    })
    assert response.status_code == 200
    assert suffix not in response.text
    if endpoint.endswith('/stream'):
        events = [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
                  for block in response.text.strip().split('\n\n')]
        assert [event for event, _ in events] == ['status', 'status', 'segment', 'done']
        result = events[-1][1]
    else:
        result = response.json()
    assert result['text'] == 'fixture transcript'
    assert result['samples'] == 32000
    assert result['sample_rate'] == 16000
    assert result['duration'] == 2.0
    assert len(inputs) == 1
    assert inputs[0].name == 'input.audio'
    assert not inputs[0].parent.exists()
