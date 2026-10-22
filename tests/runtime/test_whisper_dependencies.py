"""Exercise optional audio decoding without downloading inference weights."""
from __future__ import annotations

import io
import wave

import pytest


def test_installed_whisper_can_decode_a_real_wav():
    audio = pytest.importorskip('faster_whisper.audio')
    recording = io.BytesIO()
    with wave.open(recording, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 1600)
    recording.seek(0)
    samples = audio.decode_audio(recording, sampling_rate=16000)
    assert samples.shape == (1600,)
    assert not samples.any()
