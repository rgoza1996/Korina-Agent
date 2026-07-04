from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def test_partial_llm_transcribe_uses_whisper_fallback_on_model_not_found(
    client,
    config_module,
    monkeypatch,
):
    """Bug C: when the multimodal LLM rejects the request with a non-audio
    error (e.g. ``model_not_found`` from LM Studio when the user typed a
    file path as a model id), /api/transcribe/partial currently returns
    500 instead of falling back to whisper.

    The right behavior: any multimodal failure degrades gracefully to
    whisper with the LLM error attached as ``warning``. The user always
    gets *some* transcript so live conversation can continue.
    """
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
        "Multimodal STT model '/home/roggoz/.cache/some/path.gguf' rejected audio transcription request: "
        "HTTP 400: "
        "{\"error\":{\"message\":\"Invalid model identifier ... Please specify a valid downloaded model\",\"type\":\"invalid_request_error\",\"param\":\"model\",\"code\":\"model_not_found\"}}"
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
        "/api/transcribe/partial?backend=llm&llm_model=/home/roggoz/.cache/some/path.gguf",
        files={"audio": ("sample.wav", b"fake audio", "audio/wav")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["backend"] == "whisper-fallback"
    assert payload["text"] == "The magic word is blue lantern."
    assert payload["partial"] is True
    assert "model_not_found" in payload.get("warning", "") or "model_not_found" in payload.get("fallback_reason", "")


def test_stream_llm_transcribe_uses_whisper_fallback_on_model_not_found(
    client,
    config_module,
    monkeypatch,
):
    """Same as above for /api/transcribe/stream -- any multimodal failure
    must degrade to whisper with the LLM error attached as warning."""
    from korina.routes import stt

    cfg = config_module.load_config()
    cfg.update({
        "stt_llm_provider": "lmstudio",
        "stt_llm_base_url": "http://127.0.0.1:1234/v1",
        "stt_model": "base.en",
        "audio_unsupported": {},
    })
    config_module.save_config(cfg)

    monkeypatch.setattr(stt, "convert_to_16k_wav", lambda src, wav: wav.write_bytes(b"fake wav"))
    monkeypatch.setattr(stt.sf, "read", lambda wav, dtype="float32": (np.zeros(16000, dtype="float32"), 16000))

    detail = (
        "Multimodal STT model 'm' rejected audio transcription request: "
        "HTTP 400: model_not_found"
    )
    monkeypatch.setattr(
        stt,
        "lmstudio_transcribe_wav",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(detail)),
    )
    seg = SimpleNamespace(start=0.0, end=1.0, text="The magic word is blue lantern.")
    info = SimpleNamespace(language="en", language_probability=1.0)
    monkeypatch.setattr(stt, "transcribe_wav_segments", lambda *a, **k: ([seg], info))

    response = client.post(
        "/api/transcribe/stream?backend=llm&llm_model=m",
        files={"audio": ("sample.wav", b"fake audio", "audio/wav")},
    )

    assert response.status_code == 200, response.text
    assert "event: error" not in response.text
    assert "model_not_found" in response.text
    assert "The magic word is blue lantern." in response.text
    assert "whisper-fallback" in response.text
