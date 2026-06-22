# Phase 1 — Backend Modularization Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Split the 1832-line `Korina/korina_voice_lab.py` monolith into a `korina/` package with separate services, routes, schemas, and runtime state — **without changing any behavior, route paths, response payloads, or the live service contract**.

**Architecture:** Scaffold a `korina/` package next to the existing monolith. Each blueprint sub-step moves a logical slice of code into its own module. After Step 1.8, the monolith becomes a thin entrypoint that imports from `korina.app:main`. After Step 1.9 the monolith body is removed entirely.

**Target branch:** `beta` (per user direction: newer branch takes priority over `alpha`).

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, faster-whisper, llama.cpp via subprocess, systemd `--user` for process supervision. No new runtime dependencies.

**Risk profile:** medium. Mitigation: one commit per sub-step, every commit must leave the live service starting and `/api/health` returning 200.

---

## Pre-flight (before any task)

Before starting Task 1.1, the controller verifies:

1. Source checkout on `beta`, working tree clean.
2. Live runtime `/home/roggoz/Korina/` matches source `Korina/`.
3. `systemctl --user is-active korina-voice-lab.service` returns `active`.
4. `curl -fsS http://127.0.0.1:8001/api/health` returns `{"ok": true, ...}`.
5. SSH key on roggoz is set up; `git push origin beta` works from roggoz.

If any check fails, stop and surface to the user before dispatching the first subagent.

---

## Task 1.1 — Scaffold `korina/` directory layout

**Objective:** Create the empty package skeleton from blueprint Step 1.1. No behavior change.

**Files (all empty placeholder files):**

- `korina/__init__.py`, `korina/app.py`, `korina/config.py`, `korina/schemas.py`
- `korina/runtime/__init__.py`, `korina/runtime/state.py`, `korina/runtime/http.py`
- `korina/services/__init__.py` + 7 service files (whisper, multimodal_stt, response_llm, agent, ack, provider_manager, model_catalog)
- `korina/routes/__init__.py` + 8 route files (health, config, models, chat, stt, acks, agent, providers)
- `korina/util/__init__.py`, `korina/util/labels.py`, `korina/util/paths.py`

**Steps:**

1. Verify monolith still starts: `systemctl --user is-active korina-voice-lab.service` and `curl -fsS http://127.0.0.1:8001/api/health`.
2. Create directories and `touch` each `__init__.py` and module file.
3. Verify import works: `cd /home/roggoz/Korina-Agent && python3 -c "import korina; print(korina.__file__)"`.
4. Verify live service is still up after the new package directory exists.
5. Commit: `git add korina/ && git commit -m "refactor: scaffold korina/ package layout (no behavior change)" && git push origin beta`.

---

## Task 1.2 — Move path and config bootstrapping

**Objective:** Move the top-of-file constants and `load_config` / `save_config` into `korina/util/paths.py` and `korina/config.py`. The monolith re-exports them so no other module breaks yet.

**Files:**

- Create: `korina/util/paths.py` with all `*_ROOT` / `*_PATH` / `KOKORO_URL` / `WHISPER_*` / `LMSTUDIO_*` / `LLAMA_SERVER_*` constants and env defaults.
- Create: `korina/config.py` with `DEFAULT_CONFIG`, `CONFIG_KEYS`, `LEGACY_*`, `_normalize_config_value`, `_config_example_path`, `load_config`, `save_config`, `synchronize_llm_dependents`, all `config_*` and `agent_*` getters, `auth_headers_from_env`, `api_key_from_config`, `provider_preset_base_url`, `is_local_provider_base_url`, `display_model_label`, `parse_model_ids`, `agent_model_choices`, `llm_models_for`.
- Modify: `Korina/korina_voice_lab.py` — replace the moved blocks with `from korina.config import *` and `from korina.util.paths import *`. Keep `app = FastAPI(...)` and the rest of the monolith intact.

**Steps:**

1. Create `korina/util/paths.py` and `korina/config.py` with the moved code (copy lines from the current monolith verbatim, preserving docstrings and behavior).
2. Patch the monolith to import from the new location.
3. Restart, hit `/api/health`, hit `/api/config`.
4. Commit.

---

## Task 1.3 — Move runtime globals into RuntimeState

**Objective:** Replace the module-level `_asr_*`, `_ack_*`, `_agent_*` globals with a single `RuntimeState` dataclass accessed through `korina.runtime.state.runtime`.

**Files:**

- Create: `korina/runtime/state.py` with `RuntimeState` dataclass containing `asr`, `ack`, `agent` sub-states, and a module-level `runtime` singleton.
- Modify: `Korina/korina_voice_lab.py` — replace `_asr_models[key]` with `runtime.asr.models[key]`, etc., across all globals.

```python
# korina/runtime/state.py
from __future__ import annotations
import threading
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AsrState:
    models: dict[str, object] = field(default_factory=dict)
    loaded_at_by_device: dict[str, float] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    infer_lock: threading.Lock = field(default_factory=threading.Lock)
    device: Optional[str] = None
    compute_type: Optional[str] = None

@dataclass
class AckState:
    queue: list = field(default_factory=list)
    in_progress: set = field(default_factory=set)
    queue_lock: threading.Lock = field(default_factory=threading.Lock)
    worker_running: bool = False
    last_error: Optional[str] = None
    last_generated: Optional[str] = None
    current_voice: str = 'af_heart'

@dataclass
class AgentState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    events: list = field(default_factory=list)
    event_seq: int = 0
    busy: bool = False
    status: str = 'idle'
    last_report: str = ''
    pending_injections: list = field(default_factory=list)
    last_error: Optional[str] = None
    last_emitted_report_hash: str = ''
    last_emitted_report_at: float = 0.0

@dataclass
class RuntimeState:
    asr: AsrState = field(default_factory=AsrState)
    ack: AckState = field(default_factory=AckState)
    agent: AgentState = field(default_factory=AgentState)

runtime = RuntimeState()
```

**Steps:**

1. Create `korina/runtime/state.py` with the `RuntimeState` dataclass.
2. Replace module globals in the monolith with `runtime.*` accesses (a sweeping change).
3. Verify live service.
4. Commit.

---

## Task 1.4 — Move services into `korina/services/`

**Objective:** Move each service module to its own file. **One commit per service** (7 commits total per blueprint 1.4).

**Files (per sub-task):**

- 1.4.a: `korina/services/whisper_service.py` ← `get_asr`, `transcribe_wav_segments`, `compute_type_for`, `normalize_device`
- 1.4.b: `korina/services/multimodal_stt.py` ← `lmstudio_transcribe_wav`, `lmstudio_models`
- 1.4.c: `korina/services/response_llm.py` ← `response_llm_chat`, `format_voice_reply`
- 1.4.d: `korina/services/agent_service.py` ← `generate_agent_state_report`, `sanitize_agent_report`, `classify_agent_priority`, `push_agent_event`, `agent_snapshot`, `run_agent_transcript_job`, `submit_agent_transcript`
- 1.4.e: `korina/services/ack_service.py` ← `safe_slug`, `load_ack_manifest`, `ack_filename`, `ack_path`, `ack_files_for`, `missing_ack_phrases`, `enqueue_missing_acks`, `synthesize_ack_wav`, `ack_generation_worker`, `clear_ack_wavs`, `ack_status`
- 1.4.f: `korina/services/provider_manager.py` ← `gui_env`, `stop_lmstudio`, `start_lmstudio`, `stop_ollama`, `start_ollama`, `write_llama_server_unit`, `start_llama_server`, `stop_llama_server`, `wait_for_llama_server_ready`, `_resolve_llama_cpp_model_id`, `activate_llm_provider`
- 1.4.g: `korina/services/model_catalog.py` ← `discover_local_gguf_models`, `discover_lmstudio_catalog_models`, `find_mmproj_for_model`, `local_model_roots`

**Steps (per service):**

1. Create the new file with the moved functions.
2. Update the monolith to import from the new location.
3. Verify live service.
4. Commit.

---

## Task 1.5 — Move config-derived getters and small utilities

**Objective:** Move `safe_slug` to `korina/util/labels.py`. Other config-derived getters already moved in 1.2.

**Files:**

- Modify: `korina/util/labels.py` — add `safe_slug`.
- Update monolith imports.

**Steps:**

1. Move `safe_slug` from the monolith to `korina/util/labels.py`.
2. Verify and commit.

---

## Task 1.6 — Extract route modules

**Objective:** Split the 22 FastAPI route decorators into per-resource `APIRouter` files.

**Files:**

- `korina/routes/health.py`, `config.py`, `models.py`, `chat.py`, `stt.py`, `acks.py`, `agent.py`, `providers.py`
- Modify: `korina/app.py` — build the FastAPI app, mount middleware, `app.include_router(...)` for each.
- Modify: `Korina/korina_voice_lab.py` — keep the FastAPI app import, but route handlers come from `korina.app:app`.

**Steps:**

1. For each route file, copy the route handlers into an `APIRouter`.
2. Update `korina/app.py` to compose them.
3. Update the monolith to use the composed app.
4. Verify OpenAPI: `curl -fsS http://127.0.0.1:8001/openapi.json | python3 -c "import json,sys; print(sorted(json.load(sys.stdin)['paths'].keys()))"`.
5. Commit.

---

## Task 1.7 — Extract Pydantic schemas

**Objective:** Move schemas to `korina/schemas.py`.

**Files:**

- Create: `korina/schemas.py` with `ChatRequest`, `AgentStateRequest`, `AgentTranscriptRequest`, `AgentPermissionAnswer`, `ProviderActivateRequest`.
- Modify: route files to import the schemas from `korina.schemas`.

**Steps:**

1. Copy the schemas from the monolith to `korina/schemas.py`.
2. Update route files to import from there.
3. Verify and commit.

---

## Task 1.8 — Backward-compat entrypoint

**Objective:** Make `Korina/korina_voice_lab.py` a thin shim.

**Files:**

- Modify: `korina/app.py` — add `main()`.
- Modify: `Korina/korina_voice_lab.py` — reduce to the shim.

**Steps:**

1. Add `main()` to `korina/app.py`.
2. Update the monolith (Step 1.8 keeps the shim; full deletion is Step 1.9).
3. Verify and commit.

---

## Task 1.9 — Remove the old monolith body

**Objective:** Delete the contents of `Korina/korina_voice_lab.py` (keep the shim).

**Files:**

- Modify: `Korina/korina_voice_lab.py` to a one-liner:

```python
from korina.app import main
if __name__ == '__main__':
    main()
```

**Steps:**

1. Backup the monolith first.
2. Replace the monolith with the shim (both source and live).
3. Restart, hit `/api/health`.
4. Commit.

---

## Task 1.10 — Phase 1 verification

**Objective:** Full regression and OpenAPI diff against the live service.

**Files:** No source changes; verification only.

**Steps:**

1. Run regression: `/api/health`, `/api/chat`, `/api/agent/status`, `/api/agent/events`, `/api/config`, `/api/models`, plus round-trip tests.
2. Diff OpenAPI before/after (if a pre-refactor `openapi.json` is available).
3. Update PROGRESS.md.
4. Commit.

---

## Out-of-scope (Phase 1)

- No new runtime dependencies.
- No route path changes, no payload changes.
- No new test scaffold (that's Phase 6).
- No process supervision changes (that's Phase 5).
- The WIP `@korina_app` decorator alias is left in place; cleanup is a separate decision for Phase 4+.

## Verification gates (per task)

Each task must pass before commit:

1. `python3 -m py_compile` on any new/modified `.py` file → exit 0.
2. `systemctl --user restart korina-voice-lab.service` → `active`.
3. `curl -fsS http://127.0.0.1:8001/api/health` → `{"ok": true, ...}`.
4. The relevant endpoint for the moved service responds identically to pre-move behavior.

## Branch policy

- All Phase 1 work lands on `beta`.
- `alpha` is left alone (it's the stable branch). After Phase 1 is fully verified, the user decides whether to merge `beta` → `alpha` (release) or keep `beta` as a longer-running RC.
- `master` is the public release branch and is **not** updated during Phase 1.

## Total commit count

Conservative estimate: ~15-20 commits on `beta` (one per blueprint sub-step, plus intermediate ones for larger moves like 1.4 which has 7 service moves). All behavior-preserving. All gated by the live service staying healthy.
