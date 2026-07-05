# Verdict: Korina Converse vs Korina Agent boundary

**Date:** 2026-07-05
**Scope:** answer the user's question "Is Korina Converse and Korina Agent well divided in the codebase?"

## TL;DR

**No — they're well-prepared-for-division but not actually divided.** Phases 3 and 4 put in place all the *protocol-level primitives* (adapter interface, gateway, registry, state migration), but the *package boundary* is still one Python package, one process, one config, one runtime. For the stated goal — "Korina Converse as a chat channel compatible with other agents like Hermes Agent, OpenClaw, etc." — the remaining work is structural (a real package split + a `ConverseChannel` protocol), not further internal refactors.

## What Phase 3+4 actually accomplished

| Layer | State | Evidence |
|---|---|---|
| Protocol surface | `AgentAdapter` Protocol in `korina/agents/base.py` | Consumer code can converge or diverge without touching Converse |
| Routing boundary | `agent_gateway()` in `korina/agents/gateway.py` | Routes no longer touch `state.agent` directly |
| Adapter registry | `register_adapter()` + default `KorinaAgentAdapter` | New adapters (Hermes, OpenClaw) plug in without Converse changes |
| State isolation | `AgentState` migrated out of `korina.runtime` into `korina.agents.state` | Two commits: `54af2ab`, `80fc12f` |
| Live verification | All `/api/agent/*` endpoints return clean shapes; GET/POST smoke passed | Service `active` on `:8001` after Commit B |

These are real, valuable, and verified. A Hermes adapter today can implement `AgentAdapter`, register itself, and Converse routes will dispatch to it without changes to Converse code.

## What's NOT done — the four missing pieces

1. **One Python package.** `pyproject.toml` ships only `korina*`. There is no `korina-converse` package. A consumer cannot install Converse without all of `korina` (including the KorinaAgentAdapter, agent routes, STT, ASR, ack phrase generation).
2. **No `ConverseChannel` interface.** Converse is not a first-class concept. There is no `async def send() -> Iterator[AssistantEvent]`. Chat routes are HTTP-shaped, not channel-shaped.
3. **No adapter selection surface.** The frontend has no way to swap adapter implementations. Converse routes hardcode the default `KorinaAgentAdapter` via the gateway.
4. **Shared `korina.runtime`.** ASR (`AsrState`), Ack (`AckState`), and chat state all live in the same module. A Hermes-only consumer would still pull all of it.

## What "well-divided" looks like

```
PyPI
  ├── korina-converse          # minimal: channel protocol + chat routes
  ├── korina-converse[agents]  # adds KorinaAgentAdapter + agent routes
  ├── korina-converse[hermes]  # adds Hermes adapter
  └── korina-converse[openclaw]
```

The adapter registry lives in `korina-converse`; each agent adapter is its own extras. Converse routes dispatch through `ConverseChannel.send()`; agents (Korina, Hermes, OpenClaw) are interchangeable implementations behind that interface.

## Why this matters more than further internal refactors

The Phase 3+4 work is necessary but not sufficient. Without a package split, the "Converse is compatible with Hermes Agent" claim is aspirational. With it, the claim is verifiable (anyone can `pip install korina-converse[hermes]` and try).

## Recommended sequence

1. **Lock in this verdict as a doc** (this file). Reference it in Phase 5+6 plans.
2. **Phase 5 — `ConverseChannel` protocol + adapter registry surface.** No package split yet, but the registry becomes the consumer-facing contract.
3. **Phase 6 — optional `korina-converse` package + stubs.** `pip install korina-converse[hermes]` works against a stub adapter.
4. **Phase 7 — frontend adapter selector + hermetic Hermes/OpenClaw examples.**

## CI / Actions context

The Korina Actions pipeline has been red since `49ac4a8` (2026-07-04), well before any Converse/Agent split work. Two distinct bugs:

- **Hardcoded local paths in frontend tests** — three tests referenced `/home/roggoz/...` instead of repo-relative paths. Fixed in `526409d` via `Path(__file__).resolve().parents[2]` + a `_node_bin()` helper that `pytest.skip()`-cleanly when node isn't available.
- **`FakeWav(Path)` subclassing in `test_silent_empty_fallback.py`** — Python 3.11 `pathlib.Path` rejects subclasses that don't call `super().__new__()`.

Neither is causally related to the Converse/Agent split.

## See also

- `docs/plans/phase5-converse-channel-protocol.md` — the concrete Phase 5 plan
- `docs/plans/phase0-baseline.md` — the original baseline inventory
- Compacted prior summary for full Phase 0–4 commit history
