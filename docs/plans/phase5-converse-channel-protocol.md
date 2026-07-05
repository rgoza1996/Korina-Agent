# Phase 5 plan: `ConverseChannel` protocol + adapter-registry surface

**Prereq:** Phase 4 Complete (verified live); Phase 5 plan documented in `docs/plans/phase0-baseline.md`.

## Goal

Make Korina Converse a real chat channel — protocol-level compatibility with
Hermes Agent, OpenClaw, and any other adapter-implementing agent — **without**
doing the package split (that is Phase 6).

After Phase 5:

- `ConverseChannel` is a documented protocol (`async def send()`, `async def stream()`,
  `async def cancel()`).
- `register_converse_channel()` is the consumer-facing surface; default channel
  is `ConverseChannel` backed by `agent_gateway()`.
- A stub Hermes adapter (`korina_test_adapter_hermes`) demonstrates the seam.
- Frontend has a dropdown in Settings → "Converse channel" that swaps the
  implementation at runtime via the registry.

## Why not "conditional UI loading" (the previous Phase 5 frame)

Conditional UI loading is a feature that depends on the Converse/Agent boundary
already being a real channel. It is not the boundary itself. Phasing it as
Phase 5 puts the cart before the horse and leaves Converse as a private backend
package that only knows about one adapter.

## Phase 5 commits

### Commit 1 — `ConverseChannel` protocol + registry

**Files:**
- `korina/converse/__init__.py` — public surface
- `korina/converse/protocol.py` — `ConverseChannel` ABC + types
- `korina/converse/registry.py` — `register_converse_channel`, `get_converse_channel`
- `korina/converse/korina_channel.py` — default channel backed by `agent_gateway()`
- `korina/converse/__init__.py` — exports

**Behaviour:**
- Default channel is `KorinaConverseChannel`, registered at startup
- `get_converse_channel()` returns the active channel (default if none selected)
- Channel selection persists in `config.json` under a new top-level key
  `converse.channel`

**Verified by:**
- Unit tests for registry (`register`, `get`, `unregister`)
- Round-trip test: a stub channel + a real Korina channel both speak
  `ConverseChannel` and are interchangeable from Converse route perspective

### Commit 2 — adapter selection route + Settings UI wiring

**Files:**
- `korina/routes/converse.py` — new route file: `GET /api/converse/channel`,
  `POST /api/converse/channel/{name}`
- `korina/app_factory.py` — register the new routes
- `Korina/js/settings-ui.js` — adapter dropdown
- `Korina/styles.css` — minimal styling
- `Korina/js/api.js` — `setConverseChannel(name)` helper

**Behaviour:**
- `GET /api/converse/channel` returns `{channel: "<name>"}`
- `POST /api/converse/channel/<name>` switches the active channel
- Frontend Settings has a select element; changes call the POST and
  refresh the chat panel

**Verified by:**
- Route tests
- Adapter-swap smoke test: POST `/api/converse/channel/hermes-stub` returns OK;
  subsequent `/api/agent/transcript` POST goes through the stub channel

### Commit 3 — stub Hermes adapter

**Files:**
- `korina/converse/hermes_stub.py` — `HermesStubChannel` for dev/CI
- `tests/converse/test_hermes_stub_channel.py` — round-trip + adapter-swap tests
- `docs/converse-adapters.md` — adapter-author guide

**Behaviour:**
- `HermesStubChannel` echoes transcripts back as `[hermes-stub] <text>`
- Implements full `ConverseChannel` surface
- Ships as a dev dep so adapter authors can copy from it

**Verified by:**
- Round-trip test: send a transcript via `HermesStubChannel.send()`,
  receive `[hermes-stub] <text>` back
- Cross-adapter swap test: register Hermes stub, send a transcript,
  confirm /api/agent/transcript returned the stub's response shape

## Out of scope (Phase 6+)

- `korina-converse` package split (`pyproject.toml` extras)
- Frontend modal/UX polish beyond the dropdown
- Real Hermes Agent wiring (that is on Hermes's roadmap; we ship a stub here
  so Hermes can land their implementation against a known seam)
- OpenClaw adapter (mirrors Hermes work; Phase 6 stretch)

## Migration safety

Phase 5 commits are *additive*. The default channel is `KorinaConverseChannel`,
which routes through the existing `agent_gateway()`. Existing Converse callers
see no behavioural change. Adapter switching only happens if the user
explicitly changes the Settings dropdown.

## CI strategy during Phase 5

The CI pipeline is currently red on `e093755` due to a separate, pre-existing
`FakeWav(Path)` 3.11-compat bug. Plan:

1. Fix `FakeWav` as a one-line commit alongside Commit 1 of Phase 5 (not
   alone — the fix doesn't depend on Phase 5 work, but shipping both together
   keeps history coherent).
2. Phase 5 commits ship to `alpha` with the fix in place.
3. Once `alpha` is green, Phase 5 work is reviewed.

This is not ideal sequencing, but the CI fix is the highest-stakes item
blocking *any* future code from landing on alpha. It is cheaper to bundle
than to debate ordering further.
