# Korina Agent

Local voice conversation agent: browser UI → faster-whisper STT → LM Studio LLM → Kokoro TTS, running on roggoz (RX 6800 XT / Ryzen 7 5800X).

## Architecture

```
Browser (Korina UI)          Korina Voice Lab (FastAPI :8001)
  │                                │
  │  mic → VAD chunking            │  faster-whisper
  │  ─────────────────────────────►│  /api/transcribe/partial  (live rolling)
  │                                │  /api/transcribe/stream   (end-of-turn SSE)
  │                                │  /api/transcribe          (buffered JSON)
  │                                │
  │  ◄── transcription text ────── │
  │                                │
  │  ─── user transcript ────────► │  /api/chat  →  LM Studio (:1234)
  │                                │          ◄── LLM reply
  │                                │
  │  ◄── reply text ─────────────  │
  │                                │
  │  ─── reply + voice ──────────► │  Kokoro TTS (:8880)
  │                                │  /stream/speech  (SSE PCM chunks)
  │  ◄── audio stream ───────────  │
  │                                │
  └────────── Web Audio playback ──┘
```

## What's included

- `korina_voice_lab.py` — FastAPI server: STT endpoints, chat proxy to LM Studio, settings API.
- `kokoro-streaming-server.py` — Kokoro TTS server: SSE streaming, buffered WAV fallback.
- `Korina/index.html` — Browser UI: live VAD, partial transcription queue, settings modal, Web Audio playback.
- `Korina/start.sh` / `stop.sh` — Service lifecycle.
- `Korina/Ack/` — Short acknowledgement WAV files played while Korina is "thinking".

## Setup on a new machine

```bash
# 1. Clone
git clone https://github.com/rgoza1996/Korina-Agent
cd Korina-Agent

# 2. Python environment (3.10+)
python3 -m venv korina-env
source korina-env/bin/activate
pip install fastapi uvicorn python-multipart soundfile numpy torch
pip install faster-whisper

# 3. Kokoro TTS
pip install kokoro-onnx pydub
# Download voice manifests / models separately (see Kokoro docs)

# 4. LM Studio running locally on :1234 with your model loaded

# 5. Start
./Korina/start.sh
# Open http://hostname:8001
```

## Default ports

| Service        | Port |
|----------------|------|
| Korina UI      | 8001 |
| Kokoro TTS     | 8880 |
| LM Studio LLM  | 1234 |

## STT backends

Korina supports two speech-to-text backends via the Settings modal:

1. **faster-whisper** (default, stable) — CTranslate2-based Whisper inference, CPU or CUDA.
   - Models: `tiny.en`, `base.en`, `small.en`, `turbo`, `distil-large-v3`
   - Recommended for CPU: `base.en` (~0.6s warm / 1.8s audio clip) or `tiny.en` (~0.4s warm)
2. **LM Studio multimodal LLM** (experimental) — Routes audio to a loaded multimodal/audio LLM via OpenAI-compatible `/v1/chat/completions` with `input_audio` content blocks. Depends on model and LM Studio build support.

## Voice activity detection

Korina uses a browser-side energy VAD (root mean square of audio buffer) with configurable silence threshold. Endpointing modes:

- **Reading/dictation**: 3200ms silence before finalizing a turn. Pauses inside sentences don't cut you off.
- **Conversation**: 950ms silence — quicker back-and-forth.

Partial transcription windows (~1.8s each) are queued rather than dropped, so slow CPU inference catches up gracefully.

## TTS

Kokoro TTS with Web Audio SSE streaming. Voices selectable in the UI. Ack phrases pre-loaded and scheduled before the LLM reply to avoid overlap.

## Branching

- `master` — stable, production-ready snapshots (default branch)
- `beta` — release candidates (debugging branch)
- `alpha` — active development (working branch)

Do all new work on `alpha`.
