# Korina Agent

Korina is a local voice workspace: speak in the browser, transcribe with Whisper or an audio-capable model, get a response from an OpenAI-compatible endpoint, and hear it through Kokoro. A separate agent loop receives conversation context and returns reports, injections, or permission requests.

**Hackathon work:** [`codex/hackathon-refactor`](https://github.com/rgoza1996/Korina-Agent/tree/codex/hackathon-refactor), based on `alpha`. Track verification in [#23](https://github.com/rgoza1996/Korina-Agent/issues/23). `master` remains the v0.0.1 release snapshot. See the [demo guide](docs/hackathon.md) and [current architecture](docs/wiki/Architecture.md).

## Run from a checkout

Use Python 3.10+ and install the system `ffmpeg` executable. Node 22 is needed only for frontend development checks. The UI uses native ES modules; it has no build step.

```bash
git clone https://github.com/rgoza1996/Korina-Agent.git
cd Korina-Agent
git switch codex/hackathon-refactor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[whisper]'
python -m korina.app
```

Open `http://localhost:8001`. The first launch creates `Korina/config.json` from the tracked example. In Settings, set the response provider URL and select a model exposed by that endpoint. The default URLs are `http://127.0.0.1:8080/v1` for the LLM and `http://127.0.0.1:8880` for TTS. Model weights and external LLM servers are installed separately; Korina does not download an LLM for you.

For a CPU-only Linux demo, install CPU Torch explicitly before the Kokoro extra. Also install the system `espeak-ng` package for phonemizer fallback. In a second terminal with the same environment:

```bash
python -m pip install 'torch>=2.4,<3' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[tts]'
OMP_NUM_THREADS=4 python kokoro-streaming-server.py
```

For GPU or other platform builds, choose the appropriate Torch distribution from the [official install selector](https://pytorch.org/get-started/locally/) before installing `.[tts]`. Generic PyPI Torch can pull CUDA packages even for a CPU demo.

Kokoro loads its pipeline on first speech. Its dependencies may download model weights at that point; warm them before presenting. Use `stt_backend=whisper`, a CPU Whisper model such as `base.en`, and a working response model for the most reproducible first rehearsal. Multimodal STT requires a provider and model that accept audio; text-only models fall back to Whisper when available. The Whisper extra constrains PyAV below 19 because faster-whisper 1.2.1 still uses a decoder argument removed upstream; see [the compatibility issue](https://github.com/SYSTRAN/faster-whisper/issues/1589).

Browser microphone access needs localhost or a secure context. For a remote demo, serve the UI over HTTPS and configure the browser-visible TTS URL and allowed origins. The local control API assumes a trusted environment; provider activation can manage local processes. Do not expose it directly to the public internet.

Browser speech uses the Kokoro service protocol: PCM SSE at `/stream/speech` and WAV at `/v1/audio/speech`, relative to the service root. Custom services must support those endpoints, browser CORS, and unauthenticated access. Authenticated cloud TTS is not implemented. Settings includes an optional health URL for compatible custom services.

Compatible services must also obey the browser audio budgets: replies last at most ten minutes, buffered downloads are limited to 32 MiB, and acknowledgement downloads to 2 MiB each. Streaming PCM must use an integer sample rate between 3,000 and 384,000 Hz; the usual 8–48 kHz rates are supported. Oversized or overlong audio stops playback with an error instead of retaining more buffers.

## Install a built wheel

```bash
python -m pip install '.[dev]'
python -m build
python -m pip install dist/*.whl
korina
```

Wheels include the frontend, example configuration, and acknowledgement manifest. Generated state lives under `${XDG_DATA_HOME:-~/.local/share}/korina`, so the installed package can be read-only. Editable/source installs keep generated state alongside the tracked `Korina/` assets. `KORINA_APP_DIR` overrides both with a complete runtime tree.

## Check the stack

```bash
python scripts/check_stack.py
```

`/api/health` reports backend liveness and individual provider states. `/api/ready` returns HTTP 200 only when required voice components are ready, or HTTP 503 with a check map. A lazy, reachable Kokoro server counts as available; custom TTS remains unverified until `tts_health_url` is configured. Model warm-up is still part of rehearsal. `--allow-degraded` on the smoke command verifies the HTTP/assets layer without requiring models.

Health also publishes read-only `audio_limits` with the effective decoded duration and upload budget. Invalid audio-limit environment settings appear as `audio_limits_error` and fail readiness while liveness remains available.

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check korina
npm ci --ignore-scripts
npm run lint
npm test
```

See [observed verification](docs/verification.md) and [testing](docs/testing.md) for CI, coverage, wheel checks, and live rehearsal limits.

## Configuration and portable paths

`GET/POST /api/config` loads and saves settings. Provider activation is explicit through `/api/llm/provider/activate`; saving settings alone does not manage processes. Independent STT and agent endpoint choices are preserved. `Korina/config/config.example.json` is tracked; runtime `config.json`, generated WAVs, and model weights are ignored. API keys should come from environment variables named by `llm_api_key_env` and `stt_llm_api_key_env`. Blank STT or agent credentials inherit the response key only when their normalized API base URL matches the response endpoint. An independent endpoint needs its own explicit credentials. `agent_api_key` is a local development convenience stored in runtime config. Keep credentials out of commits.

| Setting | Purpose |
| --- | --- |
| `KORINA_HOST`, `KORINA_PORT` | API bind address and port; defaults `127.0.0.1:8001` |
| `KORINA_APP_DIR` | Complete UI/runtime tree override |
| `KORINA_LOCAL_MODEL_ROOTS` | Model search directories, separated by the OS path separator |
| `KORINA_LMSTUDIO_HUB_ROOT` | LM Studio catalog directory |
| `KORINA_LLAMA_SERVER_BIN` | `llama-server` executable; otherwise discovered on `PATH` |
| `KORINA_LMSTUDIO_BIN`, `KORINA_LMS_CLI_BIN` | LM Studio GUI/CLI overrides |
| `KORINA_LLAMA_SERVER_USER_UNIT` | Generated user unit location |
| `KORINA_LLAMA_SERVER_MEDIA_PATH` | Optional directory exposed to llama.cpp media loading |
| `KORINA_LLM_HOST` | Managed LLM bind address; default `127.0.0.1` |
| `KORINA_PYTHON`, `KORINA_TTS_PYTHON` | Interpreters for installed user services |
| `KORINA_TTS_HOST`, `KORINA_TTS_PORT` | Kokoro bind address and port; defaults `127.0.0.1:8880` |
| `KORINA_MAX_AUDIO_SECONDS` | Decoded transcription duration cap; default 300 seconds, allowed range 1–1800 |
| `KORINA_FFMPEG_TIMEOUT_SECONDS` | Audio conversion deadline; default 60 seconds |
| `KORINA_TTS_ALLOWED_ORIGINS` | Comma-separated browser origins accepted by Kokoro |

Converse CORS origins are configured by `converse.allowed_origins`. Same-origin UI requests need no CORS configuration. Local model discovery combines endpoint model lists, configured GGUF roots, LM Studio catalogs, and capability probes. Provider metadata is served at `/api/capabilities`; see [capabilities](docs/capabilities.md).

## Optional Linux user services

From an activated environment in the source checkout:

```bash
./Korina/install-services.sh
./Korina/start.sh --dry-run
./Korina/start.sh
./Korina/stop.sh
```

The installer renders units with the checkout and interpreter paths. `systemd --user` is optional; foreground Python commands work without it. GUI providers need a desktop session environment. If a user service cannot open LM Studio, import the relevant display variables with `systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY`.

## Code map

- `korina/routes/`: HTTP validation and orchestration.
- `korina/services/`: speech, response LLM, agent reports, model catalog, provider supervision, and ACK cache.
- `korina/agents/` and `korina/converse/`: adapter and channel interfaces plus default bridges.
- `korina/runtime/`: bounded runtime state, transport helpers, and request limits.
- `korina/tts/`: optional Kokoro service, with model imports deferred until inference.
- `Korina/js/`: microphone lifecycle, VAD, speech playback, settings, and active agent delivery.
- `docs/wiki/`: current repository pages; `docs/refactor/` and `docs/plans/` preserve historical implementation notes.

Korina Agent currently reports and requests permission; it does not execute arbitrary tools. Saved future execution settings are not proof of an implemented execution engine. Hermes is a stub adapter. See [third-party notices](THIRD_PARTY_NOTICES.md) and [MIT license](LICENSE).

The settings UI exposes implemented report, delivery, and interruption controls. Historical retry, transport, compaction, and tool settings remain readable in existing configuration files; they are no longer offered as working controls.
