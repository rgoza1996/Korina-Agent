from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from korina.services import whisper_service as whisper


def test_decode_command_caps_duration_size_and_wall_time(tmp_path, monkeypatch):
    monkeypatch.setenv('KORINA_MAX_AUDIO_SECONDS', '120')
    monkeypatch.setenv('KORINA_FFMPEG_TIMEOUT_SECONDS', '15')
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(whisper.subprocess, 'run', run)
    monkeypatch.setattr(whisper, 'audio_metadata', lambda path: {'duration': 120})
    whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')
    command, options = calls[0]
    assert command[command.index('-t') + 1] == '121.0'
    assert int(command[command.index('-fs') + 1]) < 4_000_000
    assert options['timeout'] == 15
    assert '-nostdin' in command
    assert command[command.index('-map') + 1] == '0:a:0'


def test_decode_rejects_duration_instead_of_returning_truncated_recording(tmp_path, monkeypatch):
    monkeypatch.setenv('KORINA_MAX_AUDIO_SECONDS', '10')
    monkeypatch.setattr(whisper.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(whisper, 'audio_metadata', lambda path: {'duration': 11})
    with pytest.raises(whisper.AudioDurationLimitError, match='10s decoded duration limit'):
        whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')


def test_decode_timeout_and_bounded_error_diagnostics(tmp_path, monkeypatch):
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])

    monkeypatch.setattr(whisper.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError, match='time limit'):
        whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')

    def malformed(command, **kwargs):
        kwargs['stderr'].write(b'x' * 20_000 + b' invalid audio')
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(whisper.subprocess, 'run', malformed)
    with pytest.raises(RuntimeError, match='invalid audio') as error:
        whisper.convert_to_16k_wav(tmp_path / 'source.webm', tmp_path / 'converted.wav')
    assert len(str(error.value)) <= 8192


@pytest.mark.parametrize('name,value', [
    ('KORINA_MAX_AUDIO_SECONDS', '0'),
    ('KORINA_MAX_AUDIO_SECONDS', '1801'),
    ('KORINA_MAX_AUDIO_SECONDS', 'nan'),
    ('KORINA_FFMPEG_TIMEOUT_SECONDS', '301'),
    ('KORINA_FFMPEG_TIMEOUT_SECONDS', 'forever'),
])
def test_decode_limits_reject_unbounded_environment_values(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        whisper.convert_to_16k_wav(tmp_path / 'source.wav', tmp_path / 'converted.wav')


@pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='optional ffmpeg not installed')
def test_tiny_compressed_upload_is_rejected_after_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv('KORINA_MAX_AUDIO_SECONDS', '1')
    source = tmp_path / 'silence.flac'
    sf.write(source, np.zeros(20_000), 16_000, format='FLAC')
    assert source.stat().st_size < 1000
    with pytest.raises(whisper.AudioDurationLimitError):
        whisper.convert_to_16k_wav(source, tmp_path / 'converted.wav')
    # Output remains bounded even if the compressed source describes more audio.
    assert (tmp_path / 'converted.wav').stat().st_size < 100_000


def test_whisper_metadata_uses_request_device_despite_global_state(monkeypatch):
    monkeypatch.setattr(whisper.state.asr, 'device', 'cuda')
    monkeypatch.setattr(whisper.state.asr, 'compute_type', 'float16')
    monkeypatch.setattr(whisper, 'WHISPER_COMPUTE_TYPE', None)
    result = whisper.whisper_payload(
        [], SimpleNamespace(language='en'), metadata={'duration': 1},
        started=0, device='cpu', model_id='base.en',
    )
    assert result['device'] == 'cpu'
    assert result['compute_type'] == 'int8'
    assert result['model'] == 'base.en'


def test_whisper_segments_remain_lazy_and_can_advance_on_different_threads(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    consumed = []

    def segments():
        consumed.append('first')
        yield SimpleNamespace(text='first', start=0, end=1)
        consumed.append('second')
        yield SimpleNamespace(text='second', start=1, end=2)

    asr = SimpleNamespace(transcribe=lambda *args, **kwargs: (segments(), SimpleNamespace(language='en')))
    monkeypatch.setattr(whisper, 'get_asr', lambda *args: asr)
    iterator, _ = whisper.transcribe_wav_segments(Path('fake.wav'))
    assert consumed == []
    with ThreadPoolExecutor(max_workers=1) as first_thread:
        assert first_thread.submit(next, iterator).result(timeout=2).text == 'first'
    assert consumed == ['first']
    with ThreadPoolExecutor(max_workers=1) as second_thread:
        assert second_thread.submit(next, iterator).result(timeout=2).text == 'second'
        second_thread.submit(iterator.close).result(timeout=2)
    assert consumed == ['first', 'second']


def test_payload_uses_captured_inference_metadata_after_environment_changes(monkeypatch):
    info = whisper.WhisperInferenceInfo(
        SimpleNamespace(language='en'), model_id='tiny.en', device='cuda', compute_type='float16',
    )
    monkeypatch.setattr(whisper, 'normalize_device', lambda *args, **kwargs: 'cpu')
    result = whisper.whisper_payload(
        [], info, metadata={'duration': 1}, started=0, device='auto', model_id='different',
    )
    assert result['model'] == 'tiny.en'
    assert result['device'] == 'cuda'
    assert result['compute_type'] == 'float16'
