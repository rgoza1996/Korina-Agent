from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def test_partial_llm_transcribe_uses_whisper_fallback_when_model_load_fails(
    client,
    config_module,
    monkeypatch,
):
    from korina.services import whisper_service

    cfg = config_module.load_config()
    cfg.update({
        "stt_llm_provider": "lmstudio",
        "stt_llm_base_url": "http://127.0.0.1:1234/v1",
        "stt_model": "base.en",
    })
    config_module.save_config(cfg)

    monkeypatch.setattr(whisper_service, "convert_to_16k_wav", lambda src, wav: wav.write_bytes(b"fake wav"))
    monkeypatch.setattr(whisper_service.sf, "read", lambda wav, dtype="float32": (np.zeros(16000, dtype="float32"), 16000))

    detail = (
        "Multimodal STT model 'bad-load-model' rejected audio transcription request: "
        'HTTP 400: {"error":{"message":"Failed to load model \"bad-load-model\". Error: Failed to load model."}}'
    )
    monkeypatch.setattr(
        whisper_service,
        "lmstudio_transcribe_wav",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(detail)),
    )
    seg = SimpleNamespace(start=0.0, end=1.0, text="The magic word is blue lantern.")
    info = SimpleNamespace(language="en", language_probability=1.0)
    monkeypatch.setattr(whisper_service, "transcribe_wav_segments", lambda *a, **k: ([seg], info))

    response = client.post(
        "/api/transcribe/partial?backend=llm&llm_model=bad-load-model",
        files={"audio": ("sample.wav", b"fake audio", "audio/wav")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "whisper-fallback"
    assert payload["text"] == "The magic word is blue lantern."
    assert payload["partial"] is True
    assert payload["stable"] is False
    assert payload["fallback_reason"] == "model_load_failed"
