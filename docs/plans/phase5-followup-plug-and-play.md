# Phase 5 Follow-up: Frontend Replaceability + Backend Plug-and-Play

> **For Hermes:** Use `subagent-driven-development` to execute task-by-task.
> **Branch:** all changes land on `alpha` (current HEAD `6633cd9`).
> **Source anchor:** all line numbers below reference `alpha` at `6633cd9`.
> **Open questions (resolved 2026-07-11):** WS0 folded in; harness lives in `tests/e2e/`; alpha-only; no other flags.

**Goal:** Move Korina Converse from "in-process protocol seam" to a real replaceable frontend + plug-and-play backend, closing the four gaps the dogfood report (`/tmp/korina-dogfood-20260711-postfix/`) identified.

**Architecture:** Six workstreams ordered by dependency. WS0+WS1+WS2 are backend-only and unblock real third-party adapter shipping. WS3+WS4 are frontend-only and unblock a replaceable UI. WS5 closes the dogfood coverage gap that blocked the "click every button" claim.

**Tech stack:** FastAPI + Pydantic + custom static JS modules; `importlib.metadata` for entry-point discovery; existing `tests/converse/`, `tests/frontend/`, `tests/api/`, `tests/e2e/` harnesses.

---

## Workstreams at a glance

| WS | Title | Files | Commits |
|---|---|---|---|
| WS0 | CORS hardening | `korina/config.py`, `korina/app_factory.py`, `tests/converse/test_cors_config.py`, `tests/api/test_cors_routes.py` | 3 |
| WS1 | Persistent channel selection | `korina/config.py`, `korina/app_factory.py`, `korina/converse/registry.py`, `korina/routes/converse.py`, `tests/converse/test_persistence.py` | 4 |
| WS2 | Adapter auto-discovery | `pyproject.toml`, `korina/app_factory.py`, `korina/adapters/__init__.py`, `korina/adapters/hermes_stub.py`, `tests/converse/test_adapter_discovery.py` | 2 |
| WS3 | Configurable frontend API origin | `Korina/js/api.js`, `Korina/js/*.js`, `Korina/index.html`, `tests/frontend/test_api_origin.py` | 4 |
| WS4 | Conditional Agent tab | `Korina/js/settings-ui.js`, `Korina/js/app.js`, `Korina/index.html`, `korina/agents/base.py`, `tests/frontend/test_conditional_agent_tab.py` | 2 |
| WS5 | Exhaustive Playwright dogfood | `tests/e2e/test_dogfood_exhaustive.py`, `tests/e2e/test_foreign_origin.py` | 2 |

**Total: 17 atomic commits on `alpha`, each independently revertable.**

**Execution order:** WS0.1 → WS0.2 → WS1.1 → WS1.2 → WS1.3 → WS1.4 → WS2.1 → WS2.2 → WS3.1 → WS3.2 → WS3.3 → WS3.4 → WS4.1 → WS4.2 → WS5.1 → WS5.2.

WS0 first because it touches `app_factory.py` (which WS1.2 and WS2.2 also modify); CORS allowed-origins must exist before the foreign-origin tests can pass; the CORS config must exist in the same `DEFAULT_CONFIG` block that WS1.1 also writes (avoids two sequential edits to the same dict literal).

WS5 last because it gates on WS0 + WS1 + WS3 landing (the foreign-origin probe needs WS0 + WS3; the inventory-driven pass needs WS1 + WS4 to assert final UI state).

---

## WS0 — CORS hardening

Current `app_factory.py:48-53` sets `allow_origins=['*']` + `allow_credentials=True`, which **browsers reject for credentialed foreign-origin requests**. The wildcard-with-credentials combo is a spec violation. WS0 replaces this with a config-driven allowlist, defaulting to the two localhost origins that ship the app today.

### Task 0.1: Add `converse.allowed_origins` to `DEFAULT_CONFIG`

**Files:** Modify `korina/config.py:17-95` (add to `DEFAULT_CONFIG`), create `tests/converse/test_cors_config.py`.

**Step 1:** Write failing test.

```python
# tests/converse/test_cors_config.py
from korina.config import DEFAULT_CONFIG

def test_default_config_has_allowed_origins():
    assert "converse" in DEFAULT_CONFIG
    assert "allowed_origins" in DEFAULT_CONFIG["converse"]
    origins = DEFAULT_CONFIG["converse"]["allowed_origins"]
    assert isinstance(origins, list)
    assert "http://127.0.0.1:8001" in origins
    assert "http://localhost:8001" in origins
```

**Step 2:** Run, expect FAIL — `KeyError: 'converse'`.

**Step 3:** Add to `DEFAULT_CONFIG`:

```python
DEFAULT_CONFIG = {
    # ... existing keys ...
    'converse': {
        'channel': 'korina',
        'allowed_origins': ['http://127.0.0.1:8001', 'http://localhost:8001'],
    },
}
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit: `feat(cors): add allowed_origins to DEFAULT_CONFIG`.

### Task 0.2: Wire `allowed_origins` into `CORSMiddleware`

**Files:** Modify `korina/app_factory.py:48-53`.

**Step 1:** Write failing test.

```python
# tests/api/test_cors_routes.py
from fastapi.testclient import TestClient
from korina import app_factory

def test_cors_allows_listed_origin(monkeypatch):
    monkeypatch.setattr(app_factory, "load_config", lambda: {"converse": {"allowed_origins": ["http://allowed.test:9000"]}})
    app = app_factory.create_app()
    with TestClient(app) as c:
        # Preflight
        r = c.options(
            "/api/converse/channels",
            headers={
                "Origin": "http://allowed.test:9000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "http://allowed.test:9000"

def test_cors_blocks_unlisted_origin(monkeypatch):
    monkeypatch.setattr(app_factory, "load_config", lambda: {"converse": {"allowed_origins": ["http://allowed.test:9000"]}})
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.options(
            "/api/converse/channels",
            headers={
                "Origin": "http://evil.test:1234",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Wildcard+credentials is gone; explicit allowlist must not echo
        # unlisted origins. CORS preflight response header should not match.
        acao = r.headers.get("access-control-allow-origin", "")
        assert acao != "*"
        assert acao != "http://evil.test:1234"
```

**Step 2:** Run, expect FAIL — current middleware allows all origins.

**Step 3:** Replace the CORS middleware block:

```python
cfg = load_config()
allowed_origins = (
    cfg.get("converse", {}).get("allowed_origins")
    if isinstance(cfg.get("converse"), dict)
    else None
) or ['http://127.0.0.1:8001', 'http://localhost:8001']
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins),
    allow_credentials=False,  # Spec-compliant; no frontend code sends credentials.
    allow_methods=['*'],
    allow_headers=['*'],
)
```

**Step 4:** Run, expect PASS for both tests.

**Step 5:** Commit: `fix(cors): replace wildcard CORS with config-driven allowlist`.

### Task 0.3: Grep frontend for credentialed fetches (defensive)

**Files:** Grep only.

Before landing Task 0.2's `allow_credentials=False`, verify no frontend code sends cookies/credentials:

```bash
grep -rn "credentials: 'include'\|credentials: 'same-origin'\|withCredentials" Korina/js/
```

Expected: empty (the frontend is a static page, no auth cookies). If anything matches, flag and ask user before removing `allow_credentials`.

**Commit:** `chore(cors): document credentialed-fetch audit result in commit msg`.

---

## WS1 — Persistent channel selection

### Task 1.1: Add `converse.channel` to `DEFAULT_CONFIG`

**Files:** Modify `korina/config.py` (same `DEFAULT_CONFIG` block as WS0.1; this is the SAME `DEFAULT_CONFIG['converse']` dict — both writes are one literal edit, but the test is separate), create `tests/converse/test_persistence.py`.

**Step 1:** Write failing test.

```python
# tests/converse/test_persistence.py
from korina.config import DEFAULT_CONFIG

def test_default_config_has_converse_channel():
    assert "converse" in DEFAULT_CONFIG
    assert "channel" in DEFAULT_CONFIG["converse"]
    assert DEFAULT_CONFIG["converse"]["channel"] == "korina"
```

**Step 2:** Run, expect FAIL.

**Step 3:** Already done as part of WS0.1's `DEFAULT_CONFIG["converse"]` literal — confirm `channel: "korina"` is present.

**Step 4:** Run, expect PASS.

**Step 5:** Commit: `feat(converse): add channel to DEFAULT_CONFIG`.

### Task 1.2: Restore saved channel at startup

**Files:** Modify `korina/app_factory.py:71-79`.

**Step 1:** Write failing test.

```python
# tests/converse/test_persistence.py (add to existing file)
import json
from fastapi.testclient import TestClient
from korina import app_factory

def test_startup_restores_saved_channel(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"converse": {"channel": "korina"}}))
    monkeypatch.setattr(app_factory, "load_config", lambda: {"converse": {"channel": "korina"}})
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.get("/api/converse/channel")
        assert r.json()["channel"] == "korina"
```

**Step 2:** Run, expect FAIL — channel is still `"korina"` because saved channel restore logic isn't wired.

**Step 3:** Modify `_startup()`:

```python
@app.on_event('startup')
def _startup() -> None:
    _startup_generate_default_acks()
    from korina.converse import ensure_default_registered, set_active_converse_channel
    from korina.converse.registry import list_channels
    ensure_default_registered()
    cfg = load_config()
    saved = cfg.get("converse", {}).get("channel") if isinstance(cfg.get("converse"), dict) else None
    if saved and saved in list_channels():
        set_active_converse_channel(saved)
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit: `feat(converse): restore saved channel at startup`.

### Task 1.3: Persist channel switch to config

**Files:** Modify `korina/routes/converse.py:60-66`.

**Step 1:** Write failing test.

```python
# tests/converse/test_persistence.py (add)
from korina.routes import converse as converse_routes

def test_switch_channel_persists_to_config(tmp_path, monkeypatch):
    saved = {}
    def fake_save(cfg):
        saved.update(cfg)
    monkeypatch.setattr(converse_routes, "save_config", fake_save)
    monkeypatch.setattr(converse_routes, "load_config", lambda: {})
    from fastapi.testclient import TestClient
    from korina import app_factory
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.post("/api/converse/channel/korina")
        assert r.status_code == 200
    assert saved.get("converse", {}).get("channel") == "korina"
```

**Step 2:** Run, expect FAIL — `save_config` not called.

**Step 3:** Modify `switch_active_channel()`:

```python
@router.post('/api/converse/channel/{name}')
def switch_active_channel(name: str):
    try:
        set_active_converse_channel(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=...)
    from korina.config import load_config, save_config
    cfg = load_config()
    cfg.setdefault("converse", {})["channel"] = name
    save_config(cfg)
    return {"channel": name, "ok": True}
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit: `feat(converse): persist channel switch to config.json`.

### Task 1.4: Migration safety for legacy configs

**Files:** Modify `korina/config.py` (legacy-key normalization section).

Add `converse` key normalization so `config.json` files without `converse` block still load. Verify by loading the live `config.json` (md5 `5bb1fa4f...`) — must not raise.

```python
def _normalize_converse(cfg):
    if not isinstance(cfg.get("converse"), dict):
        cfg["converse"] = {
            "channel": DEFAULT_CONFIG["converse"]["channel"],
            "allowed_origins": list(DEFAULT_CONFIG["converse"]["allowed_origins"]),
        }
    else:
        cfg["converse"].setdefault("channel", DEFAULT_CONFIG["converse"]["channel"])
        cfg["converse"].setdefault("allowed_origins", list(DEFAULT_CONFIG["converse"]["allowed_origins"]))
    return cfg
```

Wire into `load_config()` before return.

**Commit:** `chore(converse): tolerate legacy configs missing converse block`.

---

## WS2 — Adapter auto-discovery via entry points

### Task 2.1: Define the entry-point contract

**Files:** Modify `pyproject.toml`, `korina/adapters/__init__.py`, `korina/adapters/hermes_stub.py`.

**Step 1:** Add entry-point group to `pyproject.toml`:

```toml
[project.entry-points."korina.adapters"]
hermes-stub = "korina.adapters.hermes_stub:register"
```

**Step 2:** Refactor `korina/adapters/hermes_stub.py`:

- Rename `register_hermes_stub` → `register` (the entry-point target).
- `register(register=True)` keeps the existing opt-in semantics.
- Keep `register_hermes_stub` as a back-compat alias.

**Step 3:** Run existing `tests/converse/test_hermes_stub.py` — must still pass.

**Commit:** `refactor(adapters): rename register_hermes_stub to register for entry-point discovery`.

### Task 2.2: Discovery scan at startup

**Files:** Modify `korina/app_factory.py:71-79`.

**Step 1:** Write failing test.

```python
# tests/converse/test_adapter_discovery.py
from importlib.metadata import EntryPoint
from korina import app_factory

def test_entry_point_adapter_appears_in_channels(monkeypatch):
    fake_ep = EntryPoint(name="test-ep", value="tests.converse._fake_ep:register", group="korina.adapters")
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: [fake_ep] if kw.get("group") == "korina.adapters" else [])
    # Create test fixture module that exports `register`
    import sys, types
    mod = types.ModuleType("tests.converse._fake_ep")
    from korina.converse import register_converse_channel
    from korina.converse.protocol import ConverseChannel, ConverseRequest, ConverseResponse
    class FakeChannel(ConverseChannel):
        name = "test-ep"
        async def send(self, req): return ConverseResponse(text="ok", finished=True)
        async def stream(self, req):
            yield ConverseResponse(text="ok", finished=True)
        async def cancel(self): pass
    def register(reg=True):
        if reg:
            register_converse_channel(FakeChannel())
    mod.register = register
    sys.modules["tests.converse._fake_ep"] = mod
    app = app_factory.create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        channels = c.get("/api/converse/channels").json()["channels"]
    assert "test-ep" in channels
```

**Step 2:** Run, expect FAIL — `test-ep` not registered.

**Step 3:** Add scan to `_startup()`:

```python
@app.on_event('startup')
def _startup() -> None:
    # ... existing WS0/WS1 logic ...
    import logging
    log = logging.getLogger(__name__)
    try:
        from importlib.metadata import entry_points
        for ep in entry_points(group="korina.adapters"):
            try:
                ep.load()(register=True)
            except Exception as e:
                log.warning("Adapter entry-point %s failed: %s", ep.name, e)
    except Exception as e:
        log.warning("Adapter entry-point scan failed: %s", e)
```

**Step 4:** Run, expect PASS.

**Commit:** `feat(converse): scan importlib.metadata for korina.adapters entry points at startup`.

### Task 2.3: Honest scope note in plan header

WS2 makes the seam ready for the Phase 6 `korina-converse` package split. It does **not** by itself enable third-party `pip install` distribution — that requires a separate package with its own `pyproject.toml` entry-points declared by the third-party package.

---

## WS3 — Configurable frontend API origin

### Task 3.1: Centralize origin in `js/api.js`

**Files:** Modify `Korina/js/api.js`.

**Step 1:** Write failing source-contract test.

```python
# tests/frontend/test_api_origin.py
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API_JS = REPO / "Korina" / "js" / "api.js"

def test_api_js_exports_API_ORIGIN():
    src = API_JS.read_text()
    assert "export const API_ORIGIN" in src
    assert "export function apiUrl" in src

def test_api_js_resolves_meta_origin():
    src = API_JS.read_text()
    assert 'korina-api-origin' in src
    assert 'window.KORINA_API_ORIGIN' in src
```

**Step 2:** Run, expect FAIL.

**Step 3:** Add to top of `Korina/js/api.js`:

```javascript
function resolveApiOrigin() {
  if (typeof window !== 'undefined' && window.KORINA_API_ORIGIN) {
    return String(window.KORINA_API_ORIGIN).replace(/\/$/, '');
  }
  const meta = (typeof document !== 'undefined')
    ? document.querySelector('meta[name="korina-api-origin"]')
    : null;
  return meta ? String(meta.getAttribute('content') || '').replace(/\/$/, '') : '';
}
export const API_ORIGIN = resolveApiOrigin();

export function apiUrl(path) {
  const p = path.startsWith('/') ? path : '/' + path;
  return API_ORIGIN + p;
}
```

**Step 4:** Run, expect PASS.

**Commit:** `feat(frontend): add API_ORIGIN + apiUrl() helper to api.js`.

### Task 3.2: Replace bare `fetch('/api/...')` calls

**Files:** Modify every `Korina/js/*.js` that uses `fetch('/api/...')` or `fetch("/api/...")`.

**Step 1:** Grep:

```bash
grep -rn "fetch(['\"]/api/" Korina/js/
```

**Step 2:** For each match, replace with `fetch(apiUrl('/api/...'))` and add `import { apiUrl } from './api.js';` if not already imported.

**Step 3:** Add a regression test:

```python
# tests/frontend/test_api_origin.py (add)
def test_no_bare_api_fetch_remaining():
    js_dir = REPO / "Korina" / "js"
    for js in js_dir.glob("*.js"):
        if js.name == "api.js":
            continue  # api.js defines apiUrl; it must contain 'fetch' calls
        src = js.read_text()
        for forbidden in ["fetch('/api/", 'fetch("/api/']:
            assert forbidden not in src, f"{js.name}: bare fetch remains: {forbidden}"
```

**Step 4:** Run, expect PASS.

**Commit:** `refactor(frontend): route all /api fetches through apiUrl() helper`.

### Task 3.3: Add `<meta>` fallback to `index.html`

**Files:** Modify `Korina/index.html` `<head>`.

```html
<meta name="korina-api-origin" content="">
```

**Commit:** `chore(frontend): add korina-api-origin meta tag defaulting to same-origin`.

### Task 3.4: Foreign-origin smoke test

**Files:** Create `tests/frontend/test_foreign_origin.py`.

Python `http.server` fixture serving a minimal HTML page from `:8765` with `<meta name="korina-api-origin" content="http://127.0.0.1:8001">`. Asserts the page can fetch `apiUrl('/api/converse/channels')` and parse the response. Uses the test fixture pattern from the WS5 harness (which lands later).

For this commit, write a minimal version that just exercises `resolveApiOrigin()` semantics via JSDOM or a minimal pure-JS unit test.

**Commit:** `test(frontend): foreign-origin API call works via meta origin override`.

---

## WS4 — Conditional Agent tab

### Task 4.1: Expose `agent_only` on the channel protocol

**Files:** Modify `korina/agents/base.py` (Pydantic `AgentCapabilities`).

**Step 1:** Write failing test:

```python
# tests/agents/test_capabilities.py (or wherever capabilities tests live)
def test_korina_adapter_reports_agent_only_true():
    from korina.agents.korina_adapter import KorinaAgentAdapter
    h = KorinaAgentAdapter().capabilities
    assert h.agent_only is True
```

**Step 2:** Run, expect FAIL — field doesn't exist.

**Step 3:** Add `agent_only: bool = True` to `AgentCapabilities`.

**Commit:** `feat(converse): add agent_only capability flag to AgentCapabilities`.

### Task 4.2: Hide Agent tab when active channel is not agent-only

**Files:** Modify `Korina/js/settings-ui.js`, `Korina/js/app.js`, `Korina/index.html`.

**Step 1:** Extend `/api/converse/channels` (server-side) to include per-channel `agent_only`, derived from registered channels' `capabilities`.

**Step 2:** In `syncConverseSettingsUI()` (or `applyConfig()`), toggle Agent tab button's `display` based on `state.activeChannelAgentOnly !== false`. Default visible (preserves today's behavior).

**Step 3:** Write failing tests:

```python
# tests/frontend/test_conditional_agent_tab.py
def test_agent_tab_visible_for_korina_channel():
    # populateChannelTab receives channels with agent_only:true for korina
    # Assert settingsTab[data-settings-tab="agent"] is visible
def test_agent_tab_hidden_for_non_agent_channel():
    # populateChannelTab receives channels where active channel has agent_only:false
    # Assert settingsTab[data-settings-tab="agent"] is display:none
```

**Step 4:** Implement.

**Commit:** `feat(frontend): hide Settings Agent tab when active channel lacks agent capability`.

---

## WS5 — Exhaustive Playwright dogfood

`tests/e2e/` directory, pytest-collected. Add `pytest.importorskip("playwright")` at module level so CI without Playwright skips cleanly.

### Task 5.1: Inventory-driven harness

**Files:** Create `tests/e2e/test_dogfood_exhaustive.py`.

Refactor `/tmp/dogfood_korina.py` into a pytest test. Inside a `browser` fixture:

1. Load `/`, enumerate all visible+id clickables.
2. For each, attempt click, capture `pageerror` count, console new-entries, post-click DOM state.
3. Open each modal (Settings, Endpoints), re-enumerate, repeat.
4. Assert `pageerror_count == 0` after every click.
5. Output a coverage table: `total / clicked / skipped_with_reason / errored`.

**Commit:** `test(e2e): inventory-driven Playwright dogfood harness`.

### Task 5.2: Foreign-origin probe

**Files:** Add to `tests/e2e/test_dogfood_exhaustive.py` (or sibling `test_foreign_origin.py`).

Fixture: spawn `http.server` on `:8765`, serve minimal HTML with `<meta name="korina-api-origin" content="http://127.0.0.1:8001">`, load in Playwright from origin `:8765`, assert `fetch(apiUrl('/api/converse/channels'))` succeeds.

The `:8765` origin must be added to `converse.allowed_origins` for this test to pass — the test fixture handles this via monkeypatch on `load_config()`.

**Commit:** `test(e2e): foreign-origin fetch via meta origin override`.

---

## Risks

1. **CORS breaking change.** Old configs/defaults allowed `*`; new allowlist is restrictive. **Mitigation:** defaults include the two localhost origins. Remote deploys must add their origin to `config.json`. Documented in commit message of WS0.2.
2. **Phase 6 dependency.** WS2 entry-point discovery enables the seam; third-party distribution still requires `korina-converse` package split.
3. **Branch discipline.** All work on `alpha`. No rebase against `beta`.
4. **`config.json` migration.** Legacy configs without `converse` block default correctly via `setdefault`. Task 1.4 covers.
5. **Frontend caching.** After WS3+WS4 deploy, browsers with cached modules need Ctrl+Shift+R. Operational note in commit message.
6. **Three workstreams touch `app_factory.py`.** WS0 (CORS middleware), WS1 (startup restore), WS2 (entry-point scan). Order: WS0 first, then WS1, then WS2 — each commits on a clean base. Verify by running `pytest -q --tb=line` after each commit.
7. **Playwright in CI.** Add `playwright` to `[project.optional-dependencies] e2e`. CI workflow changes are out of scope; tests skip cleanly via `pytest.importorskip` when Playwright isn't installed.
8. **scp discipline.** All file writes via `/tmp` → scp → md5 verify. Same trap as previous session — every transfer gets an explicit md5 check.

---

## Commit sequence summary

```
docs(plans): Phase 5 follow-up plan for frontend replaceability + adapter discovery [6633cd9, done]
docs(plans): fold WS0 (CORS hardening) into Phase 5 follow-up plan

feat(cors): add allowed_origins to DEFAULT_CONFIG
fix(cors): replace wildcard CORS with config-driven allowlist
chore(cors): document credentialed-fetch audit result

feat(converse): add channel to DEFAULT_CONFIG
feat(converse): restore saved channel at startup
feat(converse): persist channel switch to config.json
chore(converse): tolerate legacy configs missing converse block

refactor(adapters): rename register_hermes_stub to register for entry-point discovery
feat(converse): scan importlib.metadata for korina.adapters entry points at startup

feat(frontend): add API_ORIGIN + apiUrl() helper to api.js
refactor(frontend): route all /api fetches through apiUrl() helper
chore(frontend): add korina-api-origin meta tag defaulting to same-origin
test(frontend): foreign-origin API call works via meta origin override

feat(converse): add agent_only capability flag to AgentCapabilities
feat(frontend): hide Settings Agent tab when active channel lacks agent capability

test(e2e): inventory-driven Playwright dogfood harness
test(e2e): foreign-origin fetch via meta origin override
```

17 commits total (1 plan-amendment + 16 implementation), each independently revertable.

---

## Open questions

None remaining. Resolved 2026-07-11: WS0 folded in; `tests/e2e/` chosen; alpha-only; no other flags.