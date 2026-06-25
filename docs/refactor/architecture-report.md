# Korina Agent — Architecture Report

Updated: 2026-06-25 10:20 PDT

Branch/snapshot reviewed: `beta` at `b629409d9faf` (`docs: close out Phase 6 testing and CI`)

## Executive summary

Korina is now two related systems sharing one local-first runtime:

1. **Korina Converse** — the local-first, voice-first design chat channel. It owns microphone capture, browser VAD, endpointing, STT, response-LLM calls, TTS playback, barge-in, interrupts, and the human-facing permission loop. Converse is intended to be a channel that can host multiple agents: the custom Korina Agent, Hermes Agent, OpenClaw, or any compatible local/OpenAI-style agent.
2. **Korina Agent** — the local-first agent designed around that Converse channel. Today it is a background state-report/injection loop: it receives transcript deltas, maintains compact state, classifies report priority, emits events, and asks Converse to inject context or speak important/critical interrupts. It is not yet a full autonomous tool-execution runner.

The architecture has moved substantially since the original report. The old single-file FastAPI monolith and inline frontend script have been split into a Python package, route modules, service modules, ES modules, pytest coverage, and GitHub Actions CI. The remaining work is no longer "create structure"; it is to formalize contracts, finish the agent execution layer, harden local-provider lifecycle/readiness, and make deployment/config paths portable.

## Current grade

**Grade: B / B+.**

Strong points:

- Backend package boundaries are now real: `korina/app.py`, `korina/app_factory.py`, `korina/routes/*`, `korina/services/*`, `korina/runtime/*`.
- Frontend code is modularized under `Korina/js/*` rather than a large inline script.
- Provider metadata has a backend-owned registry (`PROVIDER_CAPABILITIES`) and `/api/capabilities` API.
- Runtime state is gathered under typed dataclasses instead of scattered top-level globals.
- CI now exists and covers unit, API, and static frontend behavior without requiring live local model servers.

Main gaps:

- Korina Agent is still a state-report sidecar, not a complete local-first agent runtime with tool/action execution.
- The Converse ↔ agent protocol is implemented but not yet a formal, versioned contract for third-party agents.
- Local provider lifecycle still has decoupled service-readiness edge cases.
- Deployment paths/model roots are still environment-specific in parts of the code and operational docs.
- The live regression tier exists but is not automated in CI because it requires local services and model weights.

## Repository shape

Measured from the reviewed checkout:

| Area | Current shape |
|---|---|
| Backend package | 6 top-level Python files under `korina/` |
| Route modules | 10 route modules under `korina/routes/` plus package init |
| Service modules | 9 service modules under `korina/services/` plus package init |
| Runtime state | `korina/runtime/state.py` with `AsrState`, `AckState`, `AgentState`, `RuntimeState` |
| Frontend | `Korina/index.html`, `Korina/styles.css`, 17 ES modules under `Korina/js/` |
| Tests | pytest unit/API/static tests plus `tests/regression_smoke.py` live smoke |
| CI | `.github/workflows/test.yml` for Python 3.10 / 3.11 / 3.12 |

High-level file map:

```text
korina/
  app.py                 # canonical uvicorn entrypoint
  app_factory.py         # FastAPI app construction, static mounts, router include list
  config.py              # defaults, migration, load/save, derived config getters
  schemas.py             # request/response models for runtime APIs
  schemas_capabilities.py
  routes/                # HTTP routes by concern
  services/              # provider/model/STT/TTS/agent domain logic
  runtime/               # singleton mutable runtime state and HTTP auth helpers

Korina/
  korina_voice_lab.py    # compatibility shim into korina.app.main()
  index.html             # browser shell
  js/*.js                # modular frontend
  styles.css             # static stylesheet
  config/config.example.json
  start.sh / stop.sh / install-services.sh
```

## Runtime topology

```text
Browser / Korina Converse UI
  ├─ mic capture + adaptive VAD
  ├─ partial STT queue
  ├─ response-LLM chat UI/history
  ├─ TTS playback and barge-in
  └─ agent-event polling / injection / voice interrupts
        │
        ▼
FastAPI backend on :8001
  ├─ /api/transcribe*       -> faster-whisper or multimodal LLM STT
  ├─ /api/chat              -> configured response LLM
  ├─ /api/acks*             -> ACK phrase cache/manifest
  ├─ /api/models            -> endpoint + local model catalog
  ├─ /api/capabilities      -> provider registry contract
  ├─ /api/llm/provider/activate -> local provider lifecycle
  └─ /api/agent/*           -> Korina Agent state/event side channel
        │
        ├─ local/OpenAI-compatible LLM service
        ├─ Kokoro/OpenAI-compatible TTS service
        └─ optional external/local agent-compatible endpoint
```

`korina/app_factory.py:create_app()` is the app composition point (`korina/app_factory.py:40-79`). It creates the FastAPI app, adds CORS, mounts `/Ack`, conditionally mounts `/js`, serves `/styles.css`, attaches the ACK startup hook, and includes the route modules.

`korina/app.py:21-24` is now the canonical uvicorn runner. `Korina/korina_voice_lab.py` remains as the compatibility entrypoint used by service wrappers.

## Route surface

The active API surface is split by concern:

| Module | Routes |
|---|---|
| `routes/index.py` | `GET /` |
| `routes/health.py` | `GET /api/health` |
| `routes/config.py` | `GET /api/config`, `POST /api/config` |
| `routes/models.py` | `GET /api/models` |
| `routes/capabilities.py` | `GET /api/capabilities` |
| `routes/providers.py` | `POST /api/llm/provider/activate`, `GET /api/audio-probe`, `DELETE /api/audio-probe` |
| `routes/stt.py` | `POST /api/transcribe`, `POST /api/transcribe/partial`, `POST /api/transcribe/stream` |
| `routes/chat.py` | `POST /api/chat` |
| `routes/acks.py` | `GET /api/acks`, `GET /api/acks/status`, `POST /api/acks/rebuild` |
| `routes/agent.py` | `GET /api/agent/status`, `GET /api/agent/events`, `POST /api/agent/transcript`, `POST /api/agent/permission-answer`, `POST /api/agent/reset`, `GET /api/agent/models`, `POST /api/agent/state-report` |

This is a much healthier shape than the original monolith. The main caveat is that route modules are still thin wrappers around service functions that share the singleton runtime state; there is not yet dependency injection or per-session isolation.

## Backend services

| Service module | Responsibility |
|---|---|
| `ack_service.py` | ACK manifest loading, WAV cache naming/status, missing-ACK queue, worker, rebuild/clear behavior |
| `agent_service.py` | Korina Agent state-report generation, sanitization, priority classification, event queue, transcript job submission, model listing |
| `audio_probe.py` | Audio-model failure classification and fallback cache |
| `model_capability.py` | Per-model audio capability classification |
| `model_catalog.py` | Local GGUF discovery, LM Studio catalog discovery, mmproj pairing, `/models` merging |
| `multimodal_stt.py` | OpenAI-compatible audio-input STT request path |
| `provider_manager.py` | Local provider start/stop/activation for llama.cpp, LM Studio, and Ollama-style providers |
| `response_llm.py` | Response-LLM chat request and voice-reply formatting |
| `whisper_service.py` | faster-whisper loading, device normalization, upload/stream transcription, SSE helpers |

The backend split is mostly by runtime concern, which is the right direction. The next design improvement should be contract boundaries: define service interfaces for "agent runner", "provider lifecycle", and "model catalog" so external agent backends and local-provider variants can be plugged in without route-level edits.

## Config and runtime state

`korina/config.py:21-92` contains `DEFAULT_CONFIG` and the canonical config key set. `load_config()` and `save_config()` (`korina/config.py:112-153`) merge persisted config with defaults and normalize legacy values. `synchronize_llm_dependents()` (`korina/config.py:160-193`) keeps the response LLM, STT-LLM, and Agent endpoint/model fields aligned when the selected response provider changes.

The tracked example is `Korina/config/config.example.json`; runtime config is `Korina/config.json` and should be treated as local state. That split is correct. The remaining weakness is that some local-dev conveniences, especially direct `agent_api_key` storage, still coexist with the preferred env-var-secret model. The repo should keep moving toward "env var names in config, actual keys only in environment" for every provider path.

Runtime mutables are centralized in `korina/runtime/state.py:27-79`:

- `AsrState` for model cache/device/inference locks.
- `AckState` for ACK generation queue and cache status.
- `AgentState` for agent events, busy flag, pending injections, report dedupe, and last error.
- `RuntimeState` as the singleton container.

This is an improvement over scattered globals, but it is still process-global state. If Korina later supports multiple users/sessions, this layer will need session keys or an external store.

## Provider and model architecture

Provider metadata is backend-owned in `korina/util/presets.py:30-81`. The current provider split is:

- Response LLM providers: `llama.cpp`, `lmstudio`, `ollama`, `openai-compatible`.
- Agent providers: `openai-compatible`, `anthropic`.
- Local providers have default localhost-style base URLs and `manageable: true` where the backend can start/stop them.
- Cloud/custom providers are editable and not lifecycle-managed.

Model discovery is split across:

- Endpoint-loaded models from `/v1/models` (`model_catalog.py:59-81`).
- Local GGUF discovery (`model_catalog.py:21-31`).
- LM Studio catalog manifests (`model_catalog.py:33-45`).
- mmproj sidecar detection (`model_catalog.py:47-52`).
- Static/per-model capability registry plus heuristics (`model_capability.py`, `util/presets.py:130-169`).

This is good enough for the current local-first workflow, but the architecture still has a portability gap: local model roots and service binaries are environment-specific. Those should move behind explicit config/env settings before the repo is treated as cleanly portable.

## Frontend architecture

The frontend is now a modular native-ES-module app:

| Module | Role |
|---|---|
| `app.js` | App boot orchestration |
| `api.js` | Backend calls and capability/model loading |
| `state.js` | Shared frontend state |
| `settings-ui.js` | Settings modal state/apply/collect wiring |
| `providers-ui.js` | Provider/model dropdown behavior |
| `capability-filter.js` | Model capability filtering helpers |
| `live.js`, `recorder.js`, `partial-queue.js`, `vad.js` | Live mic/STT/VAD/partial-transcription flow |
| `speech.js`, `barge-in.js`, `acks.js` | TTS playback, barge-in, ACK phrase behavior |
| `agent-ui.js` | Agent event polling, debug UI, injection/interrupt UX |
| `history.js`, `labels.js`, `dom.js` | Conversation/history/display helpers |

The important architectural win is that Converse owns human-facing voice UX while Agent remains a side-channel producer of state/events. The risk is that this boundary is still encoded mostly in JS functions and route payloads, not in a formal protocol document that another agent runtime could implement independently.

## Korina Converse ↔ Korina Agent boundary

Current behavior:

1. Converse sends transcript/context deltas to `POST /api/agent/transcript`.
2. Agent either queues an injection or starts a background thread (`agent_service.py:241-251`).
3. The worker builds a compact state request, calls the configured agent endpoint, sanitizes output, classifies priority, deduplicates recent reports, and emits events (`agent_service.py:181-236`).
4. Converse polls `GET /api/agent/events` and either:
   - stores a normal/low report as next-reply hidden injection, or
   - routes important/critical/permission reports through the spoken interrupt path.
5. Permission answers flow through `POST /api/agent/permission-answer`.

This is the core idea that makes Korina distinct: the agent does not need to own the voice UI. Converse owns the voice channel, interruption policy, and human permission loop. The agent owns state and suggested action/report content.

The next architecture step is to make this boundary explicit enough that Hermes Agent, OpenClaw, or another local agent can implement it without being coupled to Korina's current Python service internals.

## STT architecture

STT has two paths:

- faster-whisper path: stable local transcription, model/device normalization, cache locks, upload and stream handling (`whisper_service.py`).
- Multimodal LLM path: OpenAI-compatible `input_audio` request flow (`multimodal_stt.py`) with model support mediated by model capability metadata and audio-probe fallback state.

The frontend queues partial transcription windows so slower STT inference can catch up instead of dropping audio. That is the right voice-first design choice. The risk is still around model capability truth: whether a model supports audio input depends on runtime server behavior, local sidecar files, and per-model quirks. The static registry should eventually be supplemented by live probes and persisted probe results.

## TTS and ACK architecture

Kokoro/OpenAI-compatible TTS remains a separate service path. The browser uses Web Audio playback; ACK phrases are manifest-driven and generated/cached per voice. This is a good local-first UX primitive because it lets Converse feel responsive while longer STT/LLM/TTS turns are running.

ACK generation is now queued and statused in `ack_service.py`, and startup generation is isolated enough for pytest to monkeypatch. The remaining architecture issue is operational: TTS health/readiness is not yet expressed as part of a single composite readiness contract for the whole voice stack.

## Process supervision

Korina-owned long-lived services are represented by tracked systemd user units and lifecycle wrappers. `provider_manager.py` can write/restart the llama.cpp user unit for the selected model and waits for `/v1/models` readiness after activation (`provider_manager.py:69-117`).

The known weakness is decoupled service state: the FastAPI process can be healthy while the selected local LLM service is stopped or not yet ready. `/api/health` and `/api/models` expose pieces of that state, but there is no single user-facing "conversation stack ready" contract yet.

## Testing and CI

The Phase 6 state is materially better than the original report:

- `pyproject.toml` defines editable install metadata and `.[test]` extras.
- `.github/workflows/test.yml` runs `pytest -q` on Python 3.10, 3.11, and 3.12.
- `tests/conftest.py` isolates `KORINA_APP_DIR` so pytest does not touch live runtime config.
- Unit tests cover config migration, model catalog/capability helpers, provider manager behavior, audio-probe fallback, response formatting, labels, and agent priority/sanitization.
- API tests use FastAPI `TestClient` for config/capabilities/models/audio-probe/provider activation/validation behavior.
- Frontend static tests cover module shape, fragile UI markers, adaptive barge-in thresholds, and the no-refresh-on-focus regression.
- `tests/regression_smoke.py` remains the live service smoke layer.

CI intentionally does not require live llama.cpp, LM Studio, Kokoro, Whisper weights, GGUF files, or systemd. That split is correct. The remaining gap is an optional self-hosted/live regression tier for the local services.

## Current architecture issues to track

These are the items that should be mirrored into GitHub Issues / wiki/backlog pages.

### ARCH-1 — Formalize the Korina Converse agent protocol

Define a versioned external-agent contract for:

- transcript delivery payloads,
- event stream format,
- injection vs. interrupt semantics,
- permission-request/answer flow,
- priority levels,
- sanitization rules,
- capability discovery for attached agents.

Why: this is what turns Converse from "the UI for the bundled Korina Agent" into a reusable local-first design chat channel for Korina Agent, Hermes Agent, OpenClaw, or other agents.

### ARCH-2 — Finish Korina Agent as a local-first execution runtime

Korina Agent currently generates state reports and events. It needs an execution layer if it is meant to be a real local-first agent:

- tool/action adapters,
- durable task state,
- permission gates before side effects,
- project trust policy enforcement,
- transport abstraction for OpenAI-compatible/Anthropic-compatible/local runners,
- a clean separation between "report to Converse" and "act on the world".

### ARCH-3 — Add composite readiness for the local voice stack

Create one clear readiness contract for the user-facing conversation path:

- FastAPI backend ready,
- selected response LLM reachable/loaded,
- STT backend ready or degraded with a known fallback,
- TTS endpoint ready,
- ACK cache state known,
- frontend static assets served from the current source/runtime sync.

Why: today the backend can report healthy while the selected LLM service is intentionally stopped or still loading.

### ARCH-4 — Make deployment paths, model roots, and local binaries portable

Move hardcoded local runtime/model/binary assumptions behind config/env settings and document a generic install contract. Keep private host paths and hardware specs out of public-facing docs.

### ARCH-5 — Replace static model capability assumptions with probe-backed metadata

The current capability registry is useful, but audio/multimodal support should eventually be confirmed by a probe-backed model metadata layer that records:

- model source,
- sidecar/mmproj presence,
- supports-audio-input result,
- last probe status/error,
- suggested fallback.

### ARCH-6 — Add an optional live/self-hosted regression tier

Keep GitHub Actions lightweight, but add an opt-in live regression job/script that can run on a local/self-hosted runner with real services and model weights. It should publish a concise readiness + smoke report without turning CI red when local services are intentionally offline.

### ARCH-7 — Audit stale public docs after each architecture phase

README is now scrubbed of personal machine specs, but other historical refactor plans intentionally contain host-specific operational notes. Decide which docs are public-facing vs. internal runbooks, then either scrub or clearly mark the internal ones.

## Recommended next implementation order

1. **Docs/protocol:** Write `docs/agent-protocol.md` for the Converse ↔ agent contract.
2. **Agent runtime:** Add a minimal local tool/action execution loop behind the existing permission UX.
3. **Readiness:** Add `/api/readiness` or extend `/api/health` with explicit component readiness and selected-provider state.
4. **Portability:** Move paths/model roots/binaries into env/config with documented defaults.
5. **Model probes:** Persist audio-probe/model-capability observations and use them in dropdown filtering.
6. **Live regression tier:** Add an opt-in self-hosted script/job for the full local stack.

## Bottom line

Korina's architecture is no longer primarily a cleanup problem. The foundation is now serviceable: package split, route split, modular frontend, provider registry, tests, and CI are in place. The next phase should focus on product contracts and local-first agent semantics:

- Converse as the reusable voice/design chat channel.
- Korina Agent as one attached local-first agent built for that channel.
- A formal protocol so Hermes Agent, OpenClaw, or other agents can attach cleanly.
- A readiness/portability layer that makes the local stack predictable outside one machine.
