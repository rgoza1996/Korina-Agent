# Phase 6 — pytest + CI

## Status
Not started. Stub seeded by prep cron (`korina-phase6-prep`, 2026-06-25). Expanded after review to capture the real gaps found before execution.

## Goal
Future Korina refactors should not repeat the regressions already fixed in Phases 0–5. Phase 6 adds a pytest-based unit/API test scaffold, CI that can run without roggoz-only services, and a live roggoz regression close-out that proves source and runtime are actually in sync.

## Key findings from the Phase 6 plan review

These are blockers/gaps that must be handled before or during Phase 6:

1. **The original Phase 6 plan was only a stub.** The detailed task list existed only in `docs/refactor/blueprint.md`, and that section predates Phases 4 and 5.
2. **Runtime sync was missing `Korina/js/`.** Any manual live regression after Phase 3 must sync the ES module tree, not only `index.html` and `styles.css`; otherwise tests can pass against stale frontend code.
3. **CI cannot run `pip install -e .[test]` yet.** The repo currently has no `pyproject.toml`, `setup.py`, or `requirements-dev.txt` defining test dependencies.
4. **pytest must not touch live runtime state.** `korina.util.paths.APP_DIR` resolves to `/home/roggoz/Korina` on roggoz unless `KORINA_APP_DIR` is set before import. Tests must isolate this to a temp app dir.
5. **`TestClient(create_app())` can trigger ACK startup side effects.** `create_app()` registers a startup hook that calls `enqueue_missing_acks(...)`. Unit/API tests must monkeypatch this or provide a test-only disable path.
6. **Blueprint Phase 6 misses Phase 4/5 coverage.** New fragile areas include model capabilities, audio probe cache, provider/model compatibility, static mounts, and systemd wrapper behavior.
7. **CI must not require real local services.** GitHub Actions should not require llama.cpp, Kokoro, Whisper, GGUF models, `systemd --user`, or roggoz paths. Live service coverage remains a roggoz-only close-out step.
8. **`PROGRESS.md` header can drift.** At review time it still named `a29502d` as current HEAD, while `origin/beta` had advanced to `fd111e1` after the Phase 6 stub commit. Refresh the header at Phase 6 start.

---

## Deployment sync strategy

- For automated tests / CI: run from `/home/roggoz/Korina-Agent`, no live rsync needed.
- For manual live regression on roggoz: MUST rsync from source → live before testing.
  - Concrete sync commands:
    ```bash
    rsync -a --delete /home/roggoz/Korina-Agent/korina/ /home/roggoz/Korina/korina/
    rsync -a --delete /home/roggoz/Korina-Agent/Korina/js/ /home/roggoz/Korina/js/
    rsync -a /home/roggoz/Korina-Agent/Korina/index.html /home/roggoz/Korina/index.html
    rsync -a /home/roggoz/Korina-Agent/Korina/styles.css /home/roggoz/Korina/styles.css
    rsync -a /home/roggoz/Korina-Agent/tests/regression_smoke.py /home/roggoz/Korina/tests/regression_smoke.py
    ```
  - Then: `systemctl --user restart korina-voice-lab.service` + poll `/api/health`.
  - Static deployment smoke after restart:
    ```bash
    curl -fsS http://127.0.0.1:8001/ | grep -E 'type="module"|js/app\.js'
    for path in /js/app.js /js/state.js /js/providers-ui.js /js/capability-filter.js /styles.css; do
      echo -n "$path: "
      curl -fsS -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8001$path"
    done
    ```
  - Expect: served HTML contains the module script and all static asset probes return `200`.

---

## Phase 6.0 — Preflight and doc refresh

### Task 6.0.1 — Refresh refactor metadata

**Objective:** Make the docs describe current `beta` before adding test infrastructure.

**Files:**
- Modify: `docs/refactor/PROGRESS.md`
- Modify: `docs/refactor/blueprint.md` if its `Status:` line still omits completed phases.

**Steps:**
1. `git fetch origin`.
2. Verify `origin/beta` HEAD and last code-touching commit:
   ```bash
   git log --oneline -8 origin/beta
   git log --oneline --no-merges origin/beta -- ':!docs' ':!*.md' | head -5
   ```
3. Update `PROGRESS.md` header to current `origin/beta` HEAD. Do **not** update `Last full verification` unless a fresh full regression actually ran.
4. If `blueprint.md` status is stale, patch only the status line.

**Verification:**
```bash
grep -nE 'Current `beta` HEAD|Last code-touching|Last full verification' docs/refactor/PROGRESS.md
grep -n '^Status:' docs/refactor/blueprint.md || true
git diff -- docs/refactor/PROGRESS.md docs/refactor/blueprint.md
```

**Commit:**
```bash
git add docs/refactor/PROGRESS.md docs/refactor/blueprint.md
git commit -m "docs: refresh refactor status before Phase 6"
```

---

## Phase 6.1 — pytest scaffold and packaging

### Task 6.1.1 — Add packaging metadata with test extras

**Objective:** Make the CI command `python -m pip install -e .[test]` real.

**Files:**
- Create: `pyproject.toml`

**Plan:**
- Define project metadata for editable install.
- Include only lightweight baseline runtime deps needed to import the app and run mocked tests.
- Put pytest dependencies under `[project.optional-dependencies].test`.
- Do **not** require heavy roggoz-only runtime dependencies in CI unless a test needs them. Avoid forcing real `torch`, `faster-whisper`, model weights, Kokoro voices, or systemd.

**Suggested dependency split:**
```toml
[project]
name = "korina-agent"
version = "0.0.0"
requires-python = ">=3.10"
dependencies = [
  "fastapi",
  "uvicorn",
  "pydantic",
  "python-multipart",
  "numpy",
  "soundfile",
]

[project.optional-dependencies]
test = [
  "pytest",
  "httpx",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

**Verification:**
```bash
python3 -m pip install -e '.[test]'
python3 -c 'import korina, fastapi, pytest; print("imports ok")'
```

**Commit:**
```bash
git add pyproject.toml
git commit -m "test: add pytest packaging scaffold"
```

### Task 6.1.2 — Add isolated pytest fixtures

**Objective:** Prevent tests from reading/writing live `/home/roggoz/Korina` state.

**Files:**
- Create: `tests/conftest.py`

**Requirements:**
- Set `KORINA_APP_DIR` to a temp directory before importing modules that read `korina.util.paths`.
- Create minimal runtime files in the temp dir:
  - `index.html`
  - `styles.css`
  - `js/app.js`
  - `js/state.js`
  - `js/providers-ui.js`
  - `js/capability-filter.js`
  - `Ack/ack_phrases.json`
  - `config/config.example.json`
- Provide fixtures for a reloaded app/config environment.
- Monkeypatch ACK generation so `TestClient` startup does not synthesize audio or contact Kokoro.

**Verification:**
```bash
pytest --collect-only -q
```

**Commit:**
```bash
git add tests/conftest.py
git commit -m "test: isolate Korina app dir for pytest"
```

---

## Phase 6.2 — Backend unit tests

Each bullet below should be one small commit unless multiple assertions naturally belong in one file.

### Task 6.2.1 — Config load/save and migration tests

**Files:**
- Create: `tests/unit/test_config.py`

**Cover:**
- config load/save round trip in temp `KORINA_APP_DIR`
- legacy `agent_steering_mode` → `agent_injection_mode`
- legacy `delivery_mode: "steer"` → `delivery_mode: "injection"`
- unknown keys are filtered according to `CONFIG_KEYS`
- `audio_unsupported` cache survives `save_config()`

**Commit:** `test(config): cover load save and migrations`

### Task 6.2.2 — Provider synchronization and resolver tests

**Files:**
- Create: `tests/unit/test_provider_manager.py`

**Cover:**
- `synchronize_llm_dependents()` for provider transitions
- `_resolve_llama_cpp_model_id()` for:
  - absolute path
  - exact filename
  - exact stem
  - case-insensitive substring
  - empty/fallback behavior
- `provider_supports_model()` compatible and incompatible examples
- `write_llama_server_unit()` output shape with `--reasoning on|off|auto`, using a temp unit path via env/monkeypatch

**Commit:** `test(provider): cover model resolution and compatibility`

### Task 6.2.3 — Model capability and catalog tests

**Files:**
- Create: `tests/unit/test_model_capability.py`
- Create: `tests/unit/test_model_catalog.py`

**Cover:**
- `get_model_capability()` registry hit
- mmproj-on-disk detection in a temp model root
- hard allowlist and blacklist behavior
- qwen3-vl remains not audio by default unless allowlisted
- `discover_local_gguf_models()` from temp `KORINA_LOCAL_MODEL_ROOTS`
- `discover_lmstudio_catalog_models()` from temp `KORINA_LMSTUDIO_HUB_ROOT`
- `llm_models_for()` combines endpoint-loaded and local GGUF models without dropping existing fields

**Commit:** `test(models): cover capability and catalog detection`

### Task 6.2.4 — Audio probe tests

**Files:**
- Create: `tests/unit/test_audio_probe.py`

**Cover:**
- `classify_failure()` for 400/415/422 audio unsupported bodies
- non-audio failures are not classified as unsupported
- `triple_key()` stable format
- `maybe_fallback_to_whisper()` with mocked `lmstudio_transcribe_wav` and mocked whisper fallback
- cache helper integration through `korina.config`

**Commit:** `test(audio): cover probe classification and fallback`

### Task 6.2.5 — Agent/service formatting tests

**Files:**
- Create: `tests/unit/test_agent_service.py`
- Create: `tests/unit/test_response_llm.py`
- Create: `tests/unit/test_labels.py`

**Cover:**
- priority classifier cases from the blueprint
- `sanitize_agent_report()` strips `<think>` blocks, ack labels, and `Korina Agent Interrupt:` prefixes
- `format_voice_reply()` sentence splitting, newline preservation, and empty input
- `safe_slug()` edge cases

**Commit:** `test(services): cover agent formatting helpers`

---

## Phase 6.3 — API tests with FastAPI TestClient

### Task 6.3.1 — App factory/TestClient smoke

**Files:**
- Create: `tests/api/test_app_factory.py`

**Cover:**
- `create_app()` returns a FastAPI app with the expected route count
- `/`, `/styles.css`, and `/js/app.js` serve from the temp app dir
- startup ACK generation is patched/no-op in tests

**Commit:** `test(api): cover app factory and static mounts`

### Task 6.3.2 — Config/capabilities/model API tests

**Files:**
- Create: `tests/api/test_config_capabilities_models.py`

**Cover:**
- `GET /api/config`
- `POST /api/config` round trip
- `GET /api/capabilities`
- `GET /api/models` additive capability fields (`llm_models_capabilities`, `stt_llm_models_capabilities`)
- endpoint-down behavior returns a graceful payload, not an exception

**Commit:** `test(api): cover config capabilities and models routes`

### Task 6.3.3 — Provider/audio-probe API tests

**Files:**
- Create: `tests/api/test_providers_audio_probe.py`

**Cover:**
- incompatible provider/model returns HTTP 400 with `incompatible_provider_model`
- `/api/audio-probe` list shape
- `/api/audio-probe` delete/clear query-param contract
- activation clears relevant probe cache entry

**Commit:** `test(api): cover provider activation and audio probe routes`

### Task 6.3.4 — Validation-error tests

**Files:**
- Create: `tests/api/test_validation.py`

**Cover:**
At least one missing-required-field case per manually-validated POST route that constructs Pydantic models inside the handler. Expected result should be 422 or the route's documented 400, not 500.

**Commit:** `test(api): guard invalid payload status codes`

---

## Phase 6.4 — Frontend/static smoke tests

No JS test framework in this phase. Use Python file/static-response tests only.

### Task 6.4.1 — Static module shape tests

**Files:**
- Create: `tests/frontend/test_static_frontend.py`

**Cover:**
- `Korina/index.html` uses `<script type="module" src="./js/app.js">`
- no large inline app script reappears
- `Korina/styles.css` exists and is linked
- required modules exist under `Korina/js/`
- imports reference existing local module files

**Commit:** `test(frontend): cover module and stylesheet shape`

### Task 6.4.2 — UI regression markers

**Files:**
- Modify: `tests/frontend/test_static_frontend.py`

**Cover:**
- model dropdown IDs and hint elements exist
- `sttCapabilityFilterOverride` toggle exists
- clear-probe button exists
- barge-in references use live/adaptive VAD threshold names, not undefined `SPEECH_THRESHOLD` / `SILENCE_THRESHOLD`
- model dropdown does not refresh on focus/pointerdown unless that behavior is intentionally reintroduced with tests

**Commit:** `test(frontend): cover fragile UI markers`

---

## Phase 6.5 — GitHub Actions CI

### Task 6.5.1 — Add pytest workflow

**Files:**
- Create: `.github/workflows/test.yml`

**Scope:**
- Install `.[test]`
- Run `pytest`
- Matrix: Python 3.10, 3.11, 3.12 after dependencies are confirmed to install cleanly.

**Important:** CI must not require:
- real llama.cpp
- real LM Studio
- real Kokoro
- real Whisper/GGUF models
- `systemd --user`
- `/home/roggoz/*` paths

**Suggested workflow body:**
```yaml
name: tests

on:
  push:
    branches: [beta, alpha, master]
  pull_request:

jobs:
  pytest:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e '.[test]'
      - run: pytest -q
```

**Commit:** `ci: add pytest workflow`

### Task 6.5.2 — CI docs note

**Files:**
- Modify: `README.md` or create `docs/testing.md`

**Cover:**
- local pytest command
- live roggoz regression command
- why CI does not start real local services

**Commit:** `docs: document test layers`

---

## Phase 6.6 — Live roggoz regression close-out

### Task 6.6.1 — Sync source to runtime

**Commands:**
```bash
rsync -a --delete /home/roggoz/Korina-Agent/korina/ /home/roggoz/Korina/korina/
rsync -a --delete /home/roggoz/Korina-Agent/Korina/js/ /home/roggoz/Korina/js/
rsync -a /home/roggoz/Korina-Agent/Korina/index.html /home/roggoz/Korina/index.html
rsync -a /home/roggoz/Korina-Agent/Korina/styles.css /home/roggoz/Korina/styles.css
rsync -a /home/roggoz/Korina-Agent/tests/regression_smoke.py /home/roggoz/Korina/tests/regression_smoke.py
systemctl --user restart korina-voice-lab.service
```

**Verification:**
```bash
systemctl --user is-active korina-voice-lab.service
curl -fsS http://127.0.0.1:8001/api/health | python3 -m json.tool | head -40
curl -fsS http://127.0.0.1:8001/ | grep -E 'type="module"|js/app\.js'
for path in /js/app.js /js/state.js /js/providers-ui.js /js/capability-filter.js /styles.css; do
  echo -n "$path: "
  curl -fsS -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8001$path"
done
```

### Task 6.6.2 — Run regression smoke from source checkout

**Commands:**
```bash
cd /home/roggoz/Korina-Agent
pytest -q
python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe
```

**Optional full regression:** only after llama.cpp is reachable and provider activation is expected to work:
```bash
python3 tests/regression_smoke.py --base http://127.0.0.1:8001
```

**Expected:** pytest passes; regression smoke passes. If `--no-chat --no-transcribe` passes but full regression fails because llama.cpp is down, report that separately instead of calling Phase 6 failed.

---

## Phase 6.7 — Close-out docs

### Task 6.7.1 — Update PROGRESS.md and blueprint status

**Files:**
- Modify: `docs/refactor/PROGRESS.md`
- Modify: `docs/refactor/blueprint.md`

**Cover:**
- pytest scaffold complete
- API/unit/static tests complete
- CI workflow added
- live regression result with pass counts
- current `beta` HEAD
- last code-touching commit

**Commit:**
```bash
git add docs/refactor/PROGRESS.md docs/refactor/blueprint.md
git commit -m "docs: close out Phase 6 testing and CI"
```

---

## Required execution discipline

- One plan task = one commit unless the task explicitly says otherwise.
- After every commit:
  ```bash
  git push origin beta
  test "$(git rev-parse HEAD)" = "$(git ls-remote --heads origin beta | awk '{print $1}')"
  ```
- Do not modify `alpha` or `master` unless the user explicitly asks.
- Do not edit live runtime data (`/home/roggoz/Korina/config.json`, `Ack/*.wav`, backups).
- Do not run `tests/regression_smoke.py` from `/home/roggoz/Korina`; it must run from `/home/roggoz/Korina-Agent`.
- Stop and report if pytest requires a heavy/runtime-only dependency; do not quietly add model-serving dependencies to CI.
- Stop and report if TestClient startup touches real Kokoro/Whisper/LLM resources; isolate or monkeypatch before continuing.

## Open questions before implementation

1. Should Phase 6 include Playwright/browser E2E now, or defer it to a later phase? Current recommendation: defer; static Python frontend tests are enough for Phase 6.
2. Should `pyproject.toml` include all current runtime dependencies, or only the subset needed for app import/tests? Current recommendation: minimal + documented runtime install remains in README.
3. Should live full regression be required for Phase 6 close-out, or is `--no-chat --no-transcribe` acceptable when llama.cpp is intentionally stopped? Current recommendation: run both when llama.cpp is reachable; otherwise report the decoupled service state explicitly.
