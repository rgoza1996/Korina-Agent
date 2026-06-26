from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def _sse_events(text: str):
    """Parse the simple SSE shape emitted by korina.services.whisper_service.sse_event."""
    events = []
    current = {}
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        ev = {"event": "message", "data": ""}
        for line in block.splitlines():
            if line.startswith("event: "):
                ev["event"] = line[len("event: "):]
            elif line.startswith("data: "):
                ev["data"] += line[len("data: "):]
        events.append(ev)
    return events


def test_streaming_llm_transcribe_uses_whisper_fallback_when_audio_unsupported_cached(
    client,
    config_module,
    monkeypatch,
):
    """Live conversation uses /api/transcribe/stream, not /api/transcribe.

    If a multimodal-STT triple is already cached as audio-unsupported, the
    streaming route must skip the LLM call and emit a normal Whisper fallback
    transcript instead of crashing with NameError from the missing fallback
    helper.
    """
    from korina.config import audio_unsupported_key
    from korina.routes import stt

    cfg = config_module.load_config()
    cfg.update({
        "stt_llm_provider": "lmstudio",
        "stt_llm_base_url": "http://127.0.0.1:1234/v1",
        "stt_model": "base.en",
        "audio_unsupported": {
            audio_unsupported_key(
                "lmstudio",
                "http://127.0.0.1:1234/v1",
                "bad-audio-model",
            ): {
                "reason": "body_match:audio.*invalid",
                "since": "2026-06-26T00:00:00+00:00",
                "last_error": "audio invalid",
            }
        },
    })
    config_module.save_config(cfg)

    monkeypatch.setattr(stt, "convert_to_16k_wav", lambda src, wav: wav.write_bytes(b"fake wav"))
    monkeypatch.setattr(stt.sf, "read", lambda wav, dtype="float32": (np.zeros(16000, dtype="float32"), 16000))

    seg = SimpleNamespace(start=0.0, end=1.0, text="The magic word is blue lantern.")
    info = SimpleNamespace(language="en", language_probability=1.0)
    monkeypatch.setattr(stt, "transcribe_wav_segments", lambda *a, **k: ([seg], info))
    monkeypatch.setattr(
        stt,
        "lmstudio_transcribe_wav",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached fallback must not call multimodal LLM")),
    )

    response = client.post(
        "/api/transcribe/stream?backend=llm&llm_model=bad-audio-model",
        files={"audio": ("sample.wav", b"fake audio", "audio/wav")},
    )

    assert response.status_code == 200
    assert "name '_whisper_fallback_payload' is not defined" not in response.text
    assert "event: error" not in response.text
    assert "The magic word is blue lantern." in response.text
    assert "whisper-fallback" in response.text

    events = _sse_events(response.text)
    assert any(ev["event"] == "segment" for ev in events)
    assert any(ev["event"] == "done" and "The magic word is blue lantern." in ev["data"] for ev in events)



def test_streaming_llm_transcribe_uses_whisper_fallback_when_model_load_fails(
    client,
    config_module,
    monkeypatch,
):
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
        "Multimodal STT model 'bad-load-model' rejected audio transcription request: "
        'HTTP 400: {"error":{"message":"Failed to load model \"bad-load-model\". Error: Failed to load model."}}'
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
        "/api/transcribe/stream?backend=llm&llm_model=bad-load-model",
        files={"audio": ("sample.wav", b"fake audio", "audio/wav")},
    )

    assert response.status_code == 200
    assert "event: error" not in response.text
    assert "The magic word is blue lantern." in response.text
    assert "whisper-fallback" in response.text
    assert "model_load_failed" in response.text
