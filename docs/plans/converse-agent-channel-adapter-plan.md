# Plan: Converse Agent Channel Adapter Separation

## Overview

Refactor Korina Converse from a fused Converse+Agent monolith into a Converse
channel that speaks to pluggable agent backends via a stable `AgentAdapter`
protocol. Korina Agent becomes `KorinaAgentAdapter`. Future adapters (Hermes,
OpenClaw) are added as new files without touching Converse.

**Constraints:**
- Never break the live roggoz service.
- Preserve all `/api/agent/*` endpoints as aliases during migration.
- Migrate flat `agent_*` config keys with backward-compat aliases.
- Never touch runtime data (`config.json`, `Ack/`, `logs/`).
- Phase 0 baseline must be captured before Phase 1 begins.

---

## Phase 0 — Baseline Inventory

**Goal:** Know exactly what exists before touching anything.

### 0.1 — Test baseline

```bash
cd /home/roggoz/Korina-Agent
python3 -m pytest -q 2>&1 | tee /tmp/baseline-pytest.txt
```

### 0.2 — Inventory state.agent accessors

```bash
grep -rn "state\.agent" korina/ > /tmp/state-agent-refs.txt
```

### 0.3 — Inventory agent_* config keys

Config keys in `korina/config.py` are snake_case:
`agent_provider`, `agent_model`, `agent_yolo_mode`, `agent_thinking_level`,
`agent_auto_compact`, `agent_transport`, etc.

```bash
grep -rn "agent_" korina/config.py > /tmp/agent-config-keys.txt
grep -n "agent" Korina/js/settings-ui.js | head -80
```

### 0.4 — Inventory agent_service callers

```bash
grep -rl "from korina.services.agent_service\|import agent_service" korina/ Korina/
```

### 0.5 — Snapshot /api/agent/* response shapes

```bash
curl -s http://127.0.0.1:8001/api/agent/status | python3 -m json.tool
curl -s http://127.0.0.1:8001/api/agent/events | python3 -m json.tool
curl -s http://127.0.0.1:8001/api/agent/models | python3 -m json.tool
```

### 0.6 — Commit Phase 0 artefacts

```bash
mkdir -p docs/inventory
cp /tmp/baseline-pytest.txt docs/inventory/
cp /tmp/state-agent-refs.txt docs/inventory/
cp /tmp/agent-config-keys.txt docs/inventory/
git add docs/inventory/
git commit -m "docs(phase0): baseline inventory"
```

**Exit gate:** All Phase 0 artefacts committed to alpha.

---

## Phase 1 — Schema Contract

**Goal:** Define wire protocol in `korina/schemas.py`.
Pydantic v2 confirmed on roggoz (`pydantic==2.13.0`).

```python
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

# Inbound: Converse -> adapter
class UserTurn(BaseModel):
    text: str
    session_id: str
    turn_count: int
    audio_seconds: float | None = None
    timestamp: float = Field(default_factory=time.time)

# Outbound: adapter -> Converse
class AgentEvent(BaseModel):
    id: int
    type: str  # "state_report" | "injection" | "permission_request" | ...
    priority: str = "normal"
    payload: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")  # tolerate extra fields from existing event dicts

class PermissionAnswerEvent(BaseModel):
    request_id: str
    answer: Literal["yes", "no"]
    transcript: str

class AgentCapabilities(BaseModel):
    adapter_id: str
    supports_state_reports: bool = True
    supports_injections: bool = True
    supports_permission_flow: bool = True
    supports_streaming: bool = False
    http_compatible: bool = False
```

**Steps:**
1. Add types above to `korina/schemas.py`.
2. `python3 -m pytest -q` — must pass.
3. Commit: `feat(schemas): add AgentContract types`

**Exit gate:** Tests pass; types import cleanly.

---

## Phase 2 — AgentAdapter Protocol + Gateway

**Goal:** Extract a typed interface any adapter must implement; create the
registry that selects the active adapter at runtime.

### korina/agents/base.py

```python
from typing import Protocol, runtime_checkable
from korina.schemas import (
    UserTurn, AgentEvent, PermissionAnswerEvent, AgentCapabilities,
)

@runtime_checkable
class AgentAdapter(Protocol):
    id: str
    capabilities: AgentCapabilities

    def submit_turn(self, turn: UserTurn) -> None: ...
    def poll_events(self, cursor: int) -> tuple[int, list[AgentEvent]]: ...
    def answer_permission(self, answer: PermissionAnswerEvent) -> None: ...
    def reset(self) -> None: ...
    def health_check(self) -> dict: ...
```

### korina/agents/gateway.py

```python
"""AgentGateway — runtime-selected adapter injector."""
from korina.agents.base import AgentAdapter

_agent: AgentAdapter | None = None

def register_adapter(adapter: AgentAdapter) -> None:
    global _agent
    _agent = adapter

def agent_gateway() -> AgentAdapter:
    if _agent is None:
        from korina.agents.korina_adapter import KorinaAgentAdapter
        register_adapter(KorinaAgentAdapter())
    return _agent
```

### korina/agents/korina_adapter.py

Wraps current `agent_service` logic. Does NOT move `AgentState` yet —
that happens in Phase 4. Wraps flat dict events into `AgentEvent`
with `model_config = ConfigDict(extra="allow")` so field mismatches are tolerated.

**Steps:**
1. Create `korina/agents/{__init__.py, base.py, gateway.py, korina_adapter.py}`.
2. `python3 -m py_compile` each file.
3. `python3 -c "from korina.agents import AgentAdapter, agent_gateway; print('ok')"`.
4. `python3 -m pytest -q`.
5. Commit: `feat(agents): AgentAdapter protocol + gateway`

**Exit gate:** All tests pass; `KorinaAgentAdapter` registered as default.

---

## Phase 3 — Route Layer Migration

**Goal:** `routes/chat.py` and `routes/agent.py` call `agent_gateway()` instead
of `state.agent` directly. Keep all endpoint paths and response shapes unchanged.

**Changes to `korina/routes/chat.py`:**
- Replace direct state mutations with `agent_gateway().submit_turn(...)`.

**Changes to `korina/routes/agent.py`:**
- Every `state.agent.X` mutation goes through `agent_gateway()`.
- Paths unchanged: `/api/agent/status`, `/api/agent/events`, etc. remain compat aliases.
- Read `agents.current` from config to select adapter at startup (Phase 6 wires this).

**Steps:**
1. Audit all `state.agent` references from Phase 0 inventory.
2. Patch each one to use gateway.
3. `python3 -m py_compile korina/routes/chat.py korina/routes/agent.py`.
4. Smoke: `curl -s http://127.0.0.1:8001/api/agent/status | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['ok']"`.
5. `python3 -m pytest -q`.
6. Commit: `refactor(routes): chat + agent routes use AgentGateway`

**Exit gate:** Agent status endpoint identical to Phase 0 snapshot.

---

## Phase 4 — Runtime State Cleanup

**Goal:** `AgentState` moves from `korina/runtime/state.py` into
`KorinaAgentAdapter._state`. No `state.agent` remains in the codebase.

This is done AFTER Phase 3 because Phase 3 is the safety net — routes
already use the gateway, so the adapter is the only caller of state.agent.

**Changes:**
- Move `event_seq`, `events`, `pending_injections`, `busy`, `status`,
  `last_report`, `last_error` into `KorinaAgentAdapter._state`.
- Remove `AgentState` class from `korina/runtime/state.py`.
- All remaining `state.agent` references (found in Phase 0) now go through
  `agent_gateway()._state.*` — patch each one.

**Steps:**
1. Move state fields into adapter.
2. Remove `AgentState` from `korina/runtime/state.py`.
3. `python3 -m pytest -q` — catches broken references.
4. Commit: `refactor(state): AgentState internalized in KorinaAgentAdapter`

**Exit gate:** No `state.agent` references in `korina/` package; tests pass.

---

## Phase 5 — Frontend: Conditional Agent Loading

**Goal:** `agent-ui.js` loads only when an agent is enabled; adapter-specific
settings panels are lazy.

### app.js

Current (always loads agent):
```javascript
import { initAgentUi } from './agent-ui.js';
initAgentUi();
```

Replace with:
```javascript
const cfg = await health();
if (cfg.agent_enabled !== false) {
  const { initAgentUi } = await import('./agent-ui.js');
  initAgentUi();
}
```

### settings-ui.js + index.html

Add adapter selector (dropdown) at the top of the agent settings section.
Adapter-specific panels are wrapped:

```html
<div id="agent-korina-panel" class="agent-panel">...</div>
<div id="agent-hermes-panel" class="agent-panel" style="display:none">
  <label>Base URL <input id="hermesBaseUrl" ...></label>
</div>
```

`showAdapterFields(selected)` hides all `.agent-panel`, shows `#agent-{selected}-panel`.

**Note:** Current `agent-ui.js` expects dict-shaped events from `/api/agent/events`.
Keep emitting the same dict shape (Phase 3 compat aliases preserve this). The adapter
wraps typed `AgentEvent` back into dict for the frontend — no frontend change needed.

**Steps:**
1. Patch `app.js` to conditional-import `agent-ui.js`.
2. Add adapter selector + panel wrappers to `index.html`.
3. `showAdapterFields()` in `settings-ui.js`.
4. Frontend static tests: `python3 tests/frontend/test_static_frontend.py`.
5. Commit: `feat(frontend): agent UI conditional on adapter selection`

**Exit gate:** With agent disabled, no `/api/agent/*` fetch fires on page load.

---

## Phase 6 — Config Namespace Migration

**Goal:** Flat `agent_*` keys -> `agents: { current, adapters: {...} }` with
backward-compat aliases so existing roggoz installs keep working.

**Current flat keys (from Phase 0):**
`agent_provider`, `agent_model`, `agent_yolo_mode`, `agent_thinking_level`,
`agent_auto_compact`, `agent_transport`, `agent_enabled`, etc.

**New namespace:**

```json
{
  "agents": {
    "current": "korina",
    "adapters": {
      "korina": {
        "provider": "openai-compatible",
        "model": "",
        "yolo_mode": false,
        "thinking_level": "low",
        "auto_compact": true,
        "transport": "auto",
        "enabled": true
      },
      "hermes": {
        "base_url": "http://localhost:8080",
        "auth_token": ""
      }
    }
  }
}
```

**Migration shim in config.py:**

```python
def _migrate_agent_config(cfg: dict) -> dict:
    if "agents" not in cfg:
        flat = {k: v for k, v in cfg.items() if k.startswith("agent_")}
        cfg["agents"] = {"current": "korina", "adapters": {"korina": flat}}
    return cfg
```

Called at config load time. Old keys continue to work on read.

**Steps:**
1. Add `_migrate_agent_config()` to `korina/config.py`.
2. Update settings UI to read/write new namespace.
3. Restart service, verify: `curl -s http://127.0.0.1:8001/api/config | python3 -c "import json,sys; assert 'agents' in json.load(sys.stdin)"`.
4. Full test suite.
5. Commit: `feat(config): agent config namespace + backward-compat migration`

**Exit gate:** Old `config.json` (flat keys) loads without error; new config saves correctly.

---

## Phase 7 — HTTP Adapter Skeleton

**Goal:** Define the HTTP wire contract; stub `HermesAgentAdapter` so the
frontend can select it even before the remote is wired.

### Wire contract

| Converse -> Agent | Method | Body |
|---|---|---|
| Submit turn | `POST /api/turn` | `UserTurn` JSON |
| Poll events | `GET /api/events?cursor=N` | -> `{"cursor": N, "events": [...]}` |
| Permission answer | `POST /api/permission` | `PermissionAnswerEvent` JSON |
| Reset | `POST /api/reset` | `{}` |
| Health | `GET /api/health` | -> `{"ok": true}` |

### korina/agents/hermes_adapter.py

```python
import httpx
from korina.schemas import UserTurn, AgentEvent, PermissionAnswerEvent, AgentCapabilities
from korina.agents.base import AgentAdapter

class HermesAgentAdapter:
    id = "hermes"
    capabilities = AgentCapabilities(
        adapter_id="hermes",
        supports_state_reports=True,
        supports_injections=True,
        supports_permission_flow=True,
        supports_streaming=False,
        http_compatible=True,
    )

    def __init__(self, base_url: str, auth_token: str = ""):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

    def submit_turn(self, turn: UserTurn) -> None:
        httpx.post(f"{self._base}/api/turn", json=turn.model_dump(), headers=self._headers, timeout=10)

    def poll_events(self, cursor: int) -> tuple[int, list[AgentEvent]]:
        r = httpx.get(f"{self._base}/api/events", params={"cursor": cursor}, headers=self._headers, timeout=10)
        data = r.json()
        return data["cursor"], [AgentEvent(**e) for e in data.get("events", [])]

    def answer_permission(self, answer: PermissionAnswerEvent) -> None:
        httpx.post(f"{self._base}/api/permission", json=answer.model_dump(), headers=self._headers, timeout=10)

    def reset(self) -> None:
        httpx.post(f"{self._base}/api/reset", headers=self._headers, timeout=10)

    def health_check(self) -> dict:
        r = httpx.get(f"{self._base}/api/health", timeout=5)
        return r.json()
```

**Note:** `httpx` is in `[project.optional-dependencies] test` but not main deps.
If used in production adapter, add to main deps in `pyproject.toml`.

**Register** in `gateway.py`: reads `base_url`, `auth_token` from
`config["agents"]["adapters"]["hermes"]` at register time.

**Steps:**
1. Write `korina/agents/hermes_adapter.py`.
2. Add `httpx` to main deps in `pyproject.toml`.
3. `python3 -m py_compile korina/agents/hermes_adapter.py`.
4. Register in gateway.
5. Tests pass.
6. Commit: `feat(agents): HermesAgentAdapter HTTP skeleton`

**Exit gate:** Adapter selectable in frontend settings; HTTP calls fire when Hermes is reachable.

---

## Phase 8 — Compatibility Aliases + Regression Gate

**Goal:** Ensure no existing caller (frontend, scripts) breaks.

All `/api/agent/*` endpoints keep their current paths and response shapes.
Route handlers delegate to `agent_gateway()` — this was done in Phase 3.

**Regression test:**
```bash
python3 tests/regression_smoke.py --no-chat --no-transcribe
```

**Steps:**
1. Diff all `/api/agent/*` responses against Phase 0 snapshots.
2. Any shape change must be intentional and documented.
3. Commit: `chore(agent): compatibility aliases verified`

---

## Phase 9 — Dogfood on Roggoz

**Per `korina-converse-dev` skill workflow:**

1. Mirror to live: `bash scripts/phase2_autopilot.sh` (runs `mirror_live()`).
2. Restart: `systemctl --user restart korina-voice-lab.service`.
3. Verify: `curl -fsS http://127.0.0.1:8001/api/health | python3 -c "import json,sys; assert json.load(sys.stdin)['ok']"`.
4. Agent-browser QA (per dogfood skill).
5. Push beta when stable.

---

## File Map

```
korina/
  schemas.py                    # Phase 1: add UserTurn, AgentEvent, PermissionAnswerEvent, AgentCapabilities
  agents/
    __init__.py               # Phase 2
    base.py                   # Phase 2: AgentAdapter Protocol
    gateway.py                # Phase 2: register_adapter(), agent_gateway()
    korina_adapter.py         # Phase 2: KorinaAgentAdapter (wraps agent_service)
    hermes_adapter.py         # Phase 7: HermesAgentAdapter (HTTP)
  runtime/
    state.py                  # Phase 4: remove AgentState
  routes/
    chat.py                  # Phase 3: submit through gateway
    agent.py                 # Phase 3: all ops through gateway; compat aliases
  config.py                   # Phase 6: _migrate_agent_config()
Korina/
  js/
    app.js                   # Phase 5: conditional import of agent-ui.js
    settings-ui.js           # Phase 5: adapter selection + showAdapterFields()
    agent-ui.js              # Phase 5: unchanged shape
  index.html                 # Phase 5: adapter panel wrappers
  styles.css                 # Phase 5: .agent-panel CSS (minimal)
pyproject.toml               # Phase 7: add httpx to deps if needed
docs/
  issues/001-converse-agent-boundary.md   # Repo-tracked issue
  plans/converse-agent-channel-adapter-plan.md  # This plan
  inventory/                            # Phase 0 artefacts
```

## Rollback

```bash
cd /home/roggoz/Korina-Agent
git log --oneline -3   # find last good commit
git reset --hard <sha>
bash scripts/phase2_autopilot.sh
systemctl --user restart korina-voice-lab.service
```
