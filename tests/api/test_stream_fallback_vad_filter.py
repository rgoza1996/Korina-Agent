from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def _sse_events(text: str):
    events = []
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


def test_stream_fallback_uses_vad_filter_false_after_multimodal_failure(
    client,
    config_module,
    monkeypatch,
):
    """Bug A: /api/transcribe/stream fallback returned empty text because
    _whisper_fallback_payload called faster-whisper with vad_filter=True,
    which strips low-energy audio (and real speech clipped at the end) to
    silence, producing 0 segments.

    Fix: the multimodal-fallback path must use vad_filter=False (matching
    /api/transcribe/partial which works correctly).
    """
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
        "Multimodal STT model 'x' rejected audio transcription request: "
        "HTTP 400: audio input is not supported by this model"
    )
    monkeypatch.setattr(
        stt,
        "lmstudio_transcribe_wav",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(detail)),
    )

    captured_kwargs = {}

    def fake_segments(*args, **kwargs):
        captured_kwargs.update(kwargs)
        seg = SimpleNamespace(start=0.0, end=1.0, text="The magic word is blue lantern.")
        info = SimpleNamespace(language="en", language_probability=1.0)
        return [seg], info

    monkeypatch.setattr(stt, "transcribe_wav_segments", fake_segments)

    response = client.post(
        "/api/transcribe/stream?backend=llm&llm_model=x",
        files={"audio": ("sample.wav", b"fake audio", "audio/wav")},
    )

    assert response.status_code == 200
    assert "event: error" not in response.text
    # The fallback must have asked for vad_filter=False so the returned
    # segments include the actual transcript instead of being filtered to
    # silence.
    assert captured_kwargs.get("vad_filter") is False, (
        f"expected vad_filter=False in multimodal-fallback, got {captured_kwargs.get('vad_filter')!r}"
    )
    events = _sse_events(response.text)
    assert any(ev["event"] == "done" and "The magic word is blue lantern." in ev["data"] for ev in events), (
        f"stream SSE done event did not contain whisper-fallback transcript. events={events}"
    )
