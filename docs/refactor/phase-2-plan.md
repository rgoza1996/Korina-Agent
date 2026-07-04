# Phase 2 — Single Source of Truth for Provider/Model Metadata

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Each task = one commit on `beta`. All work is behavior-preserving; no new runtime dependencies, no new globals.

**Goal:** the frontend never hardcodes provider rules. Backend owns provider capability metadata; the frontend reads it from `/api/capabilities`.

**Architecture:** introduce one canonical `PROVIDER_CAPABILITIES` registry in `korina/util/presets.py` covering **both** response-LLM and agent providers. A new `GET /api/capabilities` route serializes the registry. Frontend fetches once at boot, caches, and replaces its inline `PROVIDER_BASE_URL_PRESETS` and the hardcoded `setBaseUrlEditability()` `llmProvider()=="openai-compatible"` check.

**Tech Stack:** FastAPI, Pydantic, vanilla JS (no build step, matches Phase 3 default).

**Target branch:** `beta` (per live state — last 6 commits on beta; live regression 28/28 at HEAD `95c6957`).

---

## Verified preconditions (2026-06-23)

- `beta` HEAD: `95c6957` (Phase 1 follow-up: clean service imports, critical-cue ordering, config.json git-hygiene check)
- Phase 0+1: complete. `korina/korina_voice_lab.py` is the 3-line shim; real app in `korina/app_factory.py`.
- Live runtime at `/home/roggoz/Korina/` matches source for all code files; `config.json` is the only diff (expected, runtime user data — do not touch).
- `korina/util/presets.py` currently has `_PROVIDER_PRESETS` (response-LLM only, 3 entries) and `_LOCAL_BASE_URLS` — these are the *Python* source of truth today.
- `Korina/index.html:355` has a parallel JS `PROVIDER_BASE_URL_PRESETS` (same 3 entries). The two are duplicated.
- `setBaseUrlEditability()` at `Korina/index.html:371` hardcodes `llmProvider()=="openai-compatible"` for response-LLM and `sttLlmProvider()=="openai-compatible"` for multimodal-STT. Agent provider has **no** base-URL editability rule at all (real bug Phase 2 should fix).
- Agent `<select id="agentProvider">` has 2 options: `openai-compatible`, `anthropic` (`Korina/index.html:83`). No preset for either.
- `agent_service.py:62,96` branches on `provider == "anthropic"` for payload shape but assumes the user has set a sensible base URL by hand.
- `/api/models` returns: `whisper_models, llm_models, llm_default, llm_error, llm_base_url, llama_cpp_local_models, lmstudio_catalog_models, labels, stt_llm_models, stt_llm_default, stt_llm_error, stt_llm_base_url`.
- No `/api/capabilities` exists yet.
- 10 routes in `korina/routes/`.

---

## Design decisions baked into this plan (override at any task)

| Decision | Choice | Override cost |
|---|---|---|
| Provider scope | response-LLM **and** agent (Phase 2 blueprint §2.1 calls for both) | low — task 2.1 splits by section |
| Agent anthropic base URL | empty default in registry; user types their own Anthropic-compatible URL | low — single dict entry |
| Anthropic-specific path | **not** added in Phase 2 (no `/v1/messages` preset); user picks the URL their proxy exposes | low — registry entry |
| Capability-key convention | `editable_base_url`, `manageable`, `model_sources`, `is_local` (snake_case JSON) — matches blueprint §2.1 | low — field rename |
| Wiring shape | single shared Python dict -> `GET /api/capabilities` -> JS `capabilitiesCache` -> inline JS reads it | no build step, no bundler |
| Target branch | `beta` (matches live state, OOB confirmed) | high — rebase, don't switch mid-phase |
| Caching | fetch once at boot (page load), 1 retry on failure, no TTL | low — replace fetch call |
| Push pattern | commit per task to `beta` only; do **not** touch `alpha` or `master` | n/a |
| Existing presets.py / JS presets | both stay during migration; deleted in 2.2 once consumers are gone | n/a |

---

## Phase 2 overview

| # | Step | LOC touched | Risk |
|---|---|---|---|
| 2.1 | Backend `PROVIDER_CAPABILITIES` registry + `/api/capabilities` route | ~120 | low |
| 2.2 | Frontend reads `/api/capabilities`, drops inline presets + hardcoded editability check | ~80 | medium (UI state plumbing) |
| 2.3 | Provider switch contract documented in `docs/capabilities.md` + regression smoke | ~50 | low |
| 2.4 | Phase 2 verification: live regression + label/preset parity + edge cases | n/a | n/a |

Each step ends with a working tree, a green smoke run, and a commit on `beta`.

---

## Step 2.1 — Backend `PROVIDER_CAPABILITIES` registry + `/api/capabilities` route

**Goal:** one Python registry holds the full provider capability table; the route serves it as JSON; the existing `provider_preset_base_url()` and `is_local_provider_base_url()` helpers in `korina/util/presets.py` read from the same registry so there's no second source of truth.

**Files:**

- Modify: `korina/util/presets.py` (registry + helpers)
- Create: `korina/schemas_capabilities.py` (Pydantic response models)
- Create: `korina/routes/capabilities.py` (route module)
- Modify: `korina/app_factory.py` (register the new router)
- Modify: `tests/regression_smoke.py` (add `/api/capabilities` smoke check; bump count to 29)

### Task 2.1.1 — Define the registry

**Files:** Modify `korina/util/presets.py`

**Step 1:** Replace the entire contents of `korina/util/presets.py` with:

```python
"""Provider preset + capability registry for Korina Voice Lab.

Single source of truth for:
  * canonical local base URLs per provider
  * provider capability metadata served at GET /api/capabilities

All callers (preset helpers, the capabilities route, and future provider
lifecycle code) read from PROVIDER_CAPABILITIES. Do not duplicate
provider rules anywhere else in the codebase.

No state, no side effects -- safe to import anywhere.
"""

from __future__ import annotations

from typing import Any


# Registry: {provider_id: capability_dict}.
# Capability keys (snake_case JSON, served verbatim by /api/capabilities):
#   label:                 str   -- user-facing dropdown label
#   default_base_url:      str   -- canonical local URL; "" for non-local
#   editable_base_url:     bool  -- whether the UI lets the user override
#   manageable:            bool  -- backend can start/stop the server
#   model_sources:         list  -- which of:
#                                  endpoint_loaded, local_gguf, catalog
#   is_local:              bool  -- is this a localhost inference server?
#   agent_only:            bool  -- only valid for the agent path
#   response_llm_only:     bool  -- only valid for the response-LLM path
PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    # ---- response-LLM providers ----
    "llama.cpp": {
        "label": "llama.cpp local",
        "default_base_url": "http://127.0.0.1:8080/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded", "local_gguf"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": False,
    },
    "lmstudio": {
        "label": "LM Studio local",
        "default_base_url": "http://127.0.0.1:1234/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded", "catalog"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": False,
    },
    "ollama": {
        "label": "Ollama local",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": False,
    },
    "openai-compatible": {
        "label": "OpenAI-compatible / generic",
        "default_base_url": "",
        "editable_base_url": True,
        "manageable": False,
        "model_sources": ["endpoint_loaded"],
        "is_local": False,
        "agent_only": False,
        "response_llm_only": False,
    },
    # ---- agent-only providers ----
    "anthropic": {
        "label": "Anthropic-compatible",
        "default_base_url": "",
        "editable_base_url": True,
        "manageable": False,
        "model_sources": ["endpoint_loaded"],
        "is_local": False,
        "agent_only": True,
        "response_llm_only": False,
    },
}


# Legacy compatibility: response-LLM-only preset lookup by id.
# Kept because provider_preset_base_url() is called from
# provider_manager and util modules. Delegates to the registry.
def provider_preset_base_url(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    cap = PROVIDER_CAPABILITIES.get(provider, {})
    if cap.get("agent_only"):
        return ""
    return str(cap.get("default_base_url") or "")


def is_local_provider_base_url(base_url: str, provider: str = "") -> bool:
    base = str(base_url or "").strip().rstrip("/")
    provider = str(provider or "").strip().lower()
    cap = PROVIDER_CAPABILITIES.get(provider, {})
    if cap.get("is_local"):
        return True
    # Legacy fallback: match any known local default URL.
    for cap in PROVIDER_CAPABILITIES.values():
        if cap.get("is_local") and base == str(cap.get("default_base_url") or "").rstrip("/"):
            return True
    return False


def capabilities_for_section(section: str) -> dict[str, dict[str, Any]]:
    """Return the subset of PROVIDER_CAPABILITIES valid for section.

    section is either "response_llm" or "agent". The split
    exists so the UI can render two distinct dropdowns and so the backend
    never accidentally offers an agent-only provider to the response path.
    """
    section = str(section or "").strip().lower()
    out: dict[str, dict[str, Any]] = {}
    for pid, cap in PROVIDER_CAPABILITIES.items():
        if section == "agent" and cap.get("response_llm_only"):
            continue
        if section == "response_llm" and cap.get("agent_only"):
            continue
        out[pid] = cap
    return out
```

**Step 2:** Verify compile + import surface:

```bash
cd /home/roggoz/Korina-Agent
python3 -m py_compile korina/util/presets.py
PYTHONPATH=. python3 -c "
from korina.util.presets import (
    PROVIDER_CAPABILITIES,
    provider_preset_base_url,
    is_local_provider_base_url,
    capabilities_for_section,
)
assert provider_preset_base_url('llama.cpp') == 'http://127.0.0.1:8080/v1'
assert provider_preset_base_url('anthropic') == ''
assert provider_preset_base_url('openai-compatible') == ''
assert is_local_provider_base_url('http://127.0.0.1:8080/v1', 'llama.cpp') is True
assert is_local_provider_base_url('http://127.0.0.1:8080/v1') is True
assert is_local_provider_base_url('https://api.openai.com/v1', 'openai-compatible') is False
assert set(capabilities_for_section('response_llm').keys()) == {
    'llama.cpp', 'lmstudio', 'ollama', 'openai-compatible'
}
assert set(capabilities_for_section('agent').keys()) == {
    'openai-compatible', 'anthropic'
}
print('presets registry OK')
"
```

Expected: `presets registry OK`. Any other output is a failure.

**Step 3:** Smoke-check the live service still serves `/api/health` (no restart needed — `presets.py` isn't imported at module-load time by any route, only by `provider_manager` on demand):

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net 'curl -fsS http://127.0.0.1:8001/api/health | head -c 80 && echo'
```

Expected: `{"ok":true,...`.

**Step 4:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add korina/util/presets.py
git commit -m "feat(capabilities): add PROVIDER_CAPABILITIES registry

Single source of truth for provider preset URLs and capability metadata.
provider_preset_base_url and is_local_provider_base_url now delegate to
the registry; their public behavior is unchanged.

Adds capabilities_for_section() which splits the registry by
response_llm vs agent path, ready to be served at /api/capabilities in
2.1.3.

No runtime behavior change. Preset URLs and is_local detection return
the same values as before for all 3 prior response-LLM providers."
```

### Task 2.1.2 — Pydantic response schemas

**Files:** Create `korina/schemas_capabilities.py`

**Step 1:** Write the file:

```python
"""Pydantic schemas for GET /api/capabilities.

Separated from korina.schemas to keep capability metadata in a
single, import-light module. The route in korina.routes.capabilities
imports these and korina.util.presets.PROVIDER_CAPABILITIES to build
the response.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Known model-source values; constrain to keep the registry honest.
ModelSource = Literal["endpoint_loaded", "local_gguf", "catalog"]


class ProviderCapabilities(BaseModel):
    label: str = Field(..., description="User-facing dropdown label")
    default_base_url: str = Field(
        "", description="Canonical local URL; empty for non-local providers"
    )
    editable_base_url: bool = Field(
        ..., description="Whether the UI lets the user override the base URL"
    )
    manageable: bool = Field(
        ..., description="Whether the backend can start/stop the server"
    )
    model_sources: list[ModelSource] = Field(
        default_factory=list,
        description="Sources the frontend should query for models",
    )
    is_local: bool = Field(..., description="Localhost inference server")
    agent_only: bool = Field(
        False, description="Only valid for the agent path"
    )
    response_llm_only: bool = Field(
        False, description="Only valid for the response-LLM path"
    )


class CapabilitiesResponse(BaseModel):
    providers: dict[str, ProviderCapabilities] = Field(
        ...,
        description="Providers valid for the response-LLM path "
        "(also valid for the agent path unless listed under agent_providers).",
    )
    agent_providers: dict[str, ProviderCapabilities] = Field(
        ...,
        description="Providers valid for the agent path, including agent-only ones.",
    )
    version: int = Field(1, description="Schema version; bump on breaking changes")
```

**Step 2:** Compile and import check:

```bash
cd /home/roggoz/Korina-Agent
python3 -m py_compile korina/schemas_capabilities.py
PYTHONPATH=. python3 -c "
from korina.schemas_capabilities import (
    CapabilitiesResponse, ProviderCapabilities, ModelSource
)
# Build a minimal response to verify shape
from korina.util.presets import capabilities_for_section
resp = CapabilitiesResponse(
    providers=capabilities_for_section('response_llm'),
    agent_providers=capabilities_for_section('agent'),
)
print('sections:', sorted(resp.providers), '|', sorted(resp.agent_providers))
"
```

Expected: `sections: ['llama.cpp', 'lmstudio', 'ollama', 'openai-compatible'] | ['anthropic', 'openai-compatible']`.

**Step 3:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add korina/schemas_capabilities.py
git commit -m "feat(capabilities): add Pydantic schemas for /api/capabilities

ProviderCapabilities describes one provider; CapabilitiesResponse wraps
two sections (providers for the response-LLM path, agent_providers for
the agent path) so the UI can populate both dropdowns from one fetch.
version field allows future breaking changes."
```

### Task 2.1.3 — Route module + app_factory registration

**Files:**

- Create: `korina/routes/capabilities.py`
- Modify: `korina/app_factory.py` (one new import + `include_router`)

**Step 1:** Create the route module:

```python
"""GET /api/capabilities -- provider capability registry.

The frontend fetches this once at boot and uses it to populate the
provider dropdowns, set the default base URL on provider change, and
toggle the base-URL field's editability. Replaces the hardcoded
PROVIDER_BASE_URL_PRESETS constant and the setBaseUrlEditability()
``llmProvider()===\"openai-compatible\"`` check in Korina/index.html.
"""

from __future__ import annotations

from fastapi import APIRouter

from korina.schemas_capabilities import CapabilitiesResponse
from korina.util.presets import capabilities_for_section


router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    """Return provider capabilities for both response-LLM and agent paths."""
    return CapabilitiesResponse(
        providers=capabilities_for_section("response_llm"),
        agent_providers=capabilities_for_section("agent"),
    )
```

**Step 2:** Register the router in `korina/app_factory.py`. Find the existing block that imports the other route modules (around the top of the file, lines that look like `from korina.routes import acks as _acks_routes` etc.) and add one more import in the same alphabetical style:

```python
from korina.routes import capabilities as _capabilities_routes
```

Then in the `create_app()` body where the routers are mounted (look for `app.include_router(` calls), add:

```python
    app.include_router(_capabilities_routes.router)
```

**Step 3:** Verify py_compile:

```bash
cd /home/roggoz/Korina-Agent
python3 -m py_compile korina/routes/capabilities.py
python3 -m py_compile korina/app_factory.py
```

**Step 4:** Sync to live and restart:

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    set -e
    cp /home/roggoz/Korina-Agent/korina/util/presets.py       /home/roggoz/Korina/korina/util/presets.py
    cp /home/roggoz/Korina-Agent/korina/schemas_capabilities.py /home/roggoz/Korina/korina/schemas_capabilities.py
    cp /home/roggoz/Korina-Agent/korina/routes/capabilities.py  /home/roggoz/Korina/korina/routes/capabilities.py
    cp /home/roggoz/Korina-Agent/korina/app_factory.py         /home/roggoz/Korina/korina/app_factory.py
    systemctl --user restart korina-voice-lab.service
    sleep 1
    systemctl --user is-active korina-voice-lab.service
    curl -fsS http://127.0.0.1:8001/api/health | head -c 80 && echo
'
```

Expected: `active`, then `{"ok":true,...`.

**Step 5:** Probe the new endpoint:

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    curl -fsS http://127.0.0.1:8001/api/capabilities | python3 -m json.tool
'
```

Expected: a JSON object with `providers` (4 entries: llama.cpp, lmstudio, ollama, openai-compatible) and `agent_providers` (2 entries: openai-compatible, anthropic), each provider object has the 8 capability fields, `version: 1`.

**Step 6:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add korina/routes/capabilities.py korina/app_factory.py
git commit -m "feat(capabilities): serve /api/capabilities

GET /api/capabilities returns the PROVIDER_CAPABILITIES registry split
into two sections (providers for response-LLM, agent_providers for the
agent path). Backed by Pydantic CapabilitiesResponse so OpenAPI
documents the wire format.

Verification (live):
  curl /api/capabilities -> 4 response-LLM providers, 2 agent providers
  curl /api/health      -> ok: true
  systemctl is-active   -> active"
```

### Task 2.1.4 — Regression smoke for `/api/capabilities`

**Files:** Modify `tests/regression_smoke.py` (add one check, bump total to 29)

**Step 1:** Open the file and find the existing `/api/health` check pattern. The tests are run sequentially as `test_01_health`, `test_02_config_keys`, etc. Find the highest-numbered check and add the new one after it. The exact insertion point depends on what's in the file; the contract is: append a new function with the next index, then bump the printed total in the run summary.

**Step 2:** Add a check (pattern matches the others; copy the URL construction and timeout style from a sibling check):

```python
def test_29_capabilities_endpoint():
    """GET /api/capabilities returns the full provider registry split by section."""
    import json, urllib.request
    with urllib.request.urlopen(BASE + "/api/capabilities", timeout=10) as r:
        assert r.status == 200
        body = json.loads(r.read().decode("utf-8"))
    assert body.get("version") == 1
    providers = body.get("providers") or {}
    agent_providers = body.get("agent_providers") or {}
    # response-LLM section: 4 known providers, none agent-only
    assert set(providers.keys()) == {"llama.cpp", "lmstudio", "ollama", "openai-compatible"}
    for pid, cap in providers.items():
        assert cap.get("agent_only") is False, f"{pid} leaked into response-LLM section"
        assert isinstance(cap.get("editable_base_url"), bool)
        assert isinstance(cap.get("manageable"), bool)
        assert isinstance(cap.get("is_local"), bool)
        assert isinstance(cap.get("model_sources"), list)
        assert cap.get("label"), f"{pid} missing label"
    # agent section: 2 known providers, includes the agent-only anthropic
    assert set(agent_providers.keys()) == {"openai-compatible", "anthropic"}
    assert agent_providers["anthropic"].get("agent_only") is True
    assert agent_providers["anthropic"].get("default_base_url") == ""
    assert agent_providers["anthropic"].get("editable_base_url") is True
    # local providers expose the canonical local URL
    assert providers["llama.cpp"]["default_base_url"] == "http://127.0.0.1:8080/v1"
    assert providers["lmstudio"]["default_base_url"]  == "http://127.0.0.1:1234/v1"
    assert providers["ollama"]["default_base_url"]    == "http://127.0.0.1:11434/v1"
```

**Step 3:** Find the run-summary string (look for `print("...passed N/M...")` near the bottom of the file) and bump the denominator from 28 to 29. Also update the module docstring at the top of the file if it lists the count.

**Step 4:** Compile + run:

```bash
cd /home/roggoz/Korina-Agent
python3 -m py_compile tests/regression_smoke.py
python3 tests/regression_smoke.py
```

Expected: `29/29 passed` (or whatever the existing run summary says, with +1). Any new failure = stop and report; do not bulldoze.

**Step 5:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add tests/regression_smoke.py
git commit -m "test: cover /api/capabilities contract

29/29 checks pass. Verifies:
  - response-LLM section has 4 providers, none agent-only
  - agent section has 2 providers, includes agent-only anthropic
  - editable_base_url / manageable / is_local are booleans
  - local providers expose the canonical local URL
  - anthropic.agent_only=true and default_base_url is empty"
```

---

## Step 2.2 — Frontend reads `/api/capabilities`

**Goal:** the JS `PROVIDER_BASE_URL_PRESETS` constant and the hardcoded `setBaseUrlEditability()` `llmProvider()=='openai-compatible'` check are gone. The frontend fetches `/api/capabilities` once at boot, caches it, and uses it for provider dropdowns, base-URL presets, and editability.

**Files:**

- Modify: `Korina/index.html` (delete inline preset, replace editability check, add boot fetch)
- Modify: `tests/regression_smoke.py` (add 2 frontend-shape checks; total becomes 30)

### Task 2.2.1 — Fetch and cache capabilities at boot

**Files:** Modify `Korina/index.html`

**Step 1:** Find the existing `PROVIDER_BASE_URL_PRESETS` block at `Korina/index.html:355-358` and replace it with a `capabilitiesCache` object + a `loadCapabilities()` function. The cache is populated by an async `loadCapabilities()` that returns a promise so callers can `await` it. Example replacement (drop in verbatim, then update the call sites in tasks 2.2.2 and 2.2.3):

```javascript
// Provider capability registry. Fetched once at boot from /api/capabilities.
// Cached in memory; the JS no longer owns provider rules.
let _capabilitiesCache = null;
async function loadCapabilities(force=false){
  if(_capabilitiesCache && !force){
    return _capabilitiesCache;
  }
  const r = await fetch('/api/capabilities');
  if(!r.ok){ throw new Error('capabilities fetch failed: '+r.status); }
  const j = await r.json();
  _capabilitiesCache = {
    providers: j.providers || {},
    agentProviders: j.agent_providers || {},
  };
  return _capabilitiesCache;
}
function getResponseLlmProviderCaps(provider){
  const caps = _capabilitiesCache?.providers || {};
  return caps[String(provider||'').trim()] || null;
}
function getAgentProviderCaps(provider){
  const caps = _capabilitiesCache?.agentProviders || {};
  return caps[String(provider||'').trim()] || null;
}
```

**Step 2:** Find the page-init block (the `DOMContentLoaded` listener or the inline `<script>`'s bottom-of-file init). Add a call so the fetch happens before the settings modal can open. The simplest pattern: a top-level `loadCapabilities().catch(e=>console.warn('capabilities load failed',e))` next to the existing init code. The cache is populated asynchronously; any function that needs it (`setBaseUrlEditability`, `maybeApplyProviderPreset`, the agent provider select) must `await loadCapabilities()` first.

**Step 3:** Keep `PROVIDER_BASE_URL_PRESETS` and `providerPresetBaseUrl()` as a temporary fallback for one more task — they're still used by 2.2.2 callers and need to keep working until 2.2.2 lands. The migration of consumers happens in 2.2.2 (response-LLM) and 2.2.3 (agent).

**Step 4:** Verify HTML still parses + extract the JS and run `node --check`:

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    python3 -c "
import re
with open(\"/home/roggoz/Korina-Agent/Korina/index.html\") as f:
    html = f.read()
m = re.search(r\"<script>([\\s\\S]*?)</script>\", html)
open(\"/tmp/index.html.js\",\"w\").write(m.group(1))
print(\"extracted\", len(m.group(1)), \"chars\")
"
    node --check /tmp/index.html.js && echo "JS syntax OK"
'
```

Expected: `JS syntax OK`.

**Step 5:** Commit (frontend still has the old presets fallback — the registry + fetch alone, not yet wired to all call sites):

```bash
cd /home/roggoz/Korina-Agent
git add Korina/index.html
git commit -m "feat(frontend): fetch /api/capabilities on boot

Adds loadCapabilities() and a getResponseLlmProviderCaps() /
getAgentProviderCaps() lookup pair. The fetched registry is cached for
the session. Old PROVIDER_BASE_URL_PRESETS stays in place as a
fallback until 2.2.2 lands; no consumer is migrated yet.

Verification: extracted <script> passes node --check."
```

### Task 2.2.2 — Migrate response-LLM + multimodal-STT consumers to the cache

**Files:** Modify `Korina/index.html`

**Step 1:** Replace the body of `providerPresetBaseUrl(provider)` to read from the cache. The function signature stays the same; only the body changes:

```javascript
function providerPresetBaseUrl(provider){
  const caps = getResponseLlmProviderCaps(provider);
  if(caps){ return String(caps.default_base_url || ''); }
  // Fallback during migration only; delete once 2.2.3 lands.
  return PROVIDER_BASE_URL_PRESETS[String(provider||'').trim()] || '';
}
```

**Step 2:** Replace `setBaseUrlEditability()` so it reads `editable_base_url` from the cache for both response-LLM and multimodal-STT. Find the function (currently at `Korina/index.html:371`):

```javascript
async function setBaseUrlEditability(){
  await loadCapabilities();
  // response-LLM
  const llmId = $('llmProvider')?.value || '';
  const llmCaps = getResponseLlmProviderCaps(llmId);
  const llmEditable = llmCaps ? !!llmCaps.editable_base_url : (llmId === 'openai-compatible');
  if($('llmBaseUrl')) $('llmBaseUrl').disabled = !llmEditable;
  // multimodal-STT (uses response-LLM provider list)
  const sttId = String($('sttLlmProvider')?.value || '').trim();
  const sttCaps = getResponseLlmProviderCaps(sttId);
  const sttEditable = sttCaps ? !!sttCaps.editable_base_url : (sttId === 'openai-compatible');
  if($('sttLlmBaseUrl')) $('sttLlmBaseUrl').disabled = !sttEditable;
  // agent (NEW: this was missing)
  const agentId = String($('agentProvider')?.value || '').trim();
  const agentCaps = getAgentProviderCaps(agentId);
  const agentEditable = agentCaps ? !!agentCaps.editable_base_url : (agentId === 'openai-compatible' || agentId === 'anthropic');
  if($('agentBaseUrl')) $('agentBaseUrl').disabled = !agentEditable;
}
```

**Step 3:** `setBaseUrlEditability()` is now async; update its 4 call sites to `await` (or use `.then()`) it. Search for `setBaseUrlEditability` and wrap each call: at `index.html:646` (settings open), `:676` (provider onchange), `:679` (sttLlmProvider onchange), `:680` (sttLlmBaseUrl onchange), `:701` (initial sync). The onchange handlers are already async so `await` is fine; the initial sync at `:701` needs to become an IIFE or live inside the same `loadCapabilities().then(...)` you set up in 2.2.1.

**Step 4:** Update `maybeApplyProviderPreset` to also use the cache. Find the function at `Korina/index.html:361-368` and replace its first lookup:

```javascript
function maybeApplyProviderPreset(providerId, baseUrlId, {clearWhenBlank=false}={}){
  const provider = $(providerId)?.value || '';
  const input = $(baseUrlId);
  if(!input) return false;
  // agent provider uses agent_providers section
  const isAgent = providerId === 'agentProvider';
  const caps = (isAgent ? getAgentProviderCaps(provider) : getResponseLlmProviderCaps(provider));
  const preset = caps ? String(caps.default_base_url || '') : providerPresetBaseUrl(provider);
  if(preset){ input.value = preset; return true; }
  if(clearWhenBlank && !provider){ input.value = ''; return true; }
  return false;
}
```

**Step 5:** Now that the cache is the only consumer of the registry, delete the old `PROVIDER_BASE_URL_PRESETS` constant (lines 355-358) and the fallback branch in `providerPresetBaseUrl`. The function simplifies to:

```javascript
function providerPresetBaseUrl(provider){
  const caps = getResponseLlmProviderCaps(provider);
  return caps ? String(caps.default_base_url || '') : '';
}
```

**Step 6:** Re-run the JS syntax check from task 2.2.1 step 4. Must say `JS syntax OK`.

**Step 7:** Sync to live, restart, smoke:

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    set -e
    cp /home/roggoz/Korina-Agent/Korina/index.html /home/roggoz/Korina/index.html
    systemctl --user restart korina-voice-lab.service
    sleep 1
    systemctl --user is-active korina-voice-lab.service
    curl -fsS http://127.0.0.1:8001/api/health | head -c 80 && echo
    # Open the settings modal via a headless check: hit the static index and
    # grep that the new code paths are present.
    curl -fsS http://127.0.0.1:8001/ | grep -c "loadCapabilities"
'
```

Expected: `active`, then `{"ok":true,...`, then a count >= 1 for `loadCapabilities` matches in the served HTML.

**Step 8:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add Korina/index.html
git commit -m "refactor(frontend): source response-LLM and STT provider rules from /api/capabilities

- providerPresetBaseUrl() reads from the capabilities cache.
- setBaseUrlEditability() reads editable_base_url from the cache for
  response-LLM, multimodal-STT, AND (now) the agent path. The agent
  path was previously uncontrolled -- this is the Phase 2 bugfix.
- maybeApplyProviderPreset() uses the cache, with agent section lookup
  when the source is agentProvider.
- Old inline PROVIDER_BASE_URL_PRESETS constant deleted.

Behavior preserved: response-LLM base URL is editable iff
provider is openai-compatible, identical to before. New behavior:
agent base URL is now correctly editable for openai-compatible AND
anthropic, and the field is properly disabled for any future
manageable local agent provider added to the registry."
```

### Task 2.2.3 — Regression smoke for frontend shape

**Files:** Modify `tests/regression_smoke.py` (add 2 checks; total becomes 30)

**Step 1:** Append two new checks after the capabilities check from task 2.1.4:

```python
def test_30_index_html_uses_capabilities():
    """The static index page must fetch /api/capabilities and not hardcode
    the old PROVIDER_BASE_URL_PRESETS constant."""
    import urllib.request, re
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        assert r.status == 200
        html = r.read().decode("utf-8")
    m = re.search(r"<script>([\s\S]*?)</script>", html)
    assert m, "no <script> block in index.html"
    js = m.group(1)
    # new: cache loader present
    assert "loadCapabilities" in js, "loadCapabilities() not present in index.html"
    assert "/api/capabilities" in js, "no /api/capabilities fetch in index.html"
    # old: presets constant should be gone
    assert "PROVIDER_BASE_URL_PRESETS" not in js, (
        "PROVIDER_BASE_URL_PRESETS still hardcoded; should be replaced by "
        "the capabilities cache."
    )


def test_31_capabilities_set_base_url_editability_for_agent():
    """setBaseUrlEditability must wire up the agent provider's base URL
    field. This is the regression check for the bug that the agent path
    was previously uncontrolled."""
    import urllib.request, re
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode("utf-8")
    m = re.search(r"<script>([\s\S]*?)</script>", html)
    js = m.group(1)
    # The agent section must be present in the editability function.
    assert re.search(r"setBaseUrlEditability[\s\S]{0,2000}agentBaseUrl", js), (
        "setBaseUrlEditability does not reference agentBaseUrl; the agent "
        "path is still uncontrolled."
    )
    assert "getAgentProviderCaps" in js, (
        "getAgentProviderCaps helper not present; agent editability not "
        "wired to /api/capabilities."
    )
```

**Step 2:** Bump the run-summary denominator from 29 to 30. (Keep the order: `test_29_capabilities_endpoint`, `test_30_index_html_uses_capabilities`, `test_31_capabilities_set_base_url_editability_for_agent` — yes, two new tests but the run summary still says 30 because two earlier numerics were left as one logical check. Use whatever convention the rest of the file uses; if the file is `N/N passed` then bump N to 30.)

**Step 3:** Run the suite:

```bash
cd /home/roggoz/Korina-Agent
python3 tests/regression_smoke.py
```

Expected: 30/30 (or 30/N where N=30) passed. Any new failure = stop and report.

**Step 4:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add tests/regression_smoke.py
git commit -m "test: cover /api/capabilities frontend wiring

30 checks pass. Verifies:
  - index.html fetches /api/capabilities and defines loadCapabilities
  - old PROVIDER_BASE_URL_PRESETS constant is gone
  - setBaseUrlEditability now wires up agentBaseUrl (the bugfix)
  - getAgentProviderCaps helper is present"
```

---

## Step 2.3 — Provider switch contract documented + edge cases

**Goal:** the contract for "user picks a provider in the UI" is documented once, in one place future work can refer to. Edge cases that 2.2 may have missed (anthropic base URL on first selection, model dropdown reset on provider change) are verified.

**Files:**

- Create: `docs/capabilities.md` (the contract)
- Modify: `tests/regression_smoke.py` (add an edge-case check; total becomes 31)

### Task 2.3.1 — Write `docs/capabilities.md`

**Files:** Create `docs/capabilities.md`

**Step 1:** Write the file. Concrete contents:

```markdown
# Provider & Model Capability Contract

Single source of truth for which provider the user has selected, what
base URL is in play, which models the dropdown should show, and which
fields the UI may edit. This contract is the canonical answer to
"where does provider X's base URL come from?" -- if you find yourself
asking that, read this first.

## Endpoints

- `GET /api/capabilities` -- the registry. See `korina/schemas_capabilities.py`
  for the wire format. Two sections:
  - `providers` -- valid for the response-LLM path. Today:
    `llama.cpp`, `lmstudio`, `ollama`, `openai-compatible`.
  - `agent_providers` -- valid for the agent path. Today:
    `openai-compatible`, `anthropic`.

  Each entry has 8 fields: `label`, `default_base_url`,
  `editable_base_url`, `manageable`, `model_sources`, `is_local`,
  `agent_only`, `response_llm_only`.

- `POST /api/llm/provider/activate` -- switches the local inference
  server when the response-LLM provider is `manageable: true`. The
  request body is `{provider, model}`; the response includes
  `activation.started` and `activation.stopped` lists.

## Invariants

1. **The backend owns provider rules.** No JS file may hardcode
   "llama.cpp -> http://127.0.0.1:8080/v1" or
   "openai-compatible -> base URL is editable". Both are derived from
   `/api/capabilities`.

2. **A provider is editable iff `editable_base_url: true`.** The UI
   disables the base-URL input otherwise. Today that means:
   - llama.cpp, lmstudio, ollama: base URL is locked.
   - openai-compatible, anthropic: base URL is editable.

3. **manageable providers get a server lifecycle.** When the user
   picks a `manageable: true` response-LLM provider, the UI must POST
   `/api/llm/provider/activate` *after* saving the new config and
   *before* refetching `/api/models`. The activation response may
   include `{started, stopped}` so the UI can show what changed.

4. **Agent-only providers never appear in `providers`.** Today only
   `anthropic` is agent-only; if a future agent-only provider is
   added (e.g. `gemini-agent`), it goes in `agent_providers` only
   and gets `agent_only: true`.

5. **`/api/capabilities` is fetch-once.** The frontend caches the
   response for the session. There is no `/api/capabilities/refresh`
   endpoint; if the registry changes, the user reloads the page.
   (This is a Phase 2 simplification; a future phase may add an
   explicit refresh if a hot-swap use case shows up.)

6. **The capability key is always snake_case in JSON.** `editable_base_url`
   in the wire format, `editableBaseUrl` only in the JS object
   (one-shot rename at the API boundary in `loadCapabilities`).

## Adding a new provider

1. Add an entry to `PROVIDER_CAPABILITIES` in `korina/util/presets.py`.
   Pick the right section:
   - Response-LLM only: set `response_llm_only: true`.
   - Agent only: set `agent_only: true`.
   - Both: leave both flags false.
2. If the provider is local (`is_local: true`), set `manageable: true`
   AND add a corresponding branch to `start_lmstudio/stop_lmstudio` in
   `korina/services/provider_manager.py`. The Phase 1 walkthrough
   for `activate_llm_provider` is in
   `references/provider-lifecycle.md` (TODO if not yet written).
3. If the provider needs a non-default auth header, extend
   `auth_headers_from_env` in `korina/runtime/http.py`. (Most don't.)
4. Run `python3 tests/regression_smoke.py`. The capability check
   (test_29) hard-codes the expected provider set; if you add or
   remove a provider, update the assertion.
5. Commit on `beta`; do not touch `alpha` or `master` until release.

## Provider switch user flow (response-LLM)

1. User opens settings modal. Frontend calls `loadCapabilities()`
   on first open if the cache is empty.
2. User picks a new provider in `#llmProvider`.
3. UI:
   - Calls `maybeApplyProviderPreset('llmProvider','llmBaseUrl')`
     to set the base URL to the registry default.
   - Calls `setBaseUrlEditability()` to lock/unlock the base URL.
   - Calls `syncConverseSettingsUI()` to refresh status pills.
   - `await saveConfigNow()` to persist.
   - `await activateSelectedProvider(llmProvider(), lmModel())` to
     start/stop the local server.
   - `await loadModelOptions(true)` to refresh the model dropdown.
   - `await loadAgentModelOptions()` to refresh the agent dropdown.
   - `health()` to refresh the status text.
4. The user-visible text: "Activated llama.cpp. Started llama-server;
   stopped nothing." (or similar; matches today's behavior).

## Provider switch user flow (agent)

1. User picks a new agent provider in `#agentProvider`.
2. UI:
   - `maybeApplyProviderPreset('agentProvider','agentBaseUrl')`.
   - `setBaseUrlEditability()` (now also updates `agentBaseUrl`).
   - `saveConfigSoon()`.
3. There is no `activate_agent_provider` endpoint. The agent runs on
   demand in `generate_agent_state_report`; the user only needs to
   set the base URL and model. (This is the current behavior and
   Phase 2 preserves it.)

## Out of scope for Phase 2

- Per-model capability metadata (audio_input, mmproj, vram) -- Phase 4.
- Provider/model compatibility matrix -- Phase 4.
- Live reload of `/api/capabilities` -- only on full page reload.
- Removing the response-LLM provider from the agent path -- the
  registry already allows this via `response_llm_only: true`; we
  just don't set it on any current provider.
```

**Step 2:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add docs/capabilities.md
git commit -m "docs: provider switch contract for Phase 2 capabilities

Single place future work can read to know what the UI/backend contract
is. Covers endpoints, invariants, new-provider checklist, and the
two user flows (response-LLM and agent). References /api/capabilities
and POST /api/llm/provider/activate."
```

### Task 2.3.2 — Edge-case regression check

**Files:** Modify `tests/regression_smoke.py` (add 1 check; total becomes 31)

**Step 1:** Append a check that exercises the agent provider's anthropic base URL is empty by default, and the response-LLM openai-compatible path is editable:

```python
def test_32_capabilities_anthropic_default_and_editability():
    """Anthropic agent provider has an empty default base URL (user must
    supply it) and is editable. openai-compatible response-LLM is
    editable; llama.cpp is not. This pins the editable_base_url contract."""
    import json, urllib.request
    with urllib.request.urlopen(BASE + "/api/capabilities", timeout=10) as r:
        body = json.loads(r.read().decode("utf-8"))
    a = body["agent_providers"]["anthropic"]
    assert a["default_base_url"] == ""
    assert a["editable_base_url"] is True
    assert a["is_local"] is False
    assert a["agent_only"] is True
    # editable_base_url must match the legacy hardcoded check
    # (openai-compatible editable; llama.cpp not editable) so frontend
    # behavior is identical for current users.
    p = body["providers"]
    assert p["openai-compatible"]["editable_base_url"] is True
    assert p["llama.cpp"]["editable_base_url"] is False
    assert p["lmstudio"]["editable_base_url"] is False
    assert p["ollama"]["editable_base_url"] is False
    # Every provider must declare the same 8 keys (no missing fields).
    expected_keys = {
        "label", "default_base_url", "editable_base_url", "manageable",
        "model_sources", "is_local", "agent_only", "response_llm_only",
    }
    for section in (p, body["agent_providers"]):
        for pid, cap in section.items():
            missing = expected_keys - set(cap.keys())
            assert not missing, f"{pid} missing keys: {sorted(missing)}"
```

**Step 2:** Bump the run-summary denominator to 31.

**Step 3:** Run:

```bash
cd /home/roggoz/Korina-Agent
python3 tests/regression_smoke.py
```

Expected: 31/31 passed.

**Step 4:** Commit:

```bash
cd /home/roggoz/Korina-Agent
git add tests/regression_smoke.py
git commit -m "test: pin editable_base_url contract across all providers

31/31 checks pass. Verifies:
  - anthropic default_base_url is empty (user must supply)
  - anthropic editable_base_url is true
  - openai-compatible editable; llama.cpp/lmstudio/ollama not
  - all providers declare the same 8 capability keys (no field drift)"
```

---

## Step 2.4 — Phase 2 verification

**Goal:** confirm end-to-end that the live service and the live UI behave identically for current users, that the new endpoint and JS wiring work, and that the agent provider bug is fixed.

**Files:** none; this is a verification + final commit only.

### Task 2.4.1 — Full live regression

**Step 1:** Push the work-in-progress branch and verify it landed:

```bash
cd /home/roggoz/Korina-Agent
git push origin beta
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    git -C /home/roggoz/Korina-Agent ls-remote --heads origin beta
    git -C /home/roggoz/Korina-Agent rev-parse --abbrev-ref --symbolic-full-name @{u}
    git -C /home/roggoz/Korina-Agent log --oneline -8
'
```

Verify with the recipe in the `korina-converse-dev` skill: `git ls-remote --heads origin beta` should equal `git rev-parse beta` (no gap, no pending push). Do NOT just trust the push output.

**Step 2:** Sync the live runtime and restart:

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    set -e
    # Mirror the package, not just the changed files
    rm -rf /home/roggoz/Korina/korina
    cp -r /home/roggoz/Korina-Agent/korina /home/roggoz/Korina/korina
    cp /home/roggoz/Korina-Agent/Korina/index.html /home/roggoz/Korina/index.html
    cp /home/roggoz/Korina-Agent/tests/regression_smoke.py /home/roggoz/Korina/tests/regression_smoke.py
    systemctl --user restart korina-voice-lab.service
    sleep 1
    systemctl --user is-active korina-voice-lab.service
    curl -fsS http://127.0.0.1:8001/api/health | head -c 80 && echo
    # Confirm the new route exists
    curl -fsS http://127.0.0.1:8001/api/capabilities | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d[\"version\"] == 1
assert \"llama.cpp\" in d[\"providers\"]
assert \"anthropic\" in d[\"agent_providers\"]
print(\"capabilities OK\")
"
'
```

Expected: `active`, `{"ok":true,...`, `capabilities OK`.

**Step 3:** Run the regression suite on live:

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net 'cd /home/roggoz/Korina-Agent && python3 tests/regression_smoke.py'
```

Expected: 31/31 passed. Stop and report any new failure.

**Step 4:** Functional smoke (provider lifecycle still works end-to-end):

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    set -e
    # Switch response provider to llama.cpp
    MODEL=$(ls /home/roggoz/Disks/SN750/models/lmstudio-community/gemma-4-E2B-it-GGUF/*.gguf | head -1)
    curl -fsS -X POST http://127.0.0.1:8001/api/llm/provider/activate \
        -H "Content-Type: application/json" \
        -d "{\"provider\":\"llama.cpp\",\"model\":\"$MODEL\"}" \
        | python3 -m json.tool | head -20
    # /api/chat must still work
    curl -fsS -X POST http://127.0.0.1:8001/api/chat \
        -H "Content-Type: application/json" \
        -d "{\"message\":\"hi\",\"history\":[]}" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(\"chat ok:\", d.get(\"reply\",\"NO REPLY\")[:60])"
'
```

Expected: provider activation JSON, then `chat ok: <something>`.

**Step 5:** Functional smoke (OpenAPI is stable — only the new path was added):

```bash
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net '
    curl -fsS http://127.0.0.1:8001/openapi.json | python3 -c "
import json, sys
spec = json.load(sys.stdin)
paths = sorted(spec[\"paths\"].keys())
print(\"path count:\", len(paths))
assert \"/api/capabilities\" in paths
print(\"new path added:\", \"/api/capabilities\")
for p in [\"/api/health\",\"/api/config\",\"/api/models\",\"/api/chat\",\"/api/transcribe\",\"/api/acks\",\"/api/agent/events\",\"/api/llm/provider/activate\"]:
    assert p in paths, f\"missing prior path: {p}\"
print(\"all prior paths present\")
"
'
```

Expected: prior path count + 1 (with `/api/capabilities` added) and every prior path present.

**Step 6:** Commit (no code change, just a phase marker if the user wants one — skip if there's no other change):

```bash
# If there are no uncommitted changes, skip this commit. The phase
# close-out is recorded in PROGRESS.md in the next task.
```

### Task 2.4.2 — Update PROGRESS.md

**Files:** Modify `docs/refactor/PROGRESS.md`

**Step 1:** Mark the four Phase 2 entries as complete with evidence:

```markdown
## Phase 2 — Single source of truth

- [✓] 2.1 capabilities endpoint — `/api/capabilities` serves the
  `PROVIDER_CAPABILITIES` registry split into `providers` and
  `agent_providers`; Pydantic schema in `korina/schemas_capabilities.py`;
  Pydantic-served at `korina/routes/capabilities.py`; registry in
  `korina/util/presets.py`. 4 response-LLM providers, 2 agent providers.
- [✓] 2.2 frontend reads capabilities — `loadCapabilities()` caches the
  registry; `setBaseUrlEditability()` now controls the agent base URL
  field (was uncontrolled); `maybeApplyProviderPreset()` and
  `providerPresetBaseUrl()` read from the cache. Old
  `PROVIDER_BASE_URL_PRESETS` constant removed.
- [✓] 2.3 switch contract documented — `docs/capabilities.md` is the
  canonical reference. Covers endpoints, invariants, new-provider
  checklist, response-LLM and agent user flows, and what's out of
  scope for Phase 2.
- [✓] 2.4 verify — full live regression passed 31/31 on roggoz;
  `/api/capabilities` returns 200; `/api/chat` still works after
  provider activation; OpenAPI path count is Phase 1 baseline + 1
  (`/api/capabilities`) and all prior paths remain.

**Phase 2 result:** single source of truth for provider rules is live.
The agent provider's base-URL field is now properly controlled by
`/api/capabilities` (the bug the blueprint flagged). Response-LLM
provider behavior is identical to Phase 1 for current users.
```

**Step 2:** Update the **Latest verified Phase 2 commit** header:

```
**Latest verified Phase 2 commit:** `<sha from git log>` — `docs: provider switch contract for Phase 2 capabilities`
```

(replace `<sha from git log>` with the actual SHA from `git log --oneline | head -1` after the last commit)

**Step 3:** Commit + push:

```bash
cd /home/roggoz/Korina-Agent
git add docs/refactor/PROGRESS.md
git commit -m "docs: mark Phase 2 complete in PROGRESS.md"
git push origin beta
ssh -F /dev/null -o User=roggoz -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \
    roggoz.bunny-bowfin.ts.net 'git ls-remote --heads origin beta'
```

Verify the push landed; the SHA should match the local `beta` tip.

---

## End-to-end verification checklist (run before declaring done)

- [ ] `python3 -m py_compile` clean for every touched file.
- [ ] `node --check` clean for the extracted `<script>` block.
- [ ] `python3 tests/regression_smoke.py` -> 31/31.
- [ ] `git push origin beta` -> `git ls-remote --heads origin beta` matches.
- [ ] `/api/health` -> `ok: true`.
- [ ] `/api/capabilities` -> 200, both sections present.
- [ ] `/api/chat` round-trip after `POST /api/llm/provider/activate` returns 200.
- [ ] `/openapi.json` -> prior 19 paths + 1 new (`/api/capabilities`), no removals.
- [ ] Live UI: settings modal opens; provider dropdown changes update base-URL
  field + editability; agent provider dropdown now also controls the
  agent base-URL field (the new behavior).
- [ ] `docs/refactor/PROGRESS.md` updated.
- [ ] No new runtime dependencies.
- [ ] No new globals in `korina/runtime/state.py`.
- [ ] Live runtime `/home/roggoz/Korina/` mirrors source (only `config.json` differs, as expected).

## Pitfalls (from `korina-converse-dev` skill + Phase 1 lessons)

- **Never delete live runtime data.** `config.json`, `Ack/*.wav`, live
  `index.html.bak-*` snapshots are user data. Verify-regen paths use
  temp copies, not the live file.
- **Mirror the whole `korina/` package on sync, not just changed files.**
  After every package-creation step, `rm -rf /home/roggoz/Korina/korina
  && cp -r /home/roggoz/Korina-Agent/korina /home/roggoz/Korina/korina`.
  Otherwise Python may not find new submodules.
- **Don't conflate Phase 2 with Phase 4.** Phase 2 is metadata about
  providers; Phase 4 is metadata about individual models
  (audio_input, mmproj). The 8 registry keys are deliberately about
  providers, not models.
- **Don't add `/api/capabilities/refresh`.** Fetch-once is the design
  decision (see contract §5). Hot-reload is a future phase.
- **Don't change route paths or response shapes** in any sibling route.
  The OpenAPI baseline from Phase 1 must stay at 19 + 1.
- **Don't ship this to `alpha` or `master`.** Target is `beta` only
  per the user's directive.
- **Don't conflate the registry with `/api/models`.** `/api/capabilities`
  is static provider metadata; `/api/models` is dynamic model lists.
  Two different endpoints, two different purposes.
- **Stop and report on any regression failure.** Do not bulldoze through
  2.4 if 2.1–2.3's checks fail. The user has repeatedly endorsed
  "stop and report" as the correct move.

## Open question for after Phase 2

- Should `docs/refactor/PROGRESS.md` move to `docs/REFACTOR_PROGRESS.md`
  at the repo root so it's discoverable without reading the refactor
  directory? Phase 2 doesn't need to answer this, but flagging it.
```
