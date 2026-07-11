# Phase 5 Follow-up: Frontend Replaceability + Backend Plug-and-Play

> **For Hermes:** Use `subagent-driven-development` to execute task-by-task.
> **Branch:** all changes land on `alpha` (HEAD `3ffecc2`).
> **Source anchor:** all line numbers below reference `alpha` at `3ffecc2`.

**Goal:** Move Korina Converse from "in-process protocol seam" to a real replaceable frontend + plug-and-play backend, closing the four gaps the dogfood report (`/tmp/korina-dogfood-20260711-postfix/`) identified.

**Architecture:** Five workstreams ordered by dependency. WS1+WS2 are backend-only and unblock real third-party adapter shipping; WS3+WS4 are frontend-only and unblock a replaceable UI; WS5 closes the dogfood coverage gap that blocked the "click every button" claim.

**Tech stack:** FastAPI + Pydantic + custom static JS modules; `importlib.metadata` for entry-point discovery; existing `tests/converse/`, `tests/frontend/`, `tests/api/` harnesses.

---

## Workstreams at a glance

| WS | Title | Files | Commit on `alpha` |
|---|---|---|---|
| WS1 | Persistent channel selection | `korina/config.py`, `korina/app_factory.py`, `korina/converse/registry.py`, `korina/routes/converse.py`, tests | `feat(converse): persist active channel in config.json` |
| WS2 | Adapter auto-discovery via entry points | `pyproject.toml`, `korina/app_factory.py`, `korina/adapters/__init__.py`, `korina/adapters/hermes_stub.py`, tests | `feat(converse): discover adapters via importlib.metadata entry points` |
| WS3 | Configurable frontend API origin | `Korina/js/api.js`, `Korina/index.html`, `Korina/js/*.js`, frontend tests | `feat(frontend): centralize API origin in api.js; add <meta> override` |
| WS4 | Conditional Agent tab | `Korina/js/settings-ui.js`, `Korina/js/app.js`, `Korina/index.html`, `korina/converse/protocol.py`, `korina/agents/base.py`, tests | `feat(frontend): hide Agent tab when active channel lacks agent capability` |
| WS5 | Exhaustive Playwright dogfood (closes 14-control gap) | `tests/e2e/test_dogfood_exhaustive.py`, foreign-origin probe harness | `test(e2e): inventory-driven dogfood harness` |

**Order:** WS1 → WS2 (backend, no frontend deps) → WS3 → WS4 (frontend; WS4 reads `capabilities` from WS1+WS2 paths) → WS5 (verification of all four).

---

## WS1 — Persistent channel selection

### Task 1.1: Add `converse.channel` to DEFAULT_CONFIG

**Files:** Modify `korina/config.py:17-95`.

**Step 1:** Write failing test.

```python
# tests/converse/test_persistence.py
def test_default_config_has_converse_channel():
    from korina.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["converse"]["channel"] == "korina"
```

**Step 2:** Run, expect FAIL — `KeyError: 'converse'`.

**Step 3:** Add to `DEFAULT_CONFIG`:

```python
DEFAULT_CONFIG = {
    # ... existing keys ...
    'converse': {
        'channel': 'korina',
    },
}
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit: `feat(converse): add converse.channel to DEFAULT_CONFIG`.

### Task 1.2: Restore active channel at startup

**Files:** Modify `korina/app_factory.py:71-79`.

**Step 1:** Write failing test.

```python
# tests/converse/test_persistence.py
def test_startup_restores_saved_channel(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"converse": {"channel": "hermes-stub"}}))
    monkeypatch.setenv("KORINA_CONFIG_PATH", str(config_path))
    # Force-reimport app_factory and trigger startup
    from korina import app_factory
    import importlib; importlib.reload(app_factory)
    app = app_factory.create_app()
    with TestClient(app) as c:
        r = c.get("/api/converse/channel")
        assert r.json()["channel"] == "hermes-stub"
```

**Step 2:** Run, expect FAIL — `r.json()["channel"] == "korina"`.

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
def test_switch_channel_persists_to_config(tmp_path, monkeypatch):
    # ... open client, POST /api/converse/channel/hermes-stub, then read config.json
    assert json.loads(config_path.read_text())["converse"]["channel"] == "hermes-stub"
```

**Step 2:** Run, expect FAIL — config still says `"korina"`.

**Step 3:** Modify `switch_active_channel()`:

```python
@router.post('/api/converse/channel/{name}')
def switch_active_channel(name: str):
    try:
        set_active_converse_channel(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=...)
    # Persist
    from korina.config import load_config, save_config
    cfg = load_config()
    cfg.setdefault("converse", {})["channel"] = name
    save_config(cfg)
    return {"channel": name, "ok": True}
```

**Step 4:** Run, expect PASS.

**Step 5:** Commit: `feat(converse): persist channel switch to config.json`.

### Task 1.4: Migration safety

**Files:** Modify `korina/config.py` (legacy-key normalization section).

Add handling so `config.json` files without a `converse` block load without error. `DEFAULT_CONFIG.get("converse", {})` shape must work. Verify by loading the live `config.json` (md5 `5bb1fa4f...`) — no `converse` block, should still load.

**Commit:** `chore(converse): tolerate legacy configs missing converse block`.

---

## WS2 — Adapter auto-discovery

### Task 2.1: Define the entry-point contract

**Files:** Modify `pyproject.toml`, create `korina/adapters/__init__.py`.

**Step 1:** Add entry-point group to `pyproject.toml`:

```toml
[project.entry-points."korina.adapters"]
hermes-stub = "korina.adapters.hermes_stub:register"
```

**Step 2:** Refactor `korina/adapters/hermes_stub.py`:

- Rename `register_hermes_stub` → `register` (so `korina.adapters.hermes_stub:register` is the entry-point target).
- `register(register=True)` keeps the existing opt-in semantics; `register_hermes_stub` stays as a back-compat alias.

**Step 3:** Run existing `tests/converse/test_hermes_stub.py` — must still pass.

**Commit:** `refactor(adapters): rename register_hermes_stub to register for entry-point discovery`.

### Task 2.2: Discovery scan at startup

**Files:** Modify `korina/app_factory.py:71-79`.

**Step 1:** Write failing test.

```python
def test_entry_point_adapter_appears_in_channels(monkeypatch):
    # Fake entry point that registers a test channel
    fake_ep = {"korina.adapters": [("test-ep", "tests.converse._fake_ep:register")]}
    monkeypatch.setattr("importlib.metadata.entry_points", lambda **kw: fake_ep.get(kw["group"], []))
    # Run startup
    from korina import app_factory
    app = app_factory.create_app()
    with TestClient(app) as c:
        assert "test-ep" in c.get("/api/converse/channels").json()["channels"]
```

**Step 2:** Run, expect FAIL — `test-ep` not registered.

**Step 3:** Add scan to `_startup()`:

```python
@app.on_event('startup')
def _startup() -> None:
    # ... existing ensure_default_registered + saved-channel restore ...
    import importlib
    from importlib.metadata import entry_points
    for ep in entry_points(group="korina.adapters"):
        try:
            register_fn = ep.load()
            register_fn(register=True)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Adapter entry-point %s failed: %s", ep.name, e)
```

**Step 4:** Run, expect PASS.

**Commit:** `feat(converse): scan importlib.metadata for korina.adapters entry points at startup`.

### Task 2.3: Honest scope note in plan header

**Add a note to the plan and the README:** full plug-and-play for **third-party** adapters (distributed via `pip install`) still requires the `korina-converse` package split (Phase 6). WS2 makes the seam ready for that split; it does not by itself enable third-party distribution.

---

## WS3 — Configurable frontend API origin

### Task 3.1: Centralize origin in `js/api.js`

**Files:** Modify `Korina/js/api.js`.

**Step 1:** Write failing source-contract test.

```python
# tests/frontend/test_api_origin.py
def test_api_js_exports_API_ORIGIN():
    src = (Path("Korina/js/api.js")).read_text()
    assert "export const API_ORIGIN" in src

def test_no_bare_api_fetch_remaining():
    src = Path("Korina/js").joinpath("app.js").read_text()
    for forbidden in ["fetch('/api/", 'fetch("/api/']:
        assert forbidden not in src, f"bare fetch remains: {forbidden}"
```

**Step 2:** Run, expect FAIL.

**Step 3:** Add to top of `Korina/js/api.js`:

```javascript
// Centralize API origin so a replacement frontend can be served from any host.
// Default: same-origin (empty prefix). Override via:
//   1. <meta name="korina-api-origin" content="http://other-host:8001"> in index.html
//   2. window.KORINA_API_ORIGIN = "..." in inline <script> (highest priority)
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

**Files:** Modify every `Korina/js/*.js` that uses `fetch('/api/...')`.

Grep first:

```bash
grep -rn "fetch('/api\|fetch(\"/api" Korina/js/
```

For each match, change:

```javascript
const r = await fetch('/api/converse/channels');
```

to:

```javascript
import { apiUrl } from './api.js';
const r = await fetch(apiUrl('/api/converse/channels'));
```

(Or use the existing named export `apiUrl` already in `api.js`.)

**Re-run** `test_no_bare_api_fetch_remaining` after each file — must remain green.

**Commit:** `refactor(frontend): route all /api fetches through apiUrl() helper`.

### Task 3.3: Add `<meta>` fallback to `index.html`

**Files:** Modify `Korina/index.html` `<head>`.

```html
<!-- API origin override. Empty = same-origin. Set to a full base URL
     (no trailing slash) to serve the frontend from a different host than
     the backend, e.g. content="http://127.0.0.1:8001" when index.html
     is served from :8765. -->
<meta name="korina-api-origin" content="">
```

**Commit:** `chore(frontend): add korina-api-origin meta tag defaulting to same-origin`.

### Task 3.4: Foreign-origin smoke test

**Files:** Create `tests/frontend/test_foreign_origin.py`.

Playwright scenario: serve a tiny HTML page from `:8765` (Python `http.server` in fixture) that includes `<meta name="korina-api-origin" content="http://127.0.0.1:8001">`, loads the page in a browser, calls `fetch(apiUrl('/api/converse/channels'))`, asserts 200 + body shape.

**Commit:** `test(frontend): foreign-origin API call works via meta origin override`.

---

## WS4 — Conditional Agent tab

### Task 4.1: Expose `agent_only` on the channel protocol

**Files:** Modify `korina/converse/protocol.py` (or add to `korina/agents/base.py:AgentCapabilities`).

**Step 1:** Decide field. The `KorinaAgentAdapter` (the only production adapter today) reports `http_compatible=False, supports_permission_flow=True`. Add `agent_only: bool = True` defaulting to True, set to False on stubs/non-agent adapters.

**Step 2:** Refactor `HermesStubChannel.health()` to include `agent_only=False` in the response payload (since the stub does not back a real agent).

**Step 3:** Write failing test:

```python
def test_korina_adapter_reports_agent_only_true():
    from korina.agents.korina_adapter import KorinaAgentAdapter
    h = KorinaAgentAdapter().capabilities
    assert h.agent_only is True
```

**Step 4:** Run, expect FAIL (field doesn't exist yet).

**Step 5:** Add field to `AgentCapabilities` Pydantic model.

**Commit:** `feat(converse): add agent_only capability flag to ConverseChannel.health()`.

### Task 4.2: Hide Agent tab when active channel is not agent-only

**Files:** Modify `Korina/js/settings-ui.js`, `Korina/js/app.js`, `Korina/index.html`.

After WS1+WS2 land, `/api/converse/channel` and `/api/converse/channels` return enough to determine `agent_only`. The Channel tab's `populateChannelTab()` already fetches both.

**Step 1:** Extend `populateChannelTab()` to call each registered channel's `health()` and capture `agent_only`. (Or, simpler first cut: ship a `/api/converse/channels` response that includes per-channel `agent_only`, derived server-side from `register_converse_channel`'s metadata.)

**Step 2:** In `applyConfig()` / `syncConverseSettingsUI()`, toggle the Agent tab button's `display` based on `state.activeChannelAgentOnly !== false`. Default visible (today's behavior preserved).

**Step 3:** Write failing test:

```python
def test_agent_tab_visible_when_channel_is_agent_only():
    # Simulate /api/converse/channel returning {"channel": "korina", "agent_only": true}
    # Assert that the settingsTab[data-settings-tab="agent"] is visible
def test_agent_tab_hidden_when_channel_lacks_agent_capability():
    # Same but with agent_only: false
```

**Step 4:** Implement.

**Commit:** `feat(frontend): hide Settings Agent tab when active channel lacks agent capability`.

---

## WS5 — Exhaustive Playwright dogfood

### Task 5.1: Inventory-driven harness

**Files:** Create `tests/e2e/test_dogfood_exhaustive.py`.

Re-uses the existing `Korina/js` enumeration pattern from `/tmp/dogfood_korina.py` (already shipped to roggoz) but rewrites as a pytest plugin that:

1. Loads `/`, snapshots the full clickable inventory.
2. Opens each modal (Settings, Endpoints) and re-snapshots inside the modal context.
3. For every visible+id clickable, attempts a click, captures `pageerror` count, console new-entries, and post-click DOM state into a JSON artifact.
4. Asserts `pageerror_count == 0` after every click.
5. Outputs a coverage table: `total_clickables / clicked / skipped_with_reason / errored`.

**Step 1:** Move `/tmp/dogfood_korina.py` → `tests/e2e/test_dogfood_exhaustive.py`. Apply the OpenAPI eval fix from the previous session.

**Step 2:** Re-run on roggoz, confirm `62 clickables captured`, `48 clicked + 14 modal-context-deferred`, `page_errors == 0`.

**Commit:** `test(e2e): inventory-driven Playwright dogfood harness`.

### Task 5.2: Foreign-origin Playwright probe

**Files:** Add to `tests/e2e/test_dogfood_exhaustive.py` (or a sibling).

Server-spawn `http.server` on `:8765` from a fixture, serve `index.html` + minimal shim, load in Playwright from origin `:8765`, assert `fetch('/api/converse/channels')` succeeds with the `<meta>` origin override. Without WS3, this test would fail; it serves as the WS3 acceptance gate.

**Commit:** `test(e2e): foreign-origin fetch via meta origin override`.

---

## Risks & breaking changes

1. **CORS browser-spec violation.** Current `app_factory.py` uses `allow_origins=['*']` + `allow_credentials=True`. Browsers reject credentialed wildcard-CORS requests. This is **not in scope for this plan** (no CORS refactor listed above) but is a known sharp edge. If you want to harden CORS, add a WS0: explicit allowlist driven by a new `converse.allowed_origins` config key, defaulting to `['http://127.0.0.1:8001', 'http://localhost:8001']`. Flag for user decision.

2. **Phase 6 dependency.** WS2's entry-point discovery enables the seam; third-party adapter distribution still needs `korina-converse` package split. Plan is honest: WS2 = "ready for Phase 6", not "Phase 6 done."

3. **Branch discipline.** All five workstreams land on `alpha` (per user direction 2026-07-11). No rebase against `beta` until user requests it.

4. **`config.json` migration.** Old configs without `converse.channel` default to `"korina"` (the registered default). Tests must verify this explicitly (Task 1.4).

5. **Coverage gap closure.** WS5 must run after WS1+WS2+WS3+WS4 land, so the harness sees the final UI. Re-running it mid-plan would create noise.

6. **Frontend caching.** After WS3+WS4 deploy, browsers with cached `js/*.js` modules from before the deploy will continue running the old in-memory graph (same `static-SPA module cache` pitfall from the dogfood skill). Operational note: tell the user a hard refresh (Ctrl+Shift+R) is required to evict module cache.

---

## Commit sequence summary

```
docs(plans): Phase 5 follow-up plan for frontend replaceability + adapter discovery

feat(converse): add converse.channel to DEFAULT_CONFIG
feat(converse): restore saved channel at startup
feat(converse): persist channel switch to config.json
chore(converse): tolerate legacy configs missing converse block

refactor(adapters): rename register_hermes_stub to register for entry-point discovery
feat(converse): scan importlib.metadata for korina.adapters entry points at startup

feat(frontend): add API_ORIGIN + apiUrl() helper to api.js
refactor(frontend): route all /api fetches through apiUrl() helper
chore(frontend): add korina-api-origin meta tag defaulting to same-origin
test(frontend): foreign-origin API call works via meta origin override

feat(converse): add agent_only capability flag to ConverseChannel.health()
feat(frontend): hide Settings Agent tab when active channel lacks agent capability

test(e2e): inventory-driven Playwright dogfood harness
test(e2e): foreign-origin fetch via meta origin override
```

14 commits total. Each is independently revertable.

---

## Open questions for the user

1. **CORS hardening** — fold a WS0 into this plan, or punt?
2. **Plan home** — save under `docs/plans/phase5-followup-replaceability-plug-and-play.md` (committed to `alpha`) and present inline here, OR write to a separate location (e.g. GitHub Pages Roadmap)?
3. **Dogfood harness location** — `tests/e2e/` (new dir, needs an `__init__.py` and possibly a CI hook) or `scripts/dogfood/` (not collected by pytest, run on demand)?
4. **Branch alignment** — push only to `alpha`, or also fast-forward `master`?