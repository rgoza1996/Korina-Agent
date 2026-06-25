# Provider & Model Capability Contract

Single source of truth for which provider the user has selected, what
base URL is in play, which models the dropdown should show, and which
fields the UI may edit. This contract is the canonical answer to
"where does provider X's base URL come from?" — if you find yourself
asking that, read this first.

## Endpoints

- `GET /api/capabilities` — the registry. See `korina/schemas_capabilities.py`
  for the wire format. Two sections:
  - `providers` — valid for the response-LLM path. Today: `llama.cpp`,
    `lmstudio`, `ollama`, `openai-compatible`.
  - `agent_providers` — valid for the agent path. Today:
    `openai-compatible`, `anthropic`.

  Each entry has 8 fields: `label`, `default_base_url`, `editable_base_url`,
  `manageable`, `model_sources`, `is_local`, `agent_only`, `response_llm_only`.

- `POST /api/llm/provider/activate` — switches the local inference
  server when the response-LLM provider is `manageable: true`. The
  request body is `{provider, model}`; the response includes
  `activation.started` and `activation.stopped` lists.

## Registry table (today)

Response-LLM providers (`providers`):

| id                  | default_base_url            | editable | manageable | is_local | model_sources                  |
|---------------------|-----------------------------|----------|------------|----------|--------------------------------|
| `llama.cpp`         | `http://127.0.0.1:8080/v1`  | false    | true       | true     | `endpoint_loaded`, `local_gguf`|
| `lmstudio`          | `http://127.0.0.1:1234/v1`  | false    | true       | true     | `endpoint_loaded`, `catalog`   |
| `ollama`            | `http://127.0.0.1:11434/v1` | false    | true       | true     | `endpoint_loaded`              |
| `openai-compatible` | (empty)                     | true     | false      | false    | `endpoint_loaded`              |

Agent providers (`agent_providers`):

| id                  | default_base_url | editable | agent_only | is_local |
|---------------------|------------------|----------|------------|----------|
| `openai-compatible` | (empty)          | true     | false      | false    |
| `anthropic`         | (empty)          | true     | true       | false    |

The three response-LLM-only providers (`llama.cpp`, `lmstudio`, `ollama`)
are filtered out of the agent section via `response_llm_only: true`.
`anthropic` is filtered out of the response-LLM section via
`agent_only: true`. The split happens in `capabilities_for_section()`
in `korina/util/presets.py`.

## Invariants

1. **The backend owns provider rules.** No JS file may hardcode
   `"llama.cpp" → http://127.0.0.1:8080/v1` or
   `"openai-compatible" → base URL is editable`. Both are derived from
   `/api/capabilities`. The legacy `PROVIDER_BASE_URL_PRESETS`
   constant in `Korina/index.html` was deleted in Phase 2.2.2.

2. **A provider is editable iff `editable_base_url: true`.** The UI
   disables the base-URL input otherwise. Today that means:
   - `llama.cpp`, `lmstudio`, `ollama`: base URL is locked.
   - `openai-compatible`, `anthropic`: base URL is editable.

3. **`manageable` providers get a server lifecycle.** When the user
   picks a `manageable: true` response-LLM provider, the UI must POST
   `/api/llm/provider/activate` *after* saving the new config and
   *before* refetching `/api/models`. The activation response includes
   `{started, stopped}` so the UI can show what changed.

4. **Agent-only providers never appear in `providers`.** Today only
   `anthropic` is agent-only. If a future agent-only provider is added
   (e.g. `gemini-agent`), it goes in `agent_providers` only with
   `agent_only: true`.

5. **`/api/capabilities` is fetch-once per page session unless forced.** The frontend caches the
   response via `loadCapabilities()` in `Korina/js/providers-ui.js`. There is no
   `/api/capabilities/refresh` endpoint; if the backend registry changes, the
   user reloads the page or code calls `loadCapabilities(true)` as part of an
   explicit provider/model refresh.

6. **Capability keys are always snake_case in JSON.** `editable_base_url`
   in the wire format, `editableBaseUrl` only in the JS object (no
   current JS object — we read fields directly from the JSON).

7. **Capabilities warm before app init.** `Korina/js/app.js` calls
   `await loadCapabilities().then(() => setBaseUrlEditability())` before
   `initApp()`. `initApp()` still performs normal config/model loading in
   `Korina/js/api.js`; the discipline is to keep capability warm-up explicit
   so provider/base-URL UI state is correct before the rest of the page wiring runs.

## Adding a new provider

1. Add an entry to `PROVIDER_CAPABILITIES` in `korina/util/presets.py`.
   Pick the right section flag:
   - Response-LLM only: set `response_llm_only: true`.
   - Agent only: set `agent_only: true`.
   - Both: leave both flags `false`.
2. If the provider is local (`is_local: true`), set `manageable: true`
   AND add a corresponding branch to `start_*/stop_*` in
   `korina/services/provider_manager.py`.
3. If the provider needs a non-default auth header, extend
   `auth_headers_from_env` in `korina/runtime/http.py`. (Most don't.)
4. Run `python3 tests/regression_smoke.py`. The capabilities check
   (`test_capabilities_endpoint`) hard-codes the expected provider set;
   update its assertion if you add or remove a provider.
5. Commit on `beta`; do not touch `alpha` or `master` until release.

## Provider switch user flow (response-LLM)

1. User picks a new provider in `#llmProvider`.
2. UI:
   - Calls `maybeApplyProviderPreset('llmProvider', 'llmBaseUrl')`
     to set the base URL to the registry default.
   - Calls `await setBaseUrlEditability()` to lock/unlock the base URL
     (this also re-evaluates `sttLlmBaseUrl` and `agentBaseUrl`
     because the same function owns all three editability decisions).
   - Calls `syncConverseSettingsUI()` to refresh status pills.
   - `await saveConfigNow()` to persist.
   - `await activateSelectedProvider(llmProvider(), lmModel())` to
     start/stop the local server (only matters if `manageable: true`).
   - `await loadModelOptions(true)` to refresh the model dropdown.
   - `await loadAgentModelOptions()` to refresh the agent dropdown.
   - `health()` to refresh the status text.
3. The user-visible text: "Activated llama.cpp. Started llama-server;
   stopped nothing." (or similar; matches today's behavior).

## Provider switch user flow (agent)

1. User picks a new agent provider in `#agentProvider`.
2. UI:
   - `maybeApplyProviderPreset('agentProvider', 'agentBaseUrl')`
     uses `getAgentProviderCaps(provider)` (the `agent_providers`
     section, not `providers`) to look up the default.
   - `await setBaseUrlEditability()` (now also updates `agentBaseUrl`).
   - `saveConfigSoon()`.
3. There is no `activate_agent_provider` endpoint. The agent runs on
   demand in `generate_agent_state_report`; the user only needs to
   set the base URL and model. (Current behavior; Phase 2 preserves it.)

## Bug class this contract prevents

The pre-Phase-2 bug the contract fixes: `setBaseUrlEditability()` in
`Korina/index.html` had a hardcoded `llmProvider() === 'openai-compatible'`
check that controlled only `#llmBaseUrl` and `#sttLlmBaseUrl`. The
`#agentBaseUrl` field (added with the agent section in Phase 0) was
**never wired to any editability logic** — so the user could edit
the agent base URL freely, including for `manageable: true` local
providers, even though those don't make sense to edit. The
`/api/capabilities` contract forces every base-URL editability
decision through one code path, so adding a new provider can't
silently re-introduce this class of bug.

## Verification

- Backend: `GET /api/capabilities` returns 200 with both sections
  populated. Covered by `tests/api/test_config_capabilities_models.py` and
  the live `tests/regression_smoke.py` contract check.
- Frontend wiring: `Korina/js/app.js` imports and runs capability warm-up,
  `Korina/js/providers-ui.js` owns `loadCapabilities()` and base-URL
  editability, and `Korina/js/capability-filter.js` consumes model capability
  metadata for dropdown filtering. Covered by `tests/frontend/test_static_frontend.py`.
- Per-provider fields: `editable_base_url`, `manageable`, `is_local`,
  `agent_only`, and `response_llm_only` are typed and section-filtered by
  the backend registry. API and unit coverage lives under `tests/api/` and
  `tests/unit/test_provider_manager.py`.

## Out of scope for Phase 2

- Fully automatic/probe-backed model capability metadata. Phase 4 added the
  first registry/probe layer, but persistent probe-backed metadata remains a
  follow-up.
- Live reload endpoint for `/api/capabilities`; current refresh is frontend-side
  via page reload or explicit `loadCapabilities(true)`.
- `/api/capabilities` filtering by user (all providers are returned
  to all users; the current auth model is localhost-only, so this is
  a non-issue today).
