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
  │  ─── user transcript ────────► │  /api/chat  →  configured OpenAI-compatible LLM
  │                                │          ◄── LLM reply
  │                                │
  │  ◄── reply text ─────────────  │
  │                                │
  │  ─── reply + voice ──────────► │  configured Kokoro/OpenAI-compatible TTS
  │                                │
  │  ◄── audio stream ───────────  │
  │                                │
  └────────── Web Audio playback ──┘
```

## What's included

- `korina_voice_lab.py` — FastAPI server: STT endpoints, chat proxy, settings/config API.
- `kokoro-streaming-server.py` — Kokoro TTS server: SSE streaming, buffered WAV fallback.
- `Korina/index.html` — Browser UI: live VAD, partial transcription queue, settings modal, Web Audio playback.
- `Korina/config.json` — persisted test-site settings.
- `Korina/start.sh` / `stop.sh` — Service lifecycle.
- `Korina/Ack/ack_phrases.json` — tagged acknowledgement phrase manifest. Generated WAVs are cache files and are ignored by git.

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

# 4. Run an OpenAI-compatible LLM server, e.g. LM Studio on :1234

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

## Configuration

The browser loads and writes settings through:

```text
GET  /api/config
POST /api/config
```

The backing file is:

```text
Korina/config.json
```

Persisted settings include voice, speed, STT backend/model/device, LLM model/base URL, TTS provider/port/base URL/model, endpointing mode, silence duration, final STT mode, and idle ack cadence.

For local TTS, leave `tts_base_url` blank and set `tts_port` (default `8880`). The browser builds:

```text
${location.protocol}//${location.hostname}:${tts_port}
```

For custom or cloud-compatible endpoints, set the full base URL in the settings modal. API keys are not stored directly in the UI; config stores optional env-var names such as `llm_api_key_env` / `stt_api_key_env` so the server can read secrets from the environment.

## STT backends

Korina supports two speech-to-text backends via the Settings modal:

1. **faster-whisper** (default, stable) — CTranslate2-based Whisper inference, CPU or CUDA.
   - Models: `tiny.en`, `base.en`, `small.en`, `turbo`, `distil-large-v3`
   - Recommended for CPU: `base.en` or `tiny.en`
2. **LM Studio multimodal LLM** (experimental) — Routes audio to a loaded multimodal/audio LLM via OpenAI-compatible `/v1/chat/completions` with `input_audio` content blocks. Depends on model and server support.

Cloud STT fields are persisted in config for provider experiments, but the stable active STT path is still faster-whisper.

## Ack phrases

Ack phrases are generated audio cache files, not source assets. `Korina/Ack/ack_phrases.json` is the source of truth. Each phrase has:

- `id` — stable phrase id
- `text` — phrase to synthesize
- `tags` — when it can be used, e.g. `global`, `thinking`, `idle`

On boot, Korina checks the manifest for the selected/default voice and queues any missing WAV files for generation through the configured TTS endpoint. `/api/acks?voice=<voice>&tag=<tag>` also queues missing files for that voice/tag and returns currently available audio.

When the UI voice changes, Korina clears the ack WAV cache and queues a fresh set for the new voice. Idle acks use the `idle` tag with cumulative delays: first after 5s of no activity, then 10s after that, then 15s, etc.

## TTS

Kokoro TTS with Web Audio SSE streaming by default. Voices selectable in the UI. TTS endpoint/port/provider fields are configurable from the settings modal and persisted to `config.json`.

## Korina Agent state reports

The settings modal has two tabs:

- **Korina Converse** — live voice conversation, STT/TTS/LLM, endpointing, and provider settings.
- **Korina Agent** — agentic state-report settings.

Korina Agent is a lightweight agentic layer inspired by Pi Agent Harness / Pi Coding Agent concepts. Korina Converse and Korina Agent run as independent loops: Converse stays focused on real-time voice while Agent receives transcript deliveries through `/api/agent/transcript`, works in the background, and emits events through `/api/agent/events`. The frontend polls those events and either stores normal reports as hidden next-reply `/steer` injections or routes important/critical reports through Converse as voice interrupts. Permission requests are spoken by Converse and answered through `/api/agent/permission-answer`, so Converse remains the human-facing permission UX while Agent remains the background worker.

Agent debugging is visible in the **Korina Agent Debug** panel. It logs transcript deliveries/prompts, queued `/steer` updates, Agent events, and Agent output. Agent interrupts and ack phrases are added to the visible conversation transcript as explicit labels (`Korina Agent Interrupt` and `Ack Phrase`). Ack phrase labels are UI-only: they are not stored in model history, and model/Agent contexts are sanitized before delivery. `speakText()` also strips transcript labels before sending text to Kokoro, so Korina should never say “Ack Phrase” out loud even if a model emits the label. Ack phrase playback is suppressed briefly during Agent interrupts so a thinking/idle ack does not overlap an interrupt.

Korina Agent can use a separate vendor-agnostic endpoint from Korina Converse. The Agent tab supports:

- OpenAI-compatible endpoints, including local LM Studio and compatible cloud APIs such as MiniMax-style endpoints.
- Anthropic-compatible `/v1/messages` endpoints.
- API-key entry saved locally in `config.json` for test/dev use.
- Model discovery through `/api/agent/models` when the provider exposes a `/models` endpoint.

The Agent tab also exposes Pi-style behavior settings for the voice-first agent layer:

- YOLO / autonomous mode.
- Project trust: `ask`, `always`, or `never`.
- Steering mode and follow-up mode: `one-at-a-time` or `all`.
- Thinking level: `off`, `minimal`, `low`, `medium`, `high`, or `xhigh`.
- Auto-compaction and token budgets.
- Hide thinking block, transport, retry settings, HTTP idle timeout, skill commands, and image blocking.

These are persisted as `agent_*` fields in `config.json`. Some currently steer the state-report behavior directly; others are saved now so the later Pi-compatible execution layer can consume them.

Pi is MIT licensed. See `THIRD_PARTY_NOTICES.md` for preserved attribution and license text.

## Voice activity detection

Korina uses a browser-side adaptive energy VAD (root mean square of audio buffer) with configurable endpointing.

- **Reading/dictation**: 3200ms silence before finalizing a turn.
- **Conversation**: 950ms silence.

Partial transcription windows (~1.8s each) are queued rather than dropped, so slow CPU inference catches up gracefully.

## Branching

- `master` — stable, production-ready snapshots
- `beta` — release candidates
- `alpha` — active development (default working branch)

Do all new work on `alpha`.
