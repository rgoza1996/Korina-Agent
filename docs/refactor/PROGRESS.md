# Refactor Progress

Mirror of the checklist at the bottom of `blueprint.md`. Tick as each step is completed and keep a short evidence note so future work can resume without reconstructing git history.

**Branch:** `beta`
**Current `beta` HEAD:** `e21fc2f` — `docs: blueprint branch policy is beta, not alpha` (docs-only; no code change)
**Last code-touching commit:** `d3d6932` — `docs: mark Phase 2 complete in PROGRESS.md (2.4.2)`
**Last full verification:** 2026-06-23 on roggoz. `tests/regression_smoke.py` passed **38/38** with real `/api/chat` enabled; `/api/chat` returned HTTP 200 with a live LLM reply in 0.18s. OpenAPI exposes 20 paths (Phase 1 baseline 19 + `/api/capabilities`); all prior paths present, none removed. Provider activation works: `started: ['llama-server.service']`, `stopped: ['lmstudio', 'ollama']`.

## Phase 0 — Stabilization

- [✓] 0.1 barge-in thresholds — completed before Phase 1; validated on roggoz.
- [✓] 0.2 injection migration — steer/delivery wording migrated to injection terminology where required.
- [✓] 0.3 config role split — runtime config separated from tracked example config.
- [✓] 0.4 agent_api_key docs — documented API key/env contract.
- [✓] 0.5 secrets contract — runtime secrets excluded from tracked config; README updated.
- [✓] 0.6 display label for agent — model labels normalized for display.
- [✓] 0.7 verify on roggoz — Phase 0 verified and committed at `b065ea0`.

## Phase 1 — Backend modularization

- [✓] 1.1 directory layout — package skeleton under `korina/` added; committed at `e6a8f64`.
- [✓] 1.2 paths/config — path/bootstrap/config helpers moved into `korina/util/paths.py` and `korina/config.py`; committed at `5034438`.
- [✓] 1.3 runtime state — module globals gathered into `korina/runtime/state.py`; committed at `aa653c4`.
- [✓] 1.4 services — service areas extracted into `korina/services/`; committed at `b54dcd5`.
- [✓] 1.5 config helpers — helper functions redistributed into focused modules; regression suite added; committed at `e20788e`.
- [✓] 1.6 routes — FastAPI route ownership split into `korina/routes/`; committed at `37b0cd5`.
- [✓] 1.7 schemas — routes import schemas from `korina.schemas`; validation returns 422 correctly; committed at `2127df6`.
- [✓] 1.8 backward-compat shim — `korina.app.main()` introduced as canonical uvicorn launcher; committed at `392c9de`.
- [✓] 1.9 delete monolith body — `Korina/korina_voice_lab.py` reduced to 3-line shim; app factory owns app construction; committed at `4dc1f2d`.
- [✓] 1.10 verify — full live regression and Phase 0 route-contract comparison completed; committed at `e21c518`.

**Phase 1 result:** backend modularization complete on `beta`. Current app construction lives in `korina/app_factory.py`; `Korina/korina_voice_lab.py` is only the compatibility shim. The live API exposed the 19 path contract at the Phase 0 baseline (`b065ea0`) at the time of Phase 1 verification; Phase 2 subsequently added `/api/capabilities` for a current total of 20 paths.

## Phase 2 — Single source of truth

- [✓] 2.1 capabilities endpoint — `GET /api/capabilities` serves the `PROVIDER_CAPABILITIES` registry split into `providers` (4 response-LLM) and `agent_providers` (2 agent); Pydantic schema in `korina/schemas_capabilities.py`; route in `korina/routes/capabilities.py`; registry in `korina/util/presets.py`. Commits: `af94507` (registry), `7f26835` (schemas), `a08f4e2` (route + factory wiring), `e295968` (regression test).
- [✓] 2.2 frontend reads capabilities — `loadCapabilities()` fetches once at boot and caches; `getResponseLlmProviderCaps()` / `getAgentProviderCaps()` look up providers from the cache; `setBaseUrlEditability()` is async and now controls `llmBaseUrl`, `sttLlmBaseUrl`, AND `agentBaseUrl` (the pre-Phase-2 bug — agent base URL was never wired to any editability logic); `maybeApplyProviderPreset()` uses the right section (agentProvider → `agent_providers`, everything else → `providers`); legacy `PROVIDER_BASE_URL_PRESETS` constant deleted; `initApp` not modified (boot warm-up via top-level `loadCapabilities().then(()=>setBaseUrlEditability())`). Commits: `b90cbf2` (2.2.1 helpers + fallback), `06bac04` (2.2.2 migrate consumers + remove fallback).
- [✓] 2.3 switch contract documented — `docs/capabilities.md` is the canonical reference. Covers endpoints, the registry table, 7 invariants, the new-provider checklist, the response-LLM and agent user flows, the bug class this contract prevents, the verification recipe, and what's out of scope. Plus 4 new regression tests (3 frontend + 1 edge-case). Commits: `e85c739` (2.2.3 frontend tests + 3 latent regression-script bugs fixed), `b0023dd` (2.3.1 docs), `2b77d95` (2.3.2 edge-case test).
- [✓] 2.4 verify — full live regression passed 38/38 on roggoz; `/api/capabilities` returns 200; `/api/chat` still works after provider activation; OpenAPI path count is Phase 1 baseline + 1 (`/api/capabilities`) and all prior paths remain.

**Phase 2 result:** single source of truth for provider rules is live. The agent provider's base-URL field is now properly controlled by `/api/capabilities` (the bug the blueprint flagged). Response-LLM provider behavior is identical to Phase 1 for current users — the legacy hardcoded `llmProvider() === 'openai-compatible'` check is replaced by `editable_base_url` from the cache, which yields the same value for all current response-LLM providers. Three latent regression-script bugs (a `SystemExit(0)` from the factory stub that silently skipped every test after the factory block; a missing return at the end of `run()` that produced a false-green exit 0; an undefined `BASE` module constant) were fixed as part of 2.2.3 wiring the new tests to actually run. The regression suite is now actually exercising the full route catalog for the first time since 2.1.4.

**Post-Phase-2 doc fixes (no code change):** `e828647` updated `README.md` for the modular package layout, single-source-of-truth registry, and agent request-shape clarification; `e21fc2f` corrected `docs/refactor/blueprint.md` to state that the refactor series targets `beta` (the blueprint was written before Phase 0 and still said `alpha`).

**Pre-Phase-3 housekeeping:** `13c51e6` further updated `PROGRESS.md` to track current HEAD and disambiguate doc-only vs code-touching commits; `1f21a67` corrected the blueprint `Status:` line to reflect that Phases 0/1/2 are complete on `beta`. The runtime `/home/roggoz/Korina/tests/regression_smoke.py` was also synced from source — the runtime copy had been frozen at a pre-Phase-1.10 snapshot and was missing the Phase 2.2.3 frontend tests and the `SystemExit(0)` bug fix. **Note:** `regression_smoke.py` must be run from a git checkout (source `Korina-Agent/`, not the runtime `Korina/` deploy dir) because the blueprint check uses `git ls-files` resolved relative to the script's `parent.parent`. Running it from the runtime fails that one check.

## Phase 3 — Frontend modularization

- [ ] 3.1 module strategy
- [ ] 3.2 extract modules
- [ ] 3.3 extract styles
- [ ] 3.4 model fetch on open
- [ ] 3.5 verify

## Phase 4 — Capability registry

- [ ] 4.1 capability metadata
- [ ] 4.2 /api/models includes capabilities
- [ ] 4.3 frontend filters
- [ ] 4.4 provider/model compatibility
- [ ] 4.5 verify

## Phase 5 — Process supervision unification

- [ ] 5.1 supervisor decision
- [ ] 5.2 unit files
- [ ] 5.3 verify

## Phase 6 — Testing + CI

- [ ] 6.1 scaffold
- [ ] 6.2 backend tests
- [ ] 6.3 frontend smoke
- [ ] 6.4 CI
