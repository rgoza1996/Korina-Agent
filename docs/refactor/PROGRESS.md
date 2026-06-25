# Refactor Progress

Mirror of the checklist at the bottom of `blueprint.md`. Tick as each step is completed and keep a short evidence note so future work can resume without reconstructing git history.

**Branch:** `beta`
**Current `beta` HEAD:** `04e5cde` — `docs: expand Phase 6 pytest and CI plan` (docs-only; no code change)
**Last code-touching commit:** `cb60b4d` — `fix(start.sh): wait for ports to bind after systemctl start`
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

Phase 3 result: frontend modularization complete on `beta`. The inline `<script>` block in `Korina/index.html` was extracted into 16 native ES modules under `Korina/js/` plus an external stylesheet `Korina/styles.css`, with no build step (Option A per blueprint §3.1). The page is now loaded as `<script type="module" src="./js/app.js">` and `app.js` imports every module and calls `initApp()`. Bare globals from the original inline script were migrated to a consolidated `state` object exported from `js/state.js`; HTML inline event handlers (`onclick=`) continue to work via `Object.assign(window, ...)` re-exports in `app.js`. Phase 3 changed only frontend files (18 files in `Korina/`); backend, tests, and other docs are untouched.

- [✓] 3.1 module skeleton — `Korina/js/state.js` (consolidated state + EventBus) and `Korina/js/app.js` entrypoint stub created; `Korina/index.html` inline `<script>` block (lines 175–1303) replaced with `<script type="module" src="./js/app.js"></script>`; page temporarily broken between 3.1.3 and 3.2.15 per the plans acknowledged trade-off. Commits: `42cf7d8` (3.1.1), `bdda3cc` (3.1.2), `8df916e` (3.1.3), `7395160` (3.1.4).
- [✓] 3.2 extract modules — 15 modules extracted under `Korina/js/`, one commit per module. Plan line numbers in `phase-3-plan.md` referenced the pre-3.1.3 inline script at `bdda3cc` (NOT `HEAD~3`, which is post-3.1.3 and missing the inline script — implementer corrected this). Commits: `9876134` (3.2.1 dom), `e9e7a73` (3.2.2 labels), `e2242ae` (3.2.3 api), `5246d12` (3.2.4 settings-ui), `f73d7c6` (3.2.5 history), `d41e17d` (3.2.6 providers-ui), `59b38d3` (3.2.7 acks), `164425f` (3.2.8 speech), `92f3886` (3.2.9 vad), `ee190e7` (3.2.10 recorder), `65dd0cc` (3.2.11 partial-queue), `3396a31` (3.2.12 barge-in), `e0cd6b9` (3.2.13 live), `6cf7e3d` (3.2.14 agent-ui), `61c1289` (3.2.15 app.js wire-up).
- [✓] 3.3 extract styles — inline `<style>` block extracted to `Korina/styles.css`; `Korina/index.html` now has `<link rel="stylesheet" href="./styles.css">` instead. Commit: `420cbe2`.
- [✓] 3.4 model fetch on open — 4-second TTL hack in `loadModelOptions` removed; cache invalidation now relies only on query identity + the `force` flag. Commit: `47c51de`.
- [✓] 3.5 verify — full live regression passed (`tests/regression_smoke.py` against `http://127.0.0.1:8001`, `--no-chat --no-transcribe`); `/api/health` returns 200; `/api/health` exposes Phase 2 capabilities unchanged (5 whisper models, 200 response, etc.); 18 frontend files changed, 0 backend files changed (`git diff --stat 65df94d..HEAD -- korina/ tests/` is empty); total 22 Phase 3 commits on `beta`.


**Post-Phase-3 deployment fixes (pre-Phase-4 baseline):** Phase 3's modularized frontend was committed to `beta` source but never propagated to the runtime at `/home/roggoz/Korina/`. The runtime kept serving a pre-Phase-3 monolithic `index.html` (with Phase 2 inline patches). Two pre-existing latent issues surfaced once the modularized frontend was actually deployed:

- `8a976f7` — added `/js` StaticFiles mount and `/styles.css` route to `korina/app_factory.py`. Without these, the modularized `<script type="module" src="./js/app.js">` and `<link rel="stylesheet" href="./styles.css">` 404'd in the browser.
- `c64dac6` — updated Phase 2 frontend tests to read from `/js/providers-ui.js` and `/js/api.js` (where the helpers live after Phase 3 modularization) instead of trying to parse an inline `<script>` block. Also fixed `global failures` NameError bug that would crash the regression script on its first test failure.

The Phase 4 cron recipe should not need to handle runtime↔source sync as a precondition — runtime and source are now in sync at HEAD `c64dac6`.

## Phase 4 — Capability registry

Phase 4 result: capability registry for model selection is live on `beta`. The user can no longer pick a text-only model for multimodal STT, and a clearly-incompatible `(provider, model)` pair fails fast at `/api/llm/provider/activate` with HTTP 400 instead of failing deep inside the chat request. If a model passes the static checks but the inference engine still rejects audio at runtime, the first real STT request acts as a probe and the system falls back to whisper gracefully, caching the result so subsequent requests don’t re-probe.

- [✓] 4.1 backend `MODEL_CAPABILITIES` registry + `get_model_capability()` + allowlist config — registry in `korina/util/presets.py` (`_lookup_registry_capability` substring match); detector in `korina/services/model_capability.py` (registry → mmproj-on-disk → hard allowlist → name heuristic → blacklist for embeddings/TTS); `multimodal_stt_model_allowlist` config field + getter. Commits: `a0aecd7` (4.1.1), `fe3b899` (4.1.2), `87d9f98` (4.1.3), `56340a3` (4.1.4 test).
- [✓] 4.2 `/api/models` exposes `llm_models_capabilities` + `stt_llm_models_capabilities` (additive) — `ModelCapabilities` Pydantic schema in `korina/schemas_capabilities.py`; new fields populated in `korina/routes/models.py`; existing fields preserved verbatim. Commits: `9b1a51e` (4.2.1), `fc458f1` (4.2.2), `ec330f0` (4.2.3 test).
- [✓] 4.3 frontend `capability-filter.js` + `?All models` toggle (multimodal STT dropdown only) — `Korina/js/capability-filter.js` exports `filterModelsByCapability`/`sttLlmModelRequirement`/`setCapabilityFilterOverride`; `providers-ui.js` filters `sttLlmModel` dropdown; `index.html` adds checkbox; `app.js` wires the toggle. Response-LLM and agent dropdowns unchanged. Commits: `c055f7c` (4.3.1), `17d3258` (4.3.2), `1dea1c8` (4.3.3), `7dbcf8d` (4.3.4 test).
- [✓] 4.4 `provider_supports_model()` + activate endpoint raises 400 on incompatible combos — `korina/services/provider_manager.py` adds the pre-flight check; `korina/routes/providers.py:activate_provider` raises `HTTPException(400, detail={"error":"incompatible_provider_model", ...})` before delegating to `activate_llm_provider`. Commits: `5021cd8` (4.4.1), `353068c` (4.4.2), `2ba7035` (4.4.3 test).
- [✓] 4.5 runtime audio probe + graceful fallback to whisper + persistent cache — `korina/config.py` adds `audio_unsupported` slot + 4 helpers; `korina/services/audio_probe.py` defines `classify_failure` (regex on body for 400/422, status 415 short-circuit) + `triple_key` + `maybe_fallback_to_whisper` orchestrator; `korina/routes/stt.py` wraps the multimodal STT path; `korina/routes/providers.py` clears the probe cache on activate and exposes `GET /api/audio-probe` (list) + `DELETE /api/audio-probe` (clear one entry, query-param form to avoid the two-`:path`-converter URL-greed bug); frontend gets dropdown badges + clear-and-retry button. Commits: `b7e3a49` (4.5.1), `9ddfecf` (4.5.2), `415d77f` (4.5.3), `a6fcbaa` (4.5.4 + 4.5.5), `15f00c8` (4.5.6), `86e740a` (4.5.6 fixup — `state.llmProvider` is undefined on state; replaced with `effectiveSttLlmProvider()` so the badge triple-key matches the backend’s `triple_key`), `2219469` (4.5.7 test).
- [✓] 4.6 verify (service restart + regression + manual smoke + PROGRESS.md close-out) — service restarted on roggoz after `rsync -a --delete korina/` from source checkout to runtime (`/home/roggoz/Korina/korina/`); new PID 104060; `/api/health` returns 200; full live regression passes; 5 manual smoke checks pass: `*_models_capabilities` populated (29 entries; gemma-4-E2B shows `supports_audio_input: true, source: local_gguf, has_mmproj: true, approx_vram_gb: 4.0`), `/api/llm/provider/activate` returns HTTP 400 with `incompatible_provider_model` for `llama.cpp + qwen/qwen3-14b`, `sttCapabilityFilterOverride` and `clearProbeBtn` toggles present in served HTML, `capability-filter.js` serves cleanly, `/api/audio-probe` returns `{"entries":{}}` on fresh install. Commit: this close-out.

**Total: 20 Phase 4 commits on `beta` (a0aecd7 .. 86e740a), plus this close-out.**

**Pre-Phase-4 housekeeping resolved during 4.5**: `korina.config` uses a `CONFIG_KEYS` allowlist filter inside `save_config()` — any new key must also be added to `DEFAULT_CONFIG` or it would be silently stripped on the next save. The 4.5.1 implementer caught this and added `audio_unsupported: {}` to `DEFAULT_CONFIG` alongside the getter/setter functions; otherwise the probe cache would have been wiped on every `save_config()` call.

**Deployment note (from 4.6.1)**: the Phase 4 prompt’s frontend-sync recipe (`cp -p Korina/js/*.js /home/roggoz/Korina/js/`) handled only the frontend, not the backend. The Python service reads `korina/` from `/home/roggoz/Korina/korina/` (not from `/home/roggoz/Korina-Agent/korina/`). After committing all Phase 4 changes to `beta` source, the runtime `korina/` tree was stale and the service returned the pre-Phase-4 shape on every endpoint. Resolved by `rsync -a --delete /home/roggoz/Korina-Agent/korina/ /home/roggoz/Korina/korina/` + clearing `__pycache__/` + restarting. Future phases should bake backend sync into the per-task recipe, not leave it as a 4.6 surprise.

## Phase 5 — Process supervision unification

Phase 5 result: Korina Voice Lab and Kokoro streaming TTS are both managed by `systemd --user`. `Korina/start.sh` and `Korina/stop.sh` are compatibility wrappers around `systemctl --user`. Runtime services were installed from tracked units under `deploy/systemd/` and verified on roggoz.

- [✓] 5.1 supervisor decision — standardized on `systemd --user`; llama.cpp remains managed by provider activation; LM Studio/Ollama behavior unchanged.
- [✓] 5.2 unit files — added `deploy/systemd/korina-voice-lab.service`, `deploy/systemd/kokoro-streaming-server.service`, and `Korina/install-services.sh`; updated lifecycle scripts and README.
- [✓] 5.3 verify — installed units on roggoz, stopped old/manual processes, restarted units, verified `:8001` and `:8880`, `/api/health`, Kokoro `/health`, frontend static assets, lifecycle wrappers, and regression smoke.

## Phase 6 — Testing + CI

- [ ] 6.1 scaffold
- [ ] 6.2 backend tests
- [ ] 6.3 frontend smoke
- [ ] 6.4 CI
