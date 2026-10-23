# Korina Agent

Korina is a local voice workspace: speak in the browser, transcribe with Whisper or an audio-capable model, get a response from Meta Model API or another OpenAI-compatible endpoint, and hear it through Kokoro. A separate agent loop receives conversation context and returns reports, injections, or permission requests.

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

Open `http://localhost:8001`. The first launch creates `Korina/config.json` from the tracked example. In Settings, choose the response provider and select a model exposed by its endpoint; generic provider URLs are editable, while named profiles supply their canonical base. The default URLs are `http://127.0.0.1:8080/v1` for the LLM and `http://127.0.0.1:8880` for TTS. Model weights and external LLM servers are installed separately; Korina does not download an LLM for you.

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

## Meta Model API rehearsal

Select **Meta Model API** (`meta`) as the response provider. This named profile uses the fixed base `https://api.meta.ai/v1` and defaults to Bearer authentication from `MODEL_API_KEY` in the API process environment. A configured `llm_api_key_env` overrides that default. Keep the key private, using the service environment instructions below when applicable. A generic/custom provider does not gain Meta credentials merely by using the same hostname.

For a foreground Bash session, stop the API if it is already running, then enter the key without echoing it or putting its value in shell history:

```bash
read -r -s -p 'Meta API key: ' MODEL_API_KEY
printf '\n'
export MODEL_API_KEY
python -m korina.app
```

The running API must inherit the variable when it starts. The Settings credential field expects an environment-variable **name**, not the key itself; leave it blank to use the named Meta profile's default. Installed services use their private EnvironmentFile instead, as described below.

Load models through authenticated discovery and choose an ID actually available to the team; Korina does not hardcode a Meta model selection. The Meta response path omits the local/generic `reasoning` field. **Meta response reasoning effort** and **Meta agent reasoning effort** are independent optional settings: Model default leaves `reasoning_effort` out; minimal, low, medium, high and extra high send the selected value. Defaults remain blank, and changing effort does not activate a provider or reset the agent. Rehearse effort with your selected model and token budget; the controls do not guarantee a latency or quality result. Keep Whisper for STT and Kokoro for TTS. The same named profile is available independently for built-in agent reports, with the existing credential scope; the agent remains optional for this demo.

Set **Response token budget** in response settings for the selected model (1–8192, default 180). The October 23 synthetic `muse-spark-1.3-contributor` rehearsal used **2048** with model-default reasoning and produced useful context-dependent text. Use that as a measured starting point for this model, then rehearse your actual interaction. Meta reasoning shares the output budget; exhaustion is reported as an error, with no automatic increase or retry. Agent reports retain their separate budget.

The **Last response usage** panel shows provider-reported input, output, reasoning and cached-input tokens for the latest dispatched request. Its model, budget and reasoning labels come from that request’s server snapshot. Missing counts remain unavailable; zero remains zero. These observations stay out of speech/history and do not estimate spending or guarantee cache reuse.

The focused hackathon goal is one useful voice interaction using conversational context and a fact retained in the current session. An [authenticated synthetic rehearsal](docs/verification.md#authenticated-meta-effort-rehearsal-at-bc7c8c5) recalled the earlier Cedar/Friday fact through real Whisper, Meta, Kokoro and native browser playback. The latest run selected minimal response effort and low agent effort, completed native playback in 30.999 seconds, and reported 113 cached input tokens on the voice request. It used a generated fake microphone; physical headset and human interruption checks remain. This is one observation, without a general speed or cache-hit guarantee. The API receives the relevant context; this is not durable memory across restarts. All work before kickoff is preparation. Record the actual event starting SHA and later additions in the [demo guide's ledger](docs/hackathon.md).

## Install a built wheel

```bash
python -m pip install '.[dev]'
python -m build
python -m pip install dist/*.whl
korina
```

Wheels include the frontend, example configuration, and acknowledgement manifest. Generated state lives under `${XDG_DATA_HOME:-~/.local/share}/korina`, so the installed package can be read-only. Editable/source installs keep generated state alongside the tracked `Korina/` assets. `KORINA_APP_DIR` overrides both with a complete runtime tree.

The base wheel serves the UI and API; Whisper and Kokoro remain optional dependencies. For voice, install `ffmpeg`, `espeak-ng`, and the appropriate Torch distribution as described above, then install the wheel with speech extras. Use the actual wheel filename if its version differs:

```bash
python -m pip install './dist/korina_agent-0.1.0.dev0-py3-none-any.whl[whisper,tts]'
```

Run `korina` for the app and `korina-tts` in a second terminal with the same environment for Kokoro. The installed TTS launcher needs no source-checkout script. A separately supplied response LLM and model warm-up are still required.

## Check the stack

```bash
python scripts/check_stack.py
```

`/api/health` reports backend liveness and individual provider states. `/api/ready` returns HTTP 200 only when required voice components are ready, or HTTP 503 with a check map. A lazy, reachable Kokoro server counts as available; custom TTS remains unverified until `tts_health_url` is configured. Model warm-up is still part of rehearsal. `--allow-degraded` on the smoke command verifies the HTTP/assets layer without requiring models.

Health also publishes read-only `audio_limits` with the effective decoded duration and upload budget. Invalid audio-limit environment settings appear as `audio_limits_error` and fail readiness while liveness remains available.

Frontend readiness requires every shipped UI filename to be a readable regular file, including all JavaScript modules. A custom `KORINA_APP_DIR` must provide that complete set; its content can differ. A failed frontend check calls for restoring the complete UI. Browser execution and cached-page freshness still require a browser check.

Acknowledgements are optional. A malformed phrase manifest leaves startup and voice readiness available, reports an actionable ACK error with unknown clip counts, and rejects rebuilds before deleting audio. See [ACK recovery](docs/acknowledgements.md) for catalog limits and repair.

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check korina tests scripts build_support.py kokoro-streaming-server.py
npm ci --ignore-scripts
npm run lint
npm test
```

See [observed verification](docs/verification.md) and [testing](docs/testing.md) for CI, coverage, wheel checks, and live rehearsal limits.

## Configuration and portable paths

`GET/POST /api/config` loads and saves settings. Provider activation is explicit through `/api/llm/provider/activate`; saving settings alone does not manage processes. Independent STT and agent endpoint choices are preserved. `Korina/config/config.example.json` is tracked; runtime `config.json`, generated WAVs, and model weights are ignored. API keys should come from environment variables named by `llm_api_key_env` and `stt_llm_api_key_env`, or a named profile's scoped default. Blank STT or agent credentials inherit the response key only when their normalized API base URL matches the response endpoint. An independent Meta agent can use its canonical `MODEL_API_KEY` default; credentials for other independent endpoints must be configured explicitly when needed. `agent_api_key` overrides agent fallback and is a local development convenience stored in runtime config. Keep credentials out of commits.

| Setting | Purpose |
| --- | --- |
| `KORINA_HOST`, `KORINA_PORT` | API bind address and port; defaults `127.0.0.1:8001` |
| `MODEL_API_KEY` | Default Bearer key for the named Meta Model API profile at its canonical base |
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

The installer records the checkout, runtime directory, and interpreter paths. Relative `KORINA_APP_DIR` values are anchored to the caller's directory before wrappers change directory or write units; `~` expands and symlink traversal is preserved. It uses the active environment, checkout `.venv`, or `python3`; set `KORINA_PYTHON` and optionally `KORINA_TTS_PYTHON` for separate interpreters. Invalid explicit overrides abort installation.

Other shell exports are not copied into the installed units. API keys named by `llm_api_key_env` or `stt_llm_api_key_env`, the scoped Meta `MODEL_API_KEY` default, Whisper/model-path overrides, and TTS/CORS settings must also exist in the relevant service environment. Create private files for those settings:

```bash
mkdir -p ~/.config/korina
touch ~/.config/korina/app.env ~/.config/korina/tts.env
chmod 600 ~/.config/korina/app.env ~/.config/korina/tts.env
```

Use `NAME=value` lines without `export`. For example:

```ini
# ~/.config/korina/app.env
MODEL_API_KEY=replace-with-your-meta-key
WHISPER_CPU_THREADS=4
```

```ini
# ~/.config/korina/tts.env
OMP_NUM_THREADS=4
KORINA_TTS_ALLOWED_ORIGINS=http://127.0.0.1:8001,http://localhost:8001
```

Meta uses `MODEL_API_KEY` by default; for another provider or an explicit override, set its key variable name in Settings to match the app file. Run `systemctl --user edit korina-voice-lab.service` and add:

```ini
[Service]
EnvironmentFile=%h/.config/korina/app.env
```

Run `systemctl --user edit kokoro-streaming-server.service` and add:

```ini
[Service]
EnvironmentFile=%h/.config/korina/tts.env
```

Apply the changes with `systemctl --user daemon-reload` and `systemctl --user restart korina-voice-lab.service kokoro-streaming-server.service`, then run the read-only stack check. Keep secrets outside Git. These files configure the service processes; the source-checkout shell wrappers still use their caller's environment for provider activation.

Bundled units use `127.0.0.1:8001` for Korina and `127.0.0.1:8880` for Kokoro. Foreground `KORINA_HOST`/`KORINA_PORT` and TTS bind overrides do not reconfigure installed units. Startup requires the relevant installed units and returns an error if their start command or TCP wait fails. For manually customized unit ports, use `--no-wait` and check their actual URL with `scripts/check_stack.py --base URL`.

Shutdown stops eligible user units before provider-process fallback to prevent supervised workers from respawning. It attempts the remaining cleanup after a failure and returns nonzero for failed commands or unconfirmed process exits. Missing units are skipped; `--dry-run` performs no stops.

`systemd --user` is optional; foreground Python commands work without it. GUI providers need a desktop session environment. If a user service cannot open LM Studio, import the relevant display variables with `systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XAUTHORITY`, then restart `korina-voice-lab.service` before trying GUI activation again. The running service retains its old inherited environment until restarted.

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
