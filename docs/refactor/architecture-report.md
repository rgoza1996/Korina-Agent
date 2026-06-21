# Korina Agent — Architecture Report

> Code-grounded review of the repo at `alpha` (commit `355e6bb`). Every claim below references the actual files in the repo, not a mental model.

## Executive summary

Korina is currently a **small but highly coupled voice app** built from:

- **1 monolithic backend**: `korina_voice_lab.py` — 1731 lines
- **1 monolithic frontend**: `Korina/index.html` — 1120 lines of HTML/CSS/JS
- **1 separate TTS microservice**: `kokoro-streaming-server.py` — 284 lines
- thin shell scripts + config/docs

It works because the scope is still manageable, but the architecture is now at the point where **feature velocity is being paid for with coupling risk**.

### My high-level judgment
This is not a bad prototype. It already has real useful structure:
- clear HTTP API boundaries
- persistent config
- provider activation layer
- separate TTS process
- hidden agent/event loop separate from converse loop
- thoughtful VAD/partial queue logic
- fallback behavior for multimodal STT

But it is **past the safe size for "single-file app" architecture**.

The biggest problem is not one bug. It's this:

> **Too many responsibilities are shared between the same few files, with duplicated rules in frontend and backend.**

That creates:
- regressions from innocent changes
- rename drift
- config drift
- UI/backend mismatch
- hidden runtime behavior changes
- hard-to-test stateful logic

If you want Korina to keep growing, the next move should be **modularization before more feature layering**.

---

# 1. Actual project shape

Tracked files in repo:

- `.gitignore`
- `README.md`
- `THIRD_PARTY_NOTICES.md`
- `korina_voice_lab.py`
- `kokoro-streaming-server.py`
- `Korina/index.html`
- `Korina/config.json`
- `Korina/start.sh`
- `Korina/stop.sh`
- `Korina/Ack/ack_phrases.json`

### What's missing
- **No tests**
- **No package/module structure**
- **No typed shared schema layer between frontend/backend**
- **No deployment abstraction**
- **No central state model**
- **No build step**
- **No migration system for config**

So the codebase is effectively a **runtime script bundle**, not yet an app with durable internal boundaries.

---

# 2. Runtime architecture

## 2.1 Main runtime topology

There are **three runtime layers**:

### A. Browser UI
`Korina/index.html`

Responsibilities:
- settings modal
- local UI state
- microphone capture
- browser-side VAD
- partial recording windows
- final turn capture
- barge-in detection
- transcript display
- chat history
- TTS playback
- agent event polling
- provider activation triggers
- config save/load

### B. Voice backend
`korina_voice_lab.py` on port `8001`

Responsibilities:
- serves UI
- persists config
- exposes model lists
- manages LLM provider activation
- manages llama.cpp / LM Studio / Ollama process switching
- performs Whisper STT
- proxies multimodal STT
- proxies response LLM chat
- manages ack phrase generation queue
- manages agent state-report loop/events
- exposes health/status

### C. TTS backend
`kokoro-streaming-server.py` on port `8880`

Responsibilities:
- loads Kokoro pipeline
- streams SSE PCM chunks
- returns buffered WAV
- reports health and voices

---

## 2.2 Conversation data flow

### Standard live conversation path
1. Browser captures mic via `MediaRecorder`
2. Browser VAD decides speech start/end
3. While user is speaking:
   - browser creates partial windows
   - sends to `/api/transcribe/partial`
4. On end-of-turn:
   - browser either uses queued partial text, or
   - sends full blob to `/api/transcribe/stream`
5. Browser sends text to `/api/chat`
6. Backend forwards to configured response LLM
7. Browser receives reply
8. Browser plays TTS via Kokoro `/stream/speech` or `/v1/audio/speech`

### Agent side channel
Separately:
1. Browser slices transcript/history
2. Sends to `/api/agent/transcript`
3. Backend runs state-report generation
4. Backend stores/publishes agent events
5. Browser polls `/api/agent/events`
6. Browser either:
   - injects report into next reply
   - or interrupts voice playback for important/critical events

That separation is actually one of the stronger design decisions in the codebase.

---

# 3. File-by-file architecture

## 3.1 `korina_voice_lab.py`
This is the core monolith.

It currently contains **at least 8 subsystems**:

1. **config system**
2. **provider preset / dependency sync**
3. **provider process orchestration**
4. **model discovery**
5. **ack phrase asset generation**
6. **Whisper STT**
7. **multimodal STT + response chat proxy**
8. **agent event/state-report system**
9. **FastAPI routes for all of the above**

That is too much for one file now.

### Key route surface
- `/`
- `/api/health`
- `/api/config`
- `/api/models`
- `/api/llm/provider/activate`
- `/api/acks`
- `/api/acks/status`
- `/api/acks/rebuild`
- `/api/agent/status`
- `/api/agent/events`
- `/api/agent/transcript`
- `/api/agent/permission-answer`
- `/api/agent/reset`
- `/api/agent/models`
- `/api/agent/state-report`
- `/api/transcribe`
- `/api/transcribe/partial`
- `/api/transcribe/stream`
- `/api/chat`

That's already big enough to justify routers/modules.

---

## 3.2 `Korina/index.html`
This is also a monolith.

It combines:
- full HTML
- full CSS
- full app JS
- settings logic
- API client logic
- audio playback
- VAD
- partial STT queue
- agent UX
- history sanitization
- provider-switching behavior

The frontend is basically a mini SPA without modularization.

### Strong part
The live mode logic is more thoughtful than average:
- adaptive noise floor
- separate partial queue
- idle recalibration
- delayed finalization
- barge-in handling
- ack suppression during interrupts

That's valuable logic and worth preserving.

### Weak part
That logic is buried in a single global-script file with lots of mutable globals.

---

## 3.3 `kokoro-streaming-server.py`
This file is simpler and relatively clean.

Subsystems:
- device normalization
- pipeline caching
- SSE PCM streaming
- full WAV generation
- progressive WAV streaming
- health/voices

This is the most self-contained component in the repo.

### But:
It still has some architecture issues:
- global pipeline cache
- no concurrency strategy around `/tmp/kokoro_stream.wav`
- shared temp path for progressive stream endpoint
- minimal schema validation

---

# 4. State model

## 4.1 Backend state
The backend relies heavily on **module-level mutable globals**.

Examples:
- `_asr_models`
- `_asr_loaded_at_by_device`
- `_asr_device`
- `_asr_compute_type`
- `_ack_queue`
- `_ack_in_progress`
- `_ack_worker_running`
- `_agent_events`
- `_agent_busy`
- `_agent_status`
- `_agent_pending_injections`
- `_agent_last_report`

This is acceptable for a prototype, but it means:
- state is implicit
- testing is hard
- concurrency assumptions are fragile
- restart behavior matters a lot
- route behavior depends on prior route usage

## 4.2 Frontend state
The frontend also uses many global mutable variables:
- recording state
- live state
- partial queue state
- VAD state
- TTS state
- agent state
- ack cache state
- config cache state

This creates similar problems:
- hidden coupling
- order-dependent behavior
- harder debugging
- easy regression surface

---

# 5. Config architecture

## 5.1 What's good
The app does have a coherent persisted config model:
- `DEFAULT_CONFIG`
- `load_config()`
- `save_config()`
- config endpoints
- some backward compatibility logic

That's good.

## 5.2 What's weak
The config layer is doing too many jobs:
- defaults
- migration
- provider inheritance
- backend sync
- UI sync assumptions
- process reload triggers

### Concrete issue: config migration is incomplete
You renamed steering → injection, but the tracked config still contains:

- `agent_busy_delivery_mode: "steer"`

in `Korina/config.json`.

The code still accepts legacy delivery values in some paths:
- `submit_agent_transcript()` accepts `('injection', 'steer')`

So the rename is not fully normalized yet.

### Concrete issue: docs and runtime disagree on secrets
README says API keys are not stored directly and env vars are preferred.

But config schema includes:
- `agent_api_key`

and UI explicitly saves it to config for dev/test use.

That's not necessarily wrong, but it is a **documented architecture inconsistency**.

### Concrete issue: config is both runtime state and repo artifact
`Korina/config.json` is tracked, but also treated as generated runtime state.

That split causes confusion:
- example config?
- live config?
- test config?
- migration target?
- source of truth?

Right now it is doing all of those at once.

---

# 6. Provider/model architecture

## 6.1 Current design
There are really **three provider systems**:

1. **Converse response LLM**
2. **Multimodal STT LLM**
3. **Agent LLM**

And they partially inherit from each other.

That inheritance logic is spread across:
- backend `synchronize_llm_dependents()`
- backend config helper functions
- frontend `effectiveSttLlm*()` functions
- frontend UI enable/disable behavior

This is one of the highest-coupling areas in the app.

## 6.2 Good design choice
The new provider activation endpoint is directionally right:
- click provider
- backend stops others
- backend starts selected one
- backend returns saved/activation result

That's the right abstraction.

## 6.3 Weakness: frontend and backend both know provider rules
Provider presets are duplicated in both places:

Backend:
- `provider_preset_base_url()`

Frontend:
- `PROVIDER_BASE_URL_PRESETS`

Also:
- base URL editability logic is frontend-only
- dependent synchronization is backend-only
- model label prettifying exists in both frontend and backend

That duplication guarantees drift over time.

## 6.4 Concrete finding related to the model dropdown issue
This is important.

### Why llama.cpp `/v1/models` doesn't show all models
From the code:

- `llm_models_for()` hits `/models`
- if base is `http://127.0.0.1:8080/v1`, it appends `discover_local_gguf_models()`
- `discover_local_gguf_models()` only scans `LOCAL_MODEL_ROOTS`
- default `LOCAL_MODEL_ROOTS` is `/home/roggoz/Disks/SN750/models`
- LM Studio catalog manifests are discovered separately by `discover_lmstudio_catalog_models()`
- but the frontend model dropdown uses `llm_models`, not `lmstudio_catalog_models`

So:

> **The code already knows about LM Studio catalog models, but the UI does not consume that list.**

That explains the symptom.

### Meaning
If models exist only under:
`/home/roggoz/.lmstudio/hub/models`

and not as discovered `.gguf` files under `LOCAL_MODEL_ROOTS`,
they won't appear in the main model dropdown even though the backend can see their manifests.

That is a real architecture mismatch.

---

# 7. STT architecture

## 7.1 Strong parts
The STT stack is actually fairly thoughtful.

### Whisper path
- lazy load by model/device
- serialized inference via `_asr_infer_lock`
- ffmpeg normalization to 16k mono WAV
- partial and final endpoints
- partial minimum duration gate
- fallback path from multimodal STT to Whisper when audio unsupported

That's solid for a prototype.

## 7.2 Live partial queue design
This is one of the best parts of the app.

The browser:
- records bounded partial windows
- queues them
- processes sequentially
- does not drop slow inference by default
- can use queued chunks instead of retranscribing full utterance

That matches the user's preference well.

## 7.3 Weakness
The STT architecture is split across browser and backend in a way that's hard to reason about:
- browser decides when to record windows
- backend decides how to interpret them
- browser decides whether final uses chunks or full pass
- backend has no session-level notion of utterance assembly

It works, but the logic boundary is muddy.

## 7.4 Likely root cause of the earlier "live conversation got no response"
This is evidence-backed:

Current tracked config has:
- `stt_backend = "llm"`
- `stt_llm_provider = "llama.cpp"`
- `stt_llm_model = "qwen3.5-2b-uncensored-hauhaucs-aggressive"`

Earlier regression results showed that text-only Qwen path rejected audio input.

So the likely chain was:

1. live conversation used multimodal STT mode
2. selected model was text-only
3. audio transcription request failed or fell back awkwardly
4. response loop never completed as expected

That's not just a one-off bug. It reflects an architecture issue:

> **The app allows STT backend selection independently of model capability, without a capability registry.**

---

# 8. TTS architecture

## 8.1 Strong parts
- separate TTS process
- SSE chunk streaming
- buffered fallback
- per-device lazy load
- browser-side audio scheduling

That separation is good.

## 8.2 Concrete weakness
`kokoro-streaming-server.py` progressive WAV path uses:

- fixed temp file `/tmp/kokoro_stream.wav`

That is unsafe for concurrency.

If two requests hit `/stream/wav`, they can stomp each other.

Even if you rarely use that endpoint, it's an architectural smell.

## 8.3 Another weak point
No request schema. Everything is raw `dict`.

This means:
- no validation
- no typed contract
- poor error surfacing

---

# 9. Agent architecture

## 9.1 Conceptually
The agent layer is designed as a **background state-report generator**, not a full autonomous executor.

That's a good scope choice.

## 9.2 What works well
- separate status/events endpoints
- transcript slicing
- duplicate suppression
- priority classification
- permission-request path
- converse interrupt cooldown logic
- state report not recursively fed back as normal dialogue

That's thoughtful.

## 9.3 Architectural weakness
The agent is still too tightly embedded into the converse app:
- same config file
- same backend process
- same frontend file
- same history sanitization logic
- same provider inheritance path

So although it is conceptually a sidecar, it is not structurally one yet.

---

# 10. Concrete architectural risks / code smells

These are the biggest ones.

## 10.1 Two monoliths
- backend monolith
- frontend monolith

This is the main maintainability risk.

## 10.2 Duplicated domain logic
Examples:
- provider presets in frontend and backend
- model labels in frontend and backend
- inheritance rules in frontend and backend
- naming migration partially in backend, partially in config, partially in UI text

## 10.3 Hardcoded deployment assumptions
Examples:
- `/home/roggoz/Korina`
- `/home/roggoz/Disks/SN750/models`
- `/opt/LM-Studio/lm-studio`
- user systemd path for llama-server
- GUI/Xauthority assumptions

This is okay for roggoz, but it means architecture is really:
> "app + host-specific control plane"
not a portable app.

## 10.4 Global mutable state everywhere
Both frontend and backend.

## 10.5 No tests
This is now a serious issue, not a nice-to-have.

## 10.6 Mixed process supervision strategies
- `start.sh` uses `nohup`
- `llama.cpp` uses user systemd unit
- ollama uses systemd/user fallback
- LM Studio uses GUI launch + pkill

This is operationally inconsistent.

## 10.7 Incomplete migration hygiene
Example:
- steering → injection rename partly normalized, partly legacy

## 10.8 Placeholder/dead features in active config
Examples:
- `stt_cloud_*` persisted but not truly part of stable path
- agent settings saved now for future behavior
- some UI/runtime affordances exist before capability checks exist

That increases confusion surface.

## 10.9 Runtime bug verified in current frontend
The barge-in logic references:
- `SPEECH_THRESHOLD`
- `SILENCE_THRESHOLD`

But the file only defines:
- `VAD_BASE_SPEECH_THRESHOLD`
- `VAD_BASE_SILENCE_THRESHOLD`
- `vadSpeechThreshold`
- `vadSilenceThreshold`

So unless those constants are injected elsewhere, this is a real bug.

### Why it matters
When barge-in path runs, it can throw a `ReferenceError`, or at minimum it is not using the same adaptive thresholds as live VAD.

This is exactly the kind of regression monolithic duplicated logic causes.

---

# 11. What is architecturally strong and worth preserving

Don't throw these away in a refactor:

1. **Adaptive browser VAD**
2. **Queued partial transcription windows**
3. **Choice between chunk-final and full-final STT**
4. **Agent state-report as separate conceptual loop**
5. **Provider activation endpoint**
6. **Model label/display vs stored ID split**
7. **Reasoning toggle split for response vs multimodal STT**
8. **Ack phrase manifest + generated asset model**
9. **Interrupt cooldown / deferred interrupt design**
10. **Whisper fallback when multimodal STT rejects audio**

Those are good product ideas.

---

# 12. Refactor plan (summary)

Full plan lives in `blueprint.md`. Quick version:

### Phase 0 — Stabilization
- Fix barge-in threshold bug
- Finish steering → injection migration
- Resolve config role ambiguity
- Fix doc/behavior mismatch on `agent_api_key`
- Document secret-storage contract
- Display labels in the Agent model dropdown
- Verify on roggoz

### Phase 1 — Backend modularization
- New `korina/` package layout
- Move path/config bootstrapping
- Gather globals into a `RuntimeState` dataclass
- Extract services: whisper, multimodal_stt, response_llm, agent, ack, provider_manager, model_catalog
- Move config-derived getters
- Split FastAPI routes into routers
- Extract Pydantic schemas
- Keep `korina_voice_lab.py` as a backward-compat shim
- Verify

### Phase 2 — Single source of truth
- New `GET /api/capabilities` endpoint
- Frontend reads from `/api/capabilities` instead of hardcoding presets
- Document the provider-switch contract

### Phase 3 — Frontend modularization
- Move CSS to a stylesheet
- Split inline JS into `Korina/js/*.js` modules (no build step)
- Replace focus/pointerdown re-query with open-only fetch
- Verify

### Phase 4 — Capability registry
- Per-model capability metadata
- `/api/models` returns capabilities
- Frontend filters dropdowns by capability
- Provider/model compatibility check on activation
- Verify

### Phase 5 — Process supervision unification
- Decide: keep hybrid, full systemd --user, or s6
- Write unit files if going systemd
- Verify

### Phase 6 — Testing + CI
- pytest scaffold
- Backend tests for config migration, provider resolution, priority classifier, sanitizers
- Frontend smoke tests
- GitHub Actions CI

---

# 13. Recommended implementation order

### Track A — low-risk cleanup
1. fix barge-in threshold bug
2. finish legacy naming/value normalization
3. clean docs/config contradictions
4. clarify model-source semantics

### Track B — backend modularization
5. extract config/services/routes
6. add schemas
7. add provider capability endpoint

### Track C — frontend modularization
8. move JS out of `index.html`
9. centralize state + API client
10. isolate audio/VAD/provider logic

### Track D — reliability
11. add tests
12. unify service supervision

---

# 14. My recommendation on scope

I would **not** do a huge rewrite first.

Best path is:

- **surgical stabilization**
- then **backend split**
- then **frontend split**
- then **capability system**
- then **tests + ops cleanup**

That preserves current behavior while reducing future breakage.

---

# 15. Bottom line

## Current architecture grade
For a prototype that became a real tool:
- **Product design:** good
- **Runtime ideas:** strong
- **Maintainability:** now weak
- **Extensibility:** declining
- **Operational consistency:** mixed
- **Regression resistance:** poor without tests

## The main truth
Korina's problem is **not that the architecture is bad**.

It's that the architecture is **still prototype-shaped while the feature set is no longer prototype-sized**.

That mismatch is what is being felt now.
