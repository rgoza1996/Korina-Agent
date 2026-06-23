# Korina Agent Refactor Blueprint

> Companion to `architecture-report.md`. Reads that first for the "why". This document is the "what" and the "in what order".

**Status:** proposal — not yet executed.
**Target branch:** `beta` (refactor work lands on `beta`; `alpha` is the stable branch from before the refactor series started. Per user direction, the refactor series lives on `beta` until release — see `docs/refactor/PROGRESS.md` for what has actually landed and on which branch.)
**Default principle:** **every refactor step is behavior-preserving** unless the step title says otherwise. If a step changes behavior, it ships in its own commit with tests.

---

## 0. How to read this document

Each refactor is a phase. Each phase is broken into **tracks**, each track into **steps**, each step into **subtasks**.

Subtask language is precise:

- **Move** = relocate code to a new module without changing behavior.
- **Extract** = pull a named function/component out, keep all call sites working.
- **Replace** = swap one implementation for another equivalent one.
- **Add** = introduce new code.
- **Remove** = delete code; called out explicitly.
- **Test** = add or update automated coverage.

Any step that needs user input (visual design, copy, host names) says so inline with a `?DECIDE?` marker.

---

## 1. Guiding principles

These apply to every phase. Don't make a refactor decision that violates one of these unless the user explicitly approves it.

1. **Behavior preservation first.** No "while we're at it" cleanup inside a refactor commit. Bundle cleanup commits separately so they can be reverted.
2. **No new runtime dependencies** in Phase 0–1 unless the user approves.
3. **Tracked config is `config.example.json`, runtime config is `config.json` (gitignored).** Resolve the existing contradiction first.
4. **No process supervision changes** until Phase 5. The current `start.sh`/`stop.sh` continue to own process lifecycle through the refactor.
5. **Backend and frontend never share a string literal** for the same domain concept. Either backend returns it, or one of them owns it.
6. **No global mutables in new code.** Phase 1+ modules use a single runtime-state object exposed via a small set of functions.

---

## 2. Phase overview

| # | Phase                          | Risk   | Behavior changes? | Approx. LOC touched |
|---|--------------------------------|--------|-------------------|---------------------|
| 0 | Stabilization                  | low    | bug fixes only    | ~150                |
| 1 | Backend modularization         | medium | none              | ~1700               |
| 2 | Single source of truth         | medium | none (UI new)     | ~400                |
| 3 | Frontend modularization        | medium | none              | ~1100               |
| 4 | Capability registry            | high   | yes (validation)  | ~600                |
| 5 | Process supervision unification| high   | yes (ops)         | ~200                |
| 6 | Testing + CI                   | low    | none              | ~800                |

Each phase ends with a working tree, a green smoke run, and a commit on `beta`.

---

## Phase 0 — Stabilization (bug fixes only, no behavior redesign)

**Goal:** stop the bleeding. Fix concrete, known problems without touching the architecture.

**Risk profile:** low. Each step is reversible. No new abstractions.

### Step 0.1 — Fix the barge-in threshold ReferenceError

**Symptom:** `monitorBargeIn()` and `monitorBargeSpeechEnd()` reference `SPEECH_THRESHOLD` / `SILENCE_THRESHOLD` (4 occurrences each). Those constants are not declared anywhere. The declared names are `VAD_BASE_SPEECH_THRESHOLD` and `VAD_BASE_SILENCE_THRESHOLD` (and the live adaptive values `vadSpeechThreshold` / `vadSilenceThreshold`).

**Subtasks:**

- [ ] **Replace** each `SPEECH_THRESHOLD` with `vadSpeechThreshold`.
- [ ] **Replace** each `SILENCE_THRESHOLD` with `vadSilenceThreshold`.
- [ ] **Test** by triggering barge-in manually: start live, play a reply, speak. Confirm the live monitor's threshold state matches the barge monitor's.
- [ ] **Commit:** `fix: barge-in uses live VAD thresholds`.

### Step 0.2 — Finish the steering → injection normalization

**Symptom:** the rename is partial. `Korina/config.json` still has `"agent_busy_delivery_mode": "steer"`. The runtime also still accepts `"steer"` in `submit_agent_transcript()`.

**Subtasks:**

- [ ] **Add** a config migration in `load_config()` that converts any legacy `"steer"` delivery value to `"injection"`.
- [ ] **Add** an `agent_injection_mode` migration from `agent_steering_mode` (already exists; verify it covers all three modes, not just "all" / "one-at-a-time").
- [ ] **Replace** the legacy-read path in `submit_agent_transcript()` so `"steer"` is mapped to `"injection"` on read, not on write. Keep behavior identical.
- [ ] **Replace** the example config entry `agent_busy_delivery_mode: "steer"` with `agent_busy_delivery_mode: "injection"`.
- [ ] **Search** the repo for `/steer` and `steering` (case insensitive) one more time; any remaining hit is either a documentation reference to "Pi Agent Harness" or a bug.
- [ ] **Commit:** `chore: finish agent_steering → agent_injection migration`.

### Step 0.3 — Resolve the config role ambiguity

**Symptom:** `Korina/config.json` is both tracked and runtime-generated. The repo example and the live file are the same file.

**Decision needed:** keep tracked or untrack it.

**Subtask (default choice; reversible):**

- [ ] **Rename** `Korina/config.json` → `Korina/config.example.json` on disk.
- [ ] **Move** it to `config/` so its role is explicit.
- [ ] **Add** `Korina/config.json` to `.gitignore` (already present — verify it stays there).
- [ ] **Update** `load_config()` to look for `config.json` first, then `config.example.json` as a fallback when the runtime file is missing.
- [ ] **Update** `README.md` to document the new layout.
- [ ] **Commit:** `chore: split tracked example config from runtime config.json`.

> If you want me to keep `Korina/config.json` as the only path, skip this step entirely and just edit README to say it's a runtime file (not an example). Both options are fine; this is a docs/UX call.

### Step 0.4 — Fix the doc/behavior mismatch on `agent_api_key`

**Symptom:** README says "API keys are not stored directly in the UI". The schema and UI both store `agent_api_key` in `config.json`.

**Subtasks:**

- [ ] **Replace** the README line in the "Configuration" section to be accurate:

  > `Korina/config.json` may contain an optional `agent_api_key` for local dev/test. In production use the `agent_api_key_env` field to read the key from the environment.

- [ ] **Verify** the field is still in `DEFAULT_CONFIG` and that the UI input has `autocomplete="off"` (it does, line 85). No code change here.
- [ ] **Commit:** `docs: clarify agent_api_key is opt-in dev/test storage`.

### Step 0.5 — Document secret-storage contract

**Add** a small "Secrets" section to README enumerating:

- `llm_api_key_env` → read from `os.environ[...]`
- `stt_api_key_env` → read from `os.environ[...]`
- `stt_llm_api_key_env` → read from `os.environ[...]`
- `agent_api_key` → stored in `config.json` (local dev only)
- `agent_api_key_env` is not used by the agent path; either remove the field or document why it exists.

**Subtasks:**

- [ ] **Search** for `agent_api_key_env` references; if unused, **remove** from `DEFAULT_CONFIG` and from the agent settings form.
- [ ] Otherwise **Add** the agent path actually reads it.
- [ ] **Commit:** `docs: secrets contract` (or `chore: drop unused agent_api_key_env` if removal is the answer).

### Step 0.6 — Add `display_model_label` usage consistently

**Symptom:** Backend has `display_model_label()`. Frontend has `prettyModelLabel()`. They produce slightly different output. The frontend currently uses its own. The backend `/api/models` returns `labels` for all known ids but the frontend ignores them when populating the Agent model dropdown.

**Subtasks:**

- [ ] **Verify** the current frontend code uses `j.labels[m] || prettyModelLabel(m)` for the response LLM and multimodal STT dropdowns (it does, lines 483 and 490).
- [ ] **Add** the same `j.labels[m] || prettyModelLabel(m)` pattern to the Agent model dropdown (line 505 currently uses `m` as text).
- [ ] **Commit:** `fix: agent model dropdown shows display label not raw id`.

This is technically Phase 2 work but it's a one-line fix that's directly part of the bug you reported, so it lives in Phase 0.

### Step 0.7 — Verify on live service

After all Phase 0 commits:

- [ ] Run the regression checklist from the prior session against roggoz live.
- [ ] Confirm `/api/health` reports correctly.
- [ ] Confirm provider activation still works.
- [ ] Confirm barge-in still interrupts.
- [ ] Confirm agent events still poll.
- [ ] Confirm `agent_injection_mode` shows in `/api/config` and respects `one-at-a-time` / `all`.

---

## Phase 1 — Backend modularization

**Goal:** same behavior, files split. No new features. No new dependencies. No new globals.

**Risk profile:** medium. The risk is mainly in the import graph and module init order. Mitigation: keep one `app.py` that imports the same routers; do not change route paths or payloads.

### Step 1.1 — Create the new directory layout

**Subtasks:**

- [ ] **Create** the following structure (empty `__init__.py` files where needed):

  ```text
  korina/
    __init__.py
    app.py                # FastAPI app factory + root route
    config.py             # DEFAULT_CONFIG, load/save, migration
    schemas.py            # Pydantic request/response models
    runtime/
      __init__.py
      state.py            # all module globals, one place
      http.py             # shared HTTP client helpers
    services/
      __init__.py
      provider_manager.py # start/stop/activate LLM providers
      model_catalog.py    # local GGUF + LM Studio catalog
      whisper_service.py  # faster-whisper model + inference
      multimodal_stt.py   # input_audio transcription path
      response_llm.py     # /api/chat path + format_voice_reply
      agent_service.py    # state-report loop + event ring + priority
      ack_service.py      # ack manifest + queue + worker
    routes/
      __init__.py
      health.py
      config.py
      models.py
      chat.py
      stt.py
      acks.py
      agent.py
      providers.py        # /api/llm/provider/activate
    util/
      __init__.py
      labels.py           # display_model_label
      paths.py            # APP_DIR + path resolution
  ```

- [ ] **Move** `kokoro-streaming-server.py` stays where it is (separate process); no change in Phase 1.
- [ ] **Commit:** `refactor: scaffold korina/ package layout (no behavior change)`.

### Step 1.2 — Move path and config bootstrapping

**Subtasks:**

- [ ] **Move** lines 27–50 of current `korina_voice_lab.py` into `korina/paths.py` and `korina/config.py`:
  - `_KORINA_REPO_ROOT`, `_DEFAULT_APP_DIR`, `APP_DIR`, `INDEX_PATH`, `ACK_DIR`, `ACK_PHRASES_PATH`, `CONFIG_PATH` → `korina/paths.py`
  - `KOKORO_URL`, `ACK_DEFAULT_VOICE`, `WHISPER_*` env defaults → `korina/paths.py` (or a new `korina/env.py` if you prefer)
  - `WHISPER_MODEL_CHOICES`, `LOCAL_MODEL_ROOTS`, `LMSTUDIO_HUB_ROOT`, `LLAMA_SERVER_BIN`, `LMSTUDIO_BIN`, `LLAMA_SERVER_MEDIA_PATH`, `LLAMA_SERVER_USER_UNIT` → `korina/paths.py`
  - `DEFAULT_CONFIG`, `CONFIG_KEYS` → `korina/config.py`
  - `load_config()`, `save_config()` → `korina/config.py`
- [ ] **Add** a `korina/config.py:DEFAULT_CONFIG_WITH_LEGACY_KEYS` mapping that lists every legacy key the loader still tolerates (`agent_steering_mode`, `delivery_mode: steer` value normalization). The loader references this instead of inline conditionals.
- [ ] **Commit:** `refactor: move path/config bootstrapping into korina/ package`.

### Step 1.3 — Move runtime globals

**Subtasks:**

- [ ] **Move** all module-level globals into `korina/runtime/state.py` as a `RuntimeState` dataclass:
  - ASR: `_asr_models`, `_asr_loaded_at_by_device`, `_asr_lock`, `_asr_infer_lock`, `_asr_device`, `_asr_compute_type`
  - Ack: `_ack_queue`, `_ack_in_progress`, `_ack_queue_lock`, `_ack_worker_running`, `_ack_last_error`, `_ack_last_generated`, `_ack_current_voice`
  - Agent: `_agent_lock`, `_agent_events`, `_agent_event_seq`, `_agent_busy`, `_agent_status`, `_agent_last_report`, `_agent_pending_injections`, `_agent_last_error`, `_agent_last_emitted_report_hash`, `_agent_last_emitted_report_at`
- [ ] **Replace** direct global access in services with explicit getters/setters or method parameters. Example: `state.whisper.get(key)` instead of `_asr_models[key]`.
- [ ] **Add** a `korina.runtime.state.runtime` module-level singleton, set during `app.py` startup.
- [ ] **Commit:** `refactor: gather module globals into RuntimeState`.

### Step 1.4 — Move services

One service module per file, no behavior change.

| New file | Source lines in `korina_voice_lab.py` |
|---|---|
| `services/whisper_service.py` | `get_asr` (868–901), `transcribe_wav_segments` (904–920), `compute_type_for` (862–865), `normalize_device` (853–860) |
| `services/multimodal_stt.py` | `lmstudio_transcribe_wav` (1019–1080) |
| `services/response_llm.py` | `response_llm_chat` (1099–1148), `format_voice_reply` (1083–1096) |
| `services/agent_service.py` | `generate_agent_state_report` (1151–1223), `sanitize_agent_report` (1226–1235), `classify_agent_priority` (1241–1261), `push_agent_event` (1264–1273), `agent_snapshot` (1276–1285), `run_agent_transcript_job` (1288–1368), `submit_agent_transcript` (1371–1381) |
| `services/ack_service.py` | `safe_slug` (642–644), `load_ack_manifest` (647–677), `ack_filename`/`ack_path` (680–686), `ack_files_for`/`missing_ack_phrases` (689–724), `enqueue_missing_acks` (727–743), `synthesize_ack_wav` (746–762), `ack_generation_worker` (765–790), `clear_ack_wavs` (793–801), `ack_status` (804–820) |
| `services/provider_manager.py` | `gui_env` (242–253), `stop_lmstudio`/`start_lmstudio`/`stop_ollama`/`start_ollama` (256–279), `write_llama_server_unit` (282–310), `start_llama_server`/`stop_llama_server` (313–321), `_resolve_llama_cpp_model_id` (324–352), `activate_llm_provider` (355–388) |
| `services/model_catalog.py` | `discover_local_gguf_models` (206–216), `discover_lmstudio_catalog_models` (219–231), `find_mmproj_for_model` (234–239) |

**Subtasks:**

- [ ] **Move** each service as listed above.
- [ ] **Add** a thin `__init__.py` in `services/` that re-exports the public function names so route modules can `from korina.services import whisper` etc.
- [ ] **Commit per service** (one commit per move): `refactor: extract whisper_service`, `refactor: extract multimodal_stt`, etc.

### Step 1.5 — Move config-derived getters

**Subtasks:**

- [ ] **Move** all `config_*()` helper functions into `korina/config.py` (or a `korina/config_helpers.py` if you want them separate):
  - `config_tts_base_url`, `config_llm_base_url`, `config_llm_chat_url`, `config_llm_models_url`
  - `config_stt_llm_provider`, `config_stt_llm_base_url`, `config_stt_llm_chat_url`, `config_stt_llm_models_url`
  - `config_stt_llm_api_env`, `config_stt_llm_model`
  - `config_min_speech_ms`, `config_partial_window_ms`
  - `config_llm_reasoning`, `config_stt_llm_reasoning`
  - `agent_provider`, `agent_base_url`, `agent_models_url`, `agent_chat_url`, `agent_auth_headers`
- [ ] **Move** `provider_preset_base_url`, `is_local_provider_base_url`, `display_model_label` into `korina/util/labels.py` and `korina/util/presets.py`.
- [ ] **Move** `auth_headers_from_env`, `api_key_from_config` into `korina/runtime/http.py`.
- [ ] **Move** `llm_models_for` into `korina/services/model_catalog.py` (it queries a `/v1/models` endpoint — it's catalog logic, not LLM logic).
- [ ] **Move** `parse_model_ids`, `agent_model_choices` into `korina/services/agent_service.py` (they're agent-specific).
- [ ] **Move** `synchronize_llm_dependents` into `korina/config.py` (it mutates the saved config dict).
- [ ] **Commit per file.**

### Step 1.6 — Extract route modules

**Subtasks:**

- [ ] **Move** each route group into its own `routes/*.py` file using `APIRouter`. Use prefix tags so the OpenAPI surface stays the same:

  | File | Routes |
  |---|---|
  | `routes/health.py` | `GET /api/health` |
  | `routes/config.py` | `GET /api/config`, `POST /api/config` |
  | `routes/models.py` | `GET /api/models` |
  | `routes/chat.py` | `POST /api/chat` |
  | `routes/stt.py` | `POST /api/transcribe`, `POST /api/transcribe/partial`, `POST /api/transcribe/stream` |
  | `routes/acks.py` | `GET /api/acks`, `GET /api/acks/status`, `POST /api/acks/rebuild` |
  | `routes/agent.py` | `GET /api/agent/status`, `GET /api/agent/events`, `POST /api/agent/transcript`, `POST /api/agent/permission-answer`, `POST /api/agent/reset`, `GET /api/agent/models`, `POST /api/agent/state-report` |
  | `routes/providers.py` | `POST /api/llm/provider/activate` |

- [ ] **Replace** the giant `korina_voice_lab.py` route decorators with imports and `app.include_router(...)` calls in `korina/app.py`.
- [ ] **Verify** every existing curl/FastAPI test path works the same way. None of the response payloads or status codes should change.
- [ ] **Commit:** `refactor: split FastAPI routes into routers`.

### Step 1.7 — Extract schemas

**Subtasks:**

- [ ] **Move** `ChatRequest`, `AgentStateRequest`, `AgentTranscriptRequest`, `AgentPermissionAnswer`, `ProviderActivateRequest` from inline in `korina_voice_lab.py` to `korina/schemas.py`.
- [ ] **Add** schemas for any new internal service methods (e.g. `ActivateProviderRequest`, `TranscribeResponse`).
- [ ] **Verify** that the same JSON the frontend posts still validates.
- [ ] **Commit:** `refactor: extract pydantic schemas`.

### Step 1.8 — Backward-compat entry point

The repo has been invoking `python3 korina_voice_lab.py` to start the server.

**Subtasks:**

- [ ] **Add** a one-line `korina_voice_lab.py` at the repo root that just does:

  ```python
  from korina.app import main
  if __name__ == "__main__":
      main()
  ```

  with `korina.app.main()` running `uvicorn.run(...)`.

- [ ] **Verify** `python3 korina_voice_lab.py` and `python3 -m korina.app` both work.
- [ ] **Commit:** `chore: keep korina_voice_lab.py as compatibility shim`.

### Step 1.9 — Remove the old monolith

**Subtasks:**

- [ ] **Delete** the contents of the original file. Keep it as the shim from Step 1.8.
- [ ] **Verify** the file count in the repo: `git ls-files | grep -c korina_voice_lab.py` should be 1.
- [ ] **Commit:** `chore: korina_voice_lab.py is now a shim to korina.app`.

### Step 1.10 — Phase 1 verification

- [ ] **Run** regression from Phase 0.7 on roggoz.
- [ ] **Verify** `/api/health`, `/api/config`, `/api/models`, `/api/chat`, `/api/transcribe*`, `/api/acks*`, `/api/agent*` all behave identically to before.
- [ ] **Diff** the OpenAPI schema (`/openapi.json`) before and after; only new tags should differ, not paths or response shapes.

---

## Phase 2 — Single source of truth for provider/model metadata

**Goal:** the frontend never hardcodes provider rules. Backend owns provider capability metadata and serves it.

**Risk profile:** medium. Risk is mostly in UI state plumbing. Behavior must remain identical at this phase.

### Step 2.1 — Add a capabilities endpoint

**Subtasks:**

- [ ] **Add** `GET /api/capabilities` that returns:

  ```json
  {
    "providers": {
      "llama.cpp": {
        "label": "llama.cpp local",
        "default_base_url": "http://127.0.0.1:8080/v1",
        "editable_base_url": false,
        "manageable": true,
        "model_sources": ["endpoint_loaded", "local_gguf"],
        "is_local": true
      },
      "lmstudio": {
        "label": "LM Studio local",
        "default_base_url": "http://127.0.0.1:1234/v1",
        "editable_base_url": false,
        "manageable": true,
        "model_sources": ["endpoint_loaded", "catalog"],
        "is_local": true
      },
      "ollama": {
        "label": "Ollama local",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "editable_base_url": false,
        "manageable": true,
        "model_sources": ["endpoint_loaded"],
        "is_local": true
      },
      "openai-compatible": {
        "label": "OpenAI-compatible / generic",
        "default_base_url": "",
        "editable_base_url": true,
        "manageable": false,
        "model_sources": ["endpoint_loaded"],
        "is_local": false
      }
    }
  }
  ```

- [ ] **Add** the same response shape for Agent provider types in a sibling object, e.g. `agent_providers`:

  ```json
  {
    "openai-compatible": { "editable_base_url": true, "...": "..." },
    "anthropic":         { "editable_base_url": true, "...": "..." }
  }
  ```

- [ ] **Replace** `provider_preset_base_url()` in `util/presets.py` to source its data from a single registry object that both `provider_preset_base_url()` and the new `/api/capabilities` route share.
- [ ] **Commit:** `feat: serve provider capabilities from backend`.

### Step 2.2 — Frontend reads capabilities

**Subtasks:**

- [ ] **Add** a tiny `Korina/capabilities.js` (or inline at top of `index.html` for now) that fetches `/api/capabilities` once and caches it.
- [ ] **Replace** the hardcoded `PROVIDER_BASE_URL_PRESETS` constant in `index.html` with a function that reads from the cached capabilities.
- [ ] **Replace** `setBaseUrlEditability()` to read `capabilities.providers[llmProvider()].editable_base_url` instead of the hardcoded `llmProvider()==='openai-compatible'` check.
- [ ] **Commit:** `refactor: source provider metadata from /api/capabilities`.

### Step 2.3 — Provider switch fires activation

The frontend already wires `llmProvider.onchange` to `activateSelectedProvider(...)`. Verify behavior is preserved, then:

- [ ] **Add** a test (or simple smoke check) that ensures clicking each provider:
  1. saves config
  2. POSTs `/api/llm/provider/activate`
  3. updates the UI from the returned `saved` config
  4. triggers `loadModelOptions(true)` to refresh the dropdown
- [ ] **Document** the contract in `docs/capabilities.md` (new file).

### Step 2.4 — Phase 2 verification

- [ ] Confirm the model dropdown now shows display labels for all providers, including LM Studio catalog.
- [ ] Confirm base URL field disables/enables correctly.
- [ ] Confirm the "Custom / cloud" option is gone (it already is — the dropdown says "OpenAI-compatible / generic"; we just want to make sure this is consistent everywhere).

---

## Phase 3 — Frontend modularization

**Goal:** turn the inline `<script>` block into discrete modules without changing behavior.

**Risk profile:** medium. The risk is regression in live audio path. Mitigation: leave the existing global `live`, `ttsSpeaking`, etc. names as the source of truth during migration; have modules read and write them.

### Step 3.1 — Decide on a module strategy

**Two options:**

- **Option A (no build step):** multiple `<script type="module">` tags in `index.html`, files live next to it under `Korina/js/`.
- **Option B (build step):** introduce Vite/esbuild, ship `dist/bundle.js` and a built `index.html`.

**Default choice: Option A.** It's lighter, fits the repo's "no build" style, and keeps the UI runnable as a single file via `python3 -m http.server` if you ever want to.

**Subtasks:**

- [ ] **Confirm** the choice with the user (`?DECIDE?`).
- [ ] **Add** `<script type="module" src="./js/app.js"></script>` and remove the inline script.

### Step 3.2 — Extract modules

Suggested file layout:

```text
Korina/
  index.html
  js/
    app.js            # entry: wires everything, initApp()
    state.js          # single state object, exported as `state`
    api.js            # fetch wrappers: getConfig, saveConfig, activateProvider, ...
    dom.js            # $() and helper DOM functions
    labels.js         # prettyModelLabel
    settings-ui.js    # settings modal: collectConfig, applyConfig, syncConverseSettingsUI
    providers-ui.js   # base URL editability, provider switch handling
    acks.js           # ack preload, ack playback
    vad.js            # resetAdaptiveVad, updateAdaptiveVad, rmsLevel, drawWave
    recorder.js       # makeRecorder, startBargeInCapture
    partial-queue.js  # startPartialTranscriptionLoop, processPartialQueue, ...
    live.js           # startLive, stopLive, liveMonitor, handleLiveTurn
    barge-in.js       # monitorBargeIn, monitorBargeSpeechEnd, handleBargeRecording
    speech.js         # speakSSE, speakBuffered, schedulePcm, stopTtsNow
    history.js        # history array, cleanHistoryForModel, stripTranscriptLabels
    agent-ui.js       # deliverTranscriptToAgent, pollAgentEvents, handleAgentEvent, interruptConverse
```

**Subtasks (one commit per file, behavior preserved):**

- [ ] **Extract** `state.js` — the existing globals become a single object.
- [ ] **Extract** `dom.js`, `labels.js`, `api.js`.
- [ ] **Extract** `settings-ui.js`, `providers-ui.js`.
- [ ] **Extract** `history.js`, `acks.js`.
- [ ] **Extract** `vad.js`, `recorder.js`.
- [ ] **Extract** `partial-queue.js`, `live.js`, `barge-in.js`.
- [ ] **Extract** `speech.js`.
- [ ] **Extract** `agent-ui.js`.
- [ ] **Commit per file:** `refactor(frontend): extract vad`, `refactor(frontend): extract partial-queue`, etc.

### Step 3.3 — Move inline CSS to a stylesheet

**Subtasks:**

- [ ] **Move** the contents of `<style>` in `index.html` to `Korina/styles.css`.
- [ ] **Replace** the `<style>` block with `<link rel="stylesheet" href="./styles.css">`.
- [ ] **Verify** visual behavior is identical (do a side-by-side in two browser tabs).
- [ ] **Commit:** `refactor(frontend): extract styles.css`.

### Step 3.4 — Stop re-querying the model list on focus

**Symptom:** the model dropdown reloads models on every focus/pointerdown. The 4-second TTL is the only protection.

**Subtasks:**

- [ ] **Replace** the focus/pointerdown re-query with a fetch on `pointerdown` only when the dropdown opens, not on every focus. Use the existing `loadModelOptions(true)` pattern.
- [ ] **Remove** the 4-second TTL hack.
- [ ] **Commit:** `refactor(frontend): query model endpoint on dropdown open only`.

### Step 3.5 — Phase 3 verification

- [ ] Run regression from Phase 1.10 again.
- [ ] Live conversation: start, speak, get reply, interrupt with barge-in, play ack, idle ack cadence, clear session.
- [ ] Settings modal: change provider, change model, change base URL, change voice.
- [ ] Agent: enable, watch event log, see state report injection, see priority interrupt.

---

## Phase 4 — Capability registry for model selection

**Goal:** the user cannot pick a text-only model for multimodal STT, or pick a remote-only provider where llama.cpp is required.

**Risk profile:** high. This is the first phase that changes user-visible behavior (some currently-pickable combinations will be filtered). So this is a separate commit set with clear changelog.

### Step 4.1 — Define model capability metadata

**Subtasks:**

- [ ] **Add** `korina/services/model_catalog.py:get_model_capability(model_id) -> dict` that returns:

  ```python
  {
    "supports_audio_input": bool,
    "source": "endpoint_loaded" | "local_gguf" | "catalog",
    "has_mmproj": bool,         # only meaningful for local_gguf
    "approx_vram_gb": float | None,  # when known
  }
  ```

- [ ] **Add** a heuristic: if model id contains "vision" or "audio" or is one of a small allow-list (e.g. "gemma-4-e2b", "gpt-4o-audio", "ultravox", "qwen2-audio"), `supports_audio_input = True`. If model id contains "instruct" or "chat" and no audio marker, `False`. **Document** the heuristic as a known-limitation in README — this is not authoritative, it's a UX filter, not a truth claim.
- [ ] **Add** a way to opt out per model. For now, a single config field `multimodal_stt_model_allowlist: list[str]` overrides the heuristic.
- [ ] **Commit:** `feat: model capability metadata with heuristic detection`.

### Step 4.2 — Surface capabilities in `/api/models`

**Subtasks:**

- [ ] **Replace** the existing `GET /api/models` to include `capabilities` per model:

  ```json
  {
    "llm_models": [...],
    "llm_models_capabilities": { "<id>": { "supports_audio_input": false, "source": "local_gguf" } },
    "stt_llm_models": [...],
    "stt_llm_models_capabilities": { ... },
    "...": "..."
  }
  ```

- [ ] **Update** `routes/models.py` to populate these.
- [ ] **Commit:** `feat: /api/models returns per-model capabilities`.

### Step 4.3 — Frontend filters dropdowns by capability

**Subtasks:**

- [ ] **Add** a helper `Korina/js/capability-filter.js` that:
  - takes a list of model ids + their capabilities
  - returns the subset matching a requirement (e.g. `requires: "audio_input"`)
- [ ] **Modify** `loadModelOptions()` so the multimodal STT dropdown shows only models where `supports_audio_input === true` (or where the user has overridden the allowlist).
- [ ] **Add** a `?All models` toggle in the dropdown for power users who want to override.
- [ ] **Commit:** `feat(frontend): filter multimodal STT dropdown by audio capability`.

### Step 4.4 — Provider / model compatibility matrix

**Subtasks:**

- [ ] **Add** `korina/services/provider_manager.py:provider_supports_model(provider, model) -> bool` with rules:
  - llama.cpp: any local gguf; an endpoint-loaded model only if llama.cpp has been started.
  - lmstudio: any model returned by its `/v1/models` or its catalog.
  - ollama: only models returned by ollama's `/v1/models`.
  - openai-compatible: any model returned by the configured base URL.
- [ ] **Wire** it into `activate_llm_provider()` so a clearly-wrong selection raises a 400 with a clear error rather than failing later inside the chat request.
- [ ] **Commit:** `feat: provider-model compatibility check`.

### Step 4.5 — Phase 4 verification

- [ ] Run regression.
- [ ] Confirm: selecting llama.cpp + a remote-only model (impossible in current config but try via curl) returns a clear error.
- [ ] Confirm: multimodal STT dropdown hides text-only Qwen3.5.
- [ ] Confirm: response LLM dropdown still shows everything.
- [ ] Confirm: agent model dropdown still shows everything.

---

## Phase 5 — Process supervision unification

**Goal:** all long-running services are managed by one mechanism.

**Risk profile:** high (operational change). This phase is opt-in and not required for the refactor's correctness.

### Step 5.1 — Decide on a supervisor

**Default choice:** keep `systemd --user` (it's already used for `llama-server.service`).

**Subtasks:**

- [ ] **Confirm** with the user (`?DECIDE?`) whether to:
  - keep the current hybrid (no agent action in Phase 5)
  - convert `start.sh`/`stop.sh` to write + use `systemd --user` units for everything
  - introduce a process supervisor like `s6` (already used elsewhere in your infra per the skill list)

### Step 5.2 — Write the unit files (if going with systemd)

**Subtasks:**

- [ ] **Add** `deploy/systemd/korina-voice-lab.service`
- [ ] **Add** `deploy/systemd/kokoro-streaming-server.service`
- [ ] **Update** `Korina/start.sh` and `Korina/stop.sh` to use `systemctl --user` (or print a deprecation warning directing users to the new commands).
- [ ] **Add** a `Korina/install-services.sh` helper to install the units into `~/.config/systemd/user/`.
- [ ] **Document** the new install/upgrade flow in README.
- [ ] **Commit:** `chore: ship systemd user units for all Korina services`.

### Step 5.3 — Phase 5 verification

- [ ] On roggoz, run `install-services.sh` and `systemctl --user daemon-reload`.
- [ ] Stop the old nohup processes, start the new units.
- [ ] Verify the same functional behavior.

---

## Phase 6 — Testing + CI

**Goal:** future refactors don't repeat the regressions we just fixed.

### Step 6.1 — Add a test scaffold

**Subtasks:**

- [ ] **Add** `pytest` as a dev dependency (not a runtime dep).
- [ ] **Add** `tests/` directory with `tests/conftest.py` that uses `TestClient` from FastAPI.
- [ ] **Add** a test that exercises the most-fragile paths.

### Step 6.2 — Backend tests

**Subtasks (one per concern):**

- [ ] **Test** config load/save round-trip.
- [ ] **Test** config migration: legacy `agent_steering_mode` → `agent_injection_mode`.
- [ ] **Test** config migration: legacy `delivery_mode: "steer"` → `delivery_mode: "injection"`.
- [ ] **Test** `synchronize_llm_dependents()` for each provider transition.
- [ ] **Test** `_resolve_llama_cpp_model_id()` for absolute path, bare id, suffix match, empty.
- [ ] **Test** `/api/agent/models` returns correctly when endpoint is up.
- [ ] **Test** `/api/agent/models` returns error gracefully when endpoint is down.
- [ ] **Test** priority classifier:
  - explicit `Priority: critical` with permission cue → `critical`
  - explicit `Priority: critical` with only garbled STT → `normal` (regression guard)
  - explicit `Priority: low` → `low`
  - no priority line, no cue → `normal`
  - permission cue alone → `critical`
- [ ] **Test** `format_voice_reply()`:
  - multi-sentence single line → one sentence per line
  - existing newlines preserved
  - empty input → empty
- [ ] **Test** `sanitize_agent_report()` strips `<think>` blocks, ack labels, and `Korina Agent Interrupt:` prefixes.
- [ ] **Test** `safe_slug()`.
- [ ] **Commit per test.**

### Step 6.3 — Frontend tests (smoke)

Subtasks (these can be plain Python-driven curl checks, no JS test framework required to start):

- [ ] **Test** that opening the settings modal triggers `/api/agent/models` (proxy via Playwright if available; else just hit the endpoint and confirm payload).
- [ ] **Test** that the served HTML contains the model dropdown id and a hint element.
- [ ] **Test** that `barge-in` thresholds are present in the served HTML.
- [ ] **Commit:** `test(frontend): smoke checks for served UI`.

### Step 6.4 — CI

**Subtasks:**

- [ ] **Add** a GitHub Actions workflow `.github/workflows/test.yml` that runs:
  1. `python -m pip install -e .[test]`
  2. `pytest`
  3. `curl -fsS http://localhost:8001/api/health` against a started server (matrix: python 3.10, 3.11, 3.12)
- [ ] **Commit:** `ci: add GitHub Actions smoke test`.

---

## Appendix A — Specific bug fixes already identified

These are guaranteed Phase 0/Phase 1 work and not contingent on architectural decisions.

| File | Line | Bug | Fix |
|---|---|---|---|
| `Korina/index.html` | 969, 980, 988, 989 | references undefined `SPEECH_THRESHOLD`/`SILENCE_THRESHOLD` | use `vadSpeechThreshold`/`vadSilenceThreshold` |
| `Korina/index.html` | 505 | agent model dropdown shows raw id, not display label | `o.textContent = (j.labels && j.labels[m]) || prettyModelLabel(m)` |
| `Korina/config.json` | 7 | `agent_busy_delivery_mode: "steer"` is a legacy value | migrate on load (or update example file) |
| `korina_voice_lab.py` | 593 | `agent_api_key_env` is referenced but may not be wired | verify and either remove or wire |
| `korina_voice_lab.py` | 530–533 | model discovery appends GGUF paths to llama.cpp but not LM Studio catalog | add a `lmstudio_catalog` field in `/api/models` and surface in UI (Phase 2) |

## Appendix B — Risk-rated refactor backlog

After this refactor, the next things to tackle (in order of leverage):

1. **End-to-end live regression test** (Playwright + real audio). Most user-visible regressions only show up in live mode. A scripted live test would catch them automatically.
2. **Live transcript streaming from server.** Currently the live loop polls; if the user is on a different machine, the latency shows. A WebSocket from the backend could carry partials back.
3. **Whisper model hot-swap.** `get_asr()` caches by `model:device:compute_type`, so changing model in the UI doesn't unload the old one. Memory accumulates over time.
4. **Ack phrase generation cost.** Each voice change clears the cache and re-synthesizes. Could be cached across voices if the voice only differs in id and the same text is generated.
5. **Agent event ring buffer** is in-memory only. Restart loses history. Could persist to `Korina/agent_events.jsonl`.

## Appendix C — Out of scope

These are tempting but the user did not ask for them and they belong in a separate workstream:

- New LLM providers beyond llama.cpp / LM Studio / Ollama / OpenAI-compatible.
- New TTS engines beyond Kokoro + OpenAI-compatible.
- A new auth model (the current `agent_api_key` is for local dev only).
- A real `agent` executor. The current agent is a state-report generator, not a tool-using agent.
- Mobile UI.

---

## Tracking checklist

Copy this into `docs/refactor/PROGRESS.md` and tick as you go.

- [ ] Phase 0.1 barge-in thresholds
- [ ] Phase 0.2 injection migration
- [ ] Phase 0.3 config role split
- [ ] Phase 0.4 agent_api_key docs
- [ ] Phase 0.5 secrets contract
- [ ] Phase 0.6 display label for agent
- [ ] Phase 0.7 verify on roggoz
- [ ] Phase 1.1 directory layout
- [ ] Phase 1.2 paths/config
- [ ] Phase 1.3 runtime state
- [ ] Phase 1.4 services
- [ ] Phase 1.5 config helpers
- [ ] Phase 1.6 routes
- [ ] Phase 1.7 schemas
- [ ] Phase 1.8 backward-compat shim
- [ ] Phase 1.9 delete monolith body
- [ ] Phase 1.10 verify
- [ ] Phase 2.1 capabilities endpoint
- [ ] Phase 2.2 frontend reads capabilities
- [ ] Phase 2.3 switch contract documented
- [ ] Phase 2.4 verify
- [ ] Phase 3.1 module strategy
- [ ] Phase 3.2 extract modules
- [ ] Phase 3.3 extract styles
- [ ] Phase 3.4 model fetch on open
- [ ] Phase 3.5 verify
- [ ] Phase 4.1 capability metadata
- [ ] Phase 4.2 /api/models includes capabilities
- [ ] Phase 4.3 frontend filters
- [ ] Phase 4.4 provider/model compatibility
- [ ] Phase 4.5 verify
- [ ] Phase 5.1 supervisor decision
- [ ] Phase 5.2 unit files
- [ ] Phase 5.3 verify
- [ ] Phase 6.1 scaffold
- [ ] Phase 6.2 backend tests
- [ ] Phase 6.3 frontend smoke
- [ ] Phase 6.4 CI
