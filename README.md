# Korina Agent

Korina Agent is a local-first agent stack built for voice-first work through Korina Converse. Korina Converse is the local-first design chat channel: browser voice UI → STT → response LLM → TTS, with a side channel that can host agents such as the custom Korina Agent, Hermes Agent, OpenClaw, or another OpenAI-compatible/local agent.

## Architecture

```
Browser (Korina Converse UI) Korina backend (FastAPI :8001)
  │                                │
  │  mic → VAD chunking            │  faster-whisper
  │  ─────────────────────────────►│  /api/transcribe/partial  (live rolling)
  │                                │  /api/transcribe/stream   (end-of-turn SSE)
  │                                │  /api/transcribe          (buffered JSON)
  │                                │
  │  ◄── transcription text ────── │
  │                                │
  │  ─── user transcript ────────► │  /api/chat  →  configured local/OpenAI-compatible LLM
  │                                │          ◄── LLM reply
  │                                │
  │  ◄── reply text ─────────────  │
  │                                │
  │  ─── reply + voice ──────────► │  configured Kokoro/OpenAI-compatible TTS
  │                                │
  │  ◄── audio stream ───────────  │
  │                                │
  │                                │
  │  ─── transcript/events ───────►│  Korina Agent / external agent side channel
  │  ◄── injection/interrupts ─────│  /api/agent/*
  │                                │
  └────────── Web Audio playback ──┘
```

## Converse channel architecture

The chat pipeline above describes the **HTTP round-trip** for a single user turn. Underneath it, since Phase 5 Commit 1, the Converse layer talks to any agent implementation through a stable async protocol rather than reaching into the agent service directly.

```
  Converse layer (consumer code)
        │
        ▼
  korina.converse.registry                # thread-safe in-process registry
        │
        ├── get_active_converse_channel() # returns the active ConverseChannel
        ├── set_active_converse_channel() # swap adapter at runtime
        └── register_converse_channel()   # adapter authors register their impl
        │
        ▼
  ConverseChannel (Protocol)              # the contract between Converse and agents
        │
        ├── KorinaConverseChannel         # default: bridges to agent_gateway()
        │                                 # (Hermes / OpenClaw adapters plug in here)
        └── <future adapter impls>
        │
        ▼
  AgentAdapter gateway                   # korina.agents.gateway.agent_gateway()
        │
        └── KorinaAgentAdapter            # today's in-process adapter
                                          # (LLM loop, tool calls, permissions)
```

Public surface of `korina.converse` (12 exports, committed at `3951bdb`):

- `ConverseChannel` (Protocol) — `send()`, `stream()`, `cancel()`, `health()`, `name`
- `ConverseRequest` / `ConverseResponse` (dataclasses)
- `register_converse_channel()`, `unregister_converse_channel()`
- `get_converse_channel()`, `set_active_converse_channel()`, `get_active_channel_name()`, `list_channels()`
- `KorinaConverseChannel` — default in-process adapter
- `ensure_default_registered()`, `reset_default_registered()`, `reset_for_testing()`

The default `KorinaConverseChannel` is registered in `korina/app_factory.py`'s `_startup()` hook so Converse routes can resolve a channel before serving any traffic.

The chat endpoint (`/api/chat`) is intentionally **not** routed through `ConverseChannel`. It is a one-shot LLM completion and lives independently of the Converse/Agent split; the protocol boundary is for the agent side channel only.

## Phase status

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Backend modularization + provider registry | ✅ shipped on `beta` |
| Phase 2 | Plan/Architect split | ✅ shipped on `beta` |
| Phase 3 | Routes call `agent_gateway()` instead of `state.agent` | ✅ shipped on `alpha` |
| Phase 4 Commit A | `AgentState` migrated out of `korina.runtime` into `korina.agents.state` | ✅ shipped on `alpha` |
| Phase 4 Commit B | `korina.runtime` no longer exports `AgentState` | ✅ shipped on `alpha` |
| Phase 5 Commit 1 | `ConverseChannel` protocol + registry + `KorinaConverseChannel` + 14 tests + `_DEFAULT_REGISTERED` test isolation fix | ✅ shipped on `alpha` (CI green on 3.10/3.11/3.12) |
| Phase 5 Commit 4 | `/api/converse/{channel,channels}` routes + Settings dropdown | ⏸ next |
| Phase 5 Commit 5 | Stub Hermes adapter implementing `ConverseChannel` | ⏸ next |
| Phase 6 | `korina-converse` package split, `pip install korina-converse[hermes\|openclaw]` | ⏸ next |

See also:

- `docs/verdict-converse-agent-boundary.md` — what is and isn't divided yet
- `docs/plans/phase5-converse-channel-protocol.md` — Phase 5 plan
- `docs/refactor/architecture-report.md` — module layout

## What's included

- `korina_voice_lab.py` — 3-line uvicorn entrypoint shim (`from korina.app import main; main()`). The actual FastAPI app is built and configured by `korina/app_factory.py:create_app()`, which composes routers from `korina/routes/` and attaches middleware, the `/Ack` static mount, and the startup hook. See `docs/refactor/architecture-report.md` for the package layout.
- `korina/` — backend Python package:
  - `korina/app.py`, `korina/app_factory.py` — app construction
  - `korina/routes/` — one router per concern (`health`, `config`, `models`, `chat`, `stt`, `acks`, `agent`, `providers`, `capabilities`)
  - `korina/converse/` — Converse channel protocol, registry, and default `KorinaConverseChannel` (Phase 5)
  - `korina/agents/` — `AgentAdapter` Protocol, `agent_gateway()` singleton, `KorinaAgentAdapter` implementation, `AgentState` (Phase 3+ migrated here in Phase 4)
  - `korina/services/` — provider manager, model catalog, whisper service, multimodal STT, response LLM, agent service, ack service
  - `korina/util/presets.py` — `PROVIDER_CAPABILITIES` registry (single source of truth for provider metadata; served at `GET /api/capabilities`; documented in `docs/capabilities.md`)
  - `korina/schemas.py`, `korina/schemas_capabilities.py` — Pydantic request/response models
  - `korina/config.py` — `DEFAULT_CONFIG`, `load_config`, `save_config`, legacy-key migration
  - `korina/runtime/` — `RuntimeState` (gathered module globals, minus the `AgentState` that moved in Phase 4)
- `kokoro-streaming-server.py` — Kokoro TTS server: SSE streaming, buffered WAV fallback. (Separate process; unchanged by the Phase 1 backend modularization.)
- `Korina/index.html` — Browser UI: live VAD, partial transcription queue, settings modal (Converse + Agent tabs), Web Audio playback. Reads provider presets and base-URL editability from `/api/capabilities` via `loadCapabilities()` at boot.
- `Korina/config.json` — runtime test-site settings (gitignored). The tracked example is at `Korina/config/config.example.json`.
- `Korina/start.sh` / `stop.sh` — Service lifecycle.
- `Korina/Ack/ack_phrases.json` — tagged acknowledgement phrase manifest. Generated WAVs are cache files and are ignored by git.
- `tests/` — pytest unit/API/static tests for CI plus `tests/regression_smoke.py` for live server smoke tests. CI tests avoid live model servers; live regression can optionally exercise provider activation, chat, and transcription when local services are up.
- `tests/converse/` — Phase 5 ConverseChannel registry and Protocol tests.
- `docs/wiki/` — repo-backed wiki fallback pages that mirror the architecture overview and issue backlog until the GitHub Wiki repo is initialized.

## Process supervision

Korina-owned long-running services are managed with `systemd --user`:

| Unit | Port | Purpose |
|---|---:|---|
| `korina-voice-lab.service` | 8001 | FastAPI app + browser UI |
| `kokoro-streaming-server.service` | 8880 | Kokoro streaming TTS |

## Default ports

| Service        | Port |
|----------------|------|
| Korina UI      | 8001 |
| Kokoro TTS     | 8880 |
| llama.cpp LLM  | 8080 |
| LM Studio LLM  | 1234 |
| Ollama LLM      | 11434 |

## Configuration

The browser loads and writes settings through:

```text
GET  /api/config
POST /api/config
GET  /api/capabilities   # provider registry; see docs/capabilities.md
```

The backing file is:

```text
Korina/config/config.example.json   # tracked, ships with the repo
Korina/config.json                  # runtime, gitignored, written by the UI
```

Persisted settings include voice, speed, STT backend/model/device, LLM model/base URL, TTS provider/port/base URL/model, endpointing mode, silence duration, final STT mode, and idle ack cadence.

For local TTS, leave `tts_base_url` blank and set `tts_port` (default `8880`). The browser builds:

```text
${location.protocol}//${location.hostname}:${tts_port}
```

For custom or cloud-compatible endpoints, set the full base URL in the settings modal. For cloud and OpenAI-compatible endpoints, the preferred path is config-stored env-var names such as `llm_api_key_env` / `stt_api_key_env` / `stt_llm_api_key_env` so the server reads secrets from the environment. As a local-dev/test convenience only, `Korina/config.json` may also contain an optional `agent_api_key`; do not use this field in production.

## Secrets

API-key storage contract:

- `llm_api_key_env` — env-var name; the server reads the actual key from `os.environ[...]`. **Preferred path for production.**
- `stt_api_key_env` — same pattern, for the STT path.
- `stt_llm_api_key_env` — same pattern, for the multimodal LLM STT path.
- `agent_api_key` — stored directly in `Korina/config.json`. **Local dev / test only.** Do not use in production. The Agent path reads it via `api_key_from_config()` and falls back to `llm_api_key_env`.
- `agent_api_key_env` — not used by the Agent path; the field has been removed from the tracked config.

The `Korina/config.json` runtime file is gitignored. Do not commit API keys to the repo.

## STT backends

Korina supports two speech-to-text backends via the Settings modal:

1. **faster-whisper** (default, stable) — CTranslate2-based Whisper inference, CPU or CUDA.
   - Models: `tiny.en`, `base.en`, `small.en`, `turbo`, `distil-large-v3`
   - Recommended for CPU: `base.en` or `tiny.en`
2. **OpenAI-compatible multimodal LLM** (experimental) — Routes audio to a loaded multimodal/audio model via `/v1/chat/completions` with `input_audio` content blocks. This can be a local llama.cpp/LM Studio-compatible server or another compatible endpoint, depending on model and server support.

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

- **Korina Converse** — the local-first, voice-first design chat channel. It owns live conversation UX: microphone capture, VAD/endpointing, STT/TTS/response-LLM settings, spoken playback, interruptions, and the human-facing permission flow. Converse is intended to be the chat channel for whichever agent is attached: the custom Korina Agent, Hermes Agent, OpenClaw, or another local/OpenAI-compatible agent.
- **Korina Agent** — a local-first agent designed specifically for Korina Converse's voice-input focus. It receives transcript deliveries, maintains compact state, emits background reports, and can ask Converse to inject context or speak important/critical interrupts.

Korina Converse and Korina Agent run as independent loops: Converse stays focused on real-time voice while Agent receives transcript deliveries through `/api/agent/transcript`, works in the background, and emits events through `/api/agent/events`. The frontend polls those events and either stores normal reports as hidden next-reply injections or routes important/critical reports through Converse as voice interrupts. Permission requests are spoken by Converse and answered through `/api/agent/permission-answer`, so Converse remains the human-facing permission UX while Agent remains the background worker.

Agent debugging is visible in the **Korina Agent Debug** panel. It logs transcript deliveries/prompts, queued injection updates, Agent events, and Agent output. The top bar includes **Clear Session**, which clears visible transcript/conversation/debug state, client-side model history, Agent delivery counters/cooldowns, and calls `/api/agent/reset` to clear backend Agent state/events. Agent interrupts and ack phrases are added to the visible conversation transcript as explicit labels (`Korina Agent Interrupt` and `Ack Phrase`). Ack phrase labels are UI-only: they are not stored in model history, and model/Agent contexts are sanitized before delivery. Agent interrupt labels are also excluded from later model/Agent context so interrupts cannot recursively trigger new interrupts. Agent interrupts use a cooldown: while an Agent interrupt is speaking, and until its scheduled audio end plus configurable padding (`agent_interrupt_cooldown_padding_ms`, default 3000ms), new Agent reports are deferred/injected rather than interrupting again. `speakText()` strips transcript labels and `think` blocks before sending text to Kokoro, so Korina should never say "Ack Phrase" or hidden reasoning out loud even if a model emits those strings. Ack phrase playback is suppressed briefly during Agent interrupts so a thinking/idle ack does not overlap an interrupt.

Korina Agent can use a separate vendor-agnostic endpoint from Korina Converse. The Agent tab supports:

- OpenAI-compatible endpoints, including local LM Studio and compatible cloud APIs such as MiniMax-style endpoints.
- Anthropic-style request shape (`system` as a top-level field, no `stream: false`) sent to whatever base URL the user provides. The user pastes a URL into the **Agent API base URL** field; the service does not call the official Anthropic API. To use a real Anthropic endpoint, point this URL at an Anthropic-compatible proxy.
- API-key entry saved locally in `config.json` for test/dev use.
- Model discovery through `/api/agent/models` when the provider exposes a `/models` endpoint.
- The provider dropdown, base-URL default, and base-URL editability are sourced from `/api/capabilities`. See `docs/capabilities.md` for the full contract.

The Agent tab also exposes Pi-style behavior settings for the voice-first agent layer:

- YOLO / autonomous mode.
- Project trust: `ask`, `always`, or `never`.
- Injection mode and follow-up mode: `one-at-a-time` or `all`.
- Thinking level: `off`, `minimal`, `low`, `medium`, `high`, or `xhigh`.
- Auto-compaction and token budgets.
- Hide thinking block, transport, retry settings, HTTP idle timeout, skill commands, and image blocking.

These are persisted as `agent_*` fields in `config.json`. Some currently direct the state-report behavior; others are saved now so the later Pi-compatible execution layer can consume them.

Pi is MIT licensed. See `THIRD_PARTY_NOTICES.md` for preserved attribution and license text.

## Voice activity detection

Korina uses a browser-side adaptive energy VAD (root mean square of audio buffer) with configurable endpointing.

- **Reading/dictation**: 3200ms silence before finalizing a turn.
- **Conversation**: 950ms silence.

Partial transcription windows (~1.8s each) are queued rather than dropped, so slower STT inference catches up gracefully.

## Branching

- `master` — stable, production-ready snapshots
- `beta` — release candidates
- `alpha` — development branch / pre-release work

Recent refactor work has been landing on `beta` as the release-candidate branch. Check `docs/refactor/PROGRESS.md` before starting a multi-step change so the active branch, last verified commit, and live-regression status are explicit.

### Service management

For first launch:

```bash
./Korina/start.sh
```

> Note: `start.sh` is for first-launch; for restarts use `systemctl --user restart korina-voice-lab.service`.

Install/update user units from a source checkout:

```bash
cd /path/to/Korina-Agent
./Korina/install-services.sh
```

Start/restart:

```bash
systemctl --user restart kokoro-streaming-server.service korina-voice-lab.service
```

Compatibility wrappers:

```bash
./Korina/start.sh
./Korina/stop.sh
```

Status and logs:

```bash
systemctl --user status kokoro-streaming-server.service korina-voice-lab.service --no-pager -l
journalctl --user -u korina-voice-lab.service -f
journalctl --user -u kokoro-streaming-server.service -f
```

Boot behavior requires user lingering:

```bash
loginctl show-user "$USER" -p Linger
```

If `Linger=no`, a privileged user can enable boot startup with:

```bash
sudo loginctl enable-linger "$USER"
```

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

# 4. Run an OpenAI-compatible LLM server, e.g. llama.cpp on :8080/v1 or LM Studio on :1234/v1

# 5. Start
./Korina/start.sh
# Open http://hostname:8001
```
