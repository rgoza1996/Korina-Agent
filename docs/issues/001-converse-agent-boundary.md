# Issue: Converse Should Be a Generic Chat/Voice Channel

## Status

Closed by GitHub issue: https://github.com/rgoza1996/Korina-Agent/issues/1
Labels: architecture, refactor, agent-adapter, converse
Milestone: Converse channel compatibility

---

## Problem

Korina Converse is intended to be a chat/voice channel compatible with Hermes Agent,
OpenClaw, Korina Agent, and any future agent backend. The current codebase does not
support this: Converse and Korina Agent are fused into one FastAPI app, one Python
package, one frontend bundle, and one shared mutable singleton.

---

## Evidence

1. **One app, one package.** `korina/app_factory.py` registers `_agent_routes`
   alongside chat, STT, providers, and ack routes in a single FastAPI instance.
   `pyproject.toml` ships only `korina*`. There is no `korina-converse` or
   `korina-channel` package.

2. **Shared mutable singleton.** `korina/runtime/state.py` holds `AsrState`,
   `AckState`, and `AgentState` as flat siblings on a single `RuntimeState()`.
   `routes/chat.py` and `routes/agent.py` both mutate `state.agent.*` directly.
   Converse does not call an external agent — it owns the agent event bus internally.

3. **Frontend unconditionally wires agent.** `Korina/js/app.js` imports and
   initialises all of `agent-ui.js` on every load, regardless of whether any agent
   is configured. The settings modal has ~25 agent-related controls
   (`agent_provider`, `agent_yolo_mode`, `agent_thinking_level`, etc.) that are
   meaningless when a different agent adapter is selected.

4. **No adapter abstraction.** The agent engine is `agent_service.py` imported
   directly by routes and frontend. Adding Hermes Agent or OpenClaw requires editing
   Converse internals.

---

## Desired Architecture

```
                    Korina Converse
  (microphone | STT | chat transcript | TTS | browser UI)
  owns channel/session lifecycle, audio I/O
                       |
                       |  AgentContract
                       |  UserTurn / AgentEvent / PermissionAnswer
                       v
              +-------------------+
              |   AgentGateway     |  (injects adapter)
              |   AgentRegistry   |
              +-------------------+
                       |
          +-------------+----------------+
          v             v                v
  KorinaAgentAdapter  HermesAdapter  OpenClawAdapter
  (current impl)      (future HTTP)  (future HTTP/local)
```

- **Converse** owns mic, STT, TTS, transcript, UI, channel/session lifecycle.
- **Agents** are pluggable implementations behind a stable `AgentAdapter` protocol.
- **Korina Agent** becomes one adapter, not the implicit built-in.
- The contract is transport-agnostic: local (in-process) or HTTP (external agent).

---

## Acceptance Criteria

- [ ] Converse runs and chats with agent disabled (no `/api/agent/*` calls made).
- [ ] Converse can select a configured adapter at runtime.
- [ ] Korina Agent behaviour is preserved through `KorinaAgentAdapter`.
- [ ] No Converse route imports Korina Agent implementation directly.
- [ ] Frontend hides adapter-specific config unless that adapter is selected.
- [ ] Existing `/api/agent/*` endpoints remain compatible during migration.
- [ ] Hermes Agent (or a mock HTTP adapter) can be added without Converse changes.

---

## Non-Goals

- Do not rewrite all agent logic in this issue.
- Do not break the live roggoz service.
- Do not move or rename runtime data directories.
- Do not create separate repos yet.

---

## Risks

| Risk | Mitigation |
|------|------------|
| Shared `state.agent` singleton couples chat + agent routes | Extract `AgentGateway`; agent state lives inside adapter |
| Flat `agent_*` config keys need migration shim | Retain aliases for >=1 release; new namespace `agents: { current, adapters }` |
| Frontend regression — agent panel loads unconditionally | Conditional load behind `agent.enabled`; adapter-specific panels lazy |
| Breaking current `/api/agent/*` callers | Keep endpoints as aliases into adapter layer |

---

## Related Files

- `korina/routes/agent.py` — current agent route handlers
- `korina/services/agent_service.py` — current agent logic (to become KorinaAgentAdapter)
- `korina/runtime/state.py` — `AgentState` singleton (to be migrated into adapter)
- `Korina/js/agent-ui.js` — frontend agent wiring
- `Korina/js/settings-ui.js` — agent settings controls
- `korina/config.py` — flat `agent_*` snake_case config keys
- `docs/plans/converse-agent-channel-adapter-plan.md` — full implementation plan
