# Refactor Progress

Mirror of the checklist at the bottom of `blueprint.md`. Tick as each step is completed and keep a short evidence note so future work can resume without reconstructing git history.

**Branch:** `beta`  
**Latest verified Phase 1 commit:** `e21c518` — `test: verify Phase 1 refactor contract and restore OpenAPI body schemas (Phase 1.10)`  
**Last full verification:** `2026-06-22 21:00 PDT` on roggoz. `tests/regression_smoke.py` passed **28/28** with real `/api/chat` enabled; `/api/chat` returned HTTP 200 with a live LLM reply.  
**Verification report:** `docs/refactor/phase-1-verification.md`

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

**Phase 1 result:** backend modularization is complete on `beta`. Current app construction lives in `korina/app_factory.py`; `Korina/korina_voice_lab.py` is only the compatibility shim. The live API exposes the same 19 path contract as the Phase 0 baseline (`b065ea0`) with no missing/added paths and no method diffs.

## Phase 2 — Single source of truth

- [ ] 2.1 capabilities endpoint
- [ ] 2.2 frontend reads capabilities
- [ ] 2.3 switch contract documented
- [ ] 2.4 verify

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
