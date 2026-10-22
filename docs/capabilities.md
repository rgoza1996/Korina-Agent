# Provider and model contracts

The backend registry in `korina/util/presets.py` describes provider defaults, editable fields, local process management, and model sources. The browser reads that registry through `GET /api/capabilities`; it caches a successful result for the page session and can refresh explicitly.

## Provider registry

The response `providers` map contains `llama.cpp`, `lmstudio`, `ollama`, and `openai-compatible`. The `agent_providers` map contains `openai-compatible` and `anthropic`. Each entry declares `label`, `default_base_url`, `editable_base_url`, `manageable`, `model_sources`, `is_local`, `agent_only`, and `response_llm_only`.

| Response provider | Default API base | Editable URL | Managed locally | Model sources |
| --- | --- | --- | --- | --- |
| `llama.cpp` | `http://127.0.0.1:8080/v1` | No | Yes | Endpoint and local GGUFs |
| `lmstudio` | `http://127.0.0.1:1234/v1` | No | Yes | Endpoint and catalog |
| `ollama` | `http://127.0.0.1:11434/v1` | No | Yes | Endpoint |
| `openai-compatible` | User supplied | Yes | No server started | Endpoint |

Both agent endpoint types use editable URLs and externally supplied servers. Selecting an agent endpoint does not activate a local response-provider process.

## Saving and activation

`POST /api/config` validates and atomically saves settings. It does not activate providers. Independent STT and agent endpoints stay independent; settings saves cannot replace the server-owned audio-probe cache.

During provider activation or refresh, changed effective response/STT/agent settings return HTTP 409 before any save or reset. This closes the gap between checking role ownership and restarting a service. Unrelated settings and identical full-form role values remain saveable. The browser keeps a failed explicit agent transition queued ahead of later saves until retried; its error leaves agent delivery paused.

`POST /api/llm/provider/activate` accepts `{provider, model}`. Unsupported response providers are rejected before saving. It saves the response selection before activation; failures explicitly report that the selection remains saved without automatic rollback. A conflict with another active role returns HTTP 409. Other startup failures return HTTP 500.

Activation returns performed `started`/`stopped` actions, `retained` services with role/reason metadata, and any unconfirmed `stop_requested` actions or warnings. It preserves managed servers used by the selected generic response endpoint, active audio-model STT, or enabled agent. Retention follows actual loopback endpoints and listening process ownership, including generic provider labels. It does not start extra role models. Preflight validates models/executables before teardown and blocks protected single-model/port conflicts. Local process management remains Linux-specific. Generic activation starts no server; unused managed services may be stopped.

The settings modal closes immediately, then serializes its save/apply work. A failed save prevents activation. Changing only STT settings does not activate or repurpose the response provider. Reopening settings and making new edits cannot let an older activation response overwrite the new form.

Llama refresh shares activation admission and preflight, then merges only refresh-owned fields against current settings. It preserves unrelated settings/probe writes and rejects a provider/model selection that changed while discovery was running. Refresh refuses to restart a running llama service used by active STT or Agent, including a shared same-model or custom-port process. Failed prerequisites leave settings/cache/unit unchanged; a failure after the atomic merge explicitly reports what was saved. Refresh starts no unrelated provider.

## Model discovery

`GET /api/models` lists endpoint models and discovered local/catalog choices, with role-specific status and estimated capability metadata. Query parameters can select response/STT candidate URLs, provider types, and API-key environment-variable names before saving. Omitted auth can inherit saved credentials only for the matching saved API base; an explicit blank disables that inheritance.

`GET /api/agent/models` discovers models for saved agent settings. `POST /api/agent/models` accepts bounded candidate fields in JSON: `agent_provider`, `agent_base_url`, `agent_api_key`, `llm_base_url`, and `llm_api_key_env`. Candidate discovery resolves one in-memory configuration snapshot without saving settings or activating processes. Omitted fields retain saved values, except a saved direct agent key is not forwarded to a changed actual API URL unless the request explicitly supplies it. Normal same-response-base credential fallback still applies. Keys stay out of query strings and diagnostic responses; validation errors omit raw input. Both methods use the same model-list envelope.

The browser accepts picker/cache writes only from the latest request for the current settings query. Failed or partial discovery stays retryable. Late results cannot restore an old model selection or replace current options. Local refresh probes use the accepted STT/local model pool and its explicit credential scope.

`GET /api/models/roots` returns path/existence/directory diagnostics. `POST` replaces the configured list by default; `{roots:[...], append:true}` atomically appends, as used by Add Directory. `DELETE ?path=...` atomically removes against the latest list and always returns the same root metadata shape. New lists allow at most 128 directories, with paths of at most 4096 characters; invalid or unavailable new paths are rejected before saving. Historical omitted/default-empty lists still use environment roots. An explicit new empty list or deleting the final root records a server-owned override, keeping the configured list empty across reloads. Clients cannot unset that marker directly. The independent LM Studio hub catalog remains discoverable. Older invalid roots remain visible with diagnostics and are skipped during catalog scans without rewriting the saved file.

## Credentials and API paths

Response credentials come from the environment variable named by `llm_api_key_env`. STT can name its own `stt_llm_api_key_env`. Blank STT or agent credentials inherit the response key only when the actual normalized API base matches. Independent endpoints need explicit role credentials; the local `agent_api_key` setting remains a development convenience stored in runtime configuration.

OpenAI-compatible chat uses `/chat/completions`; Anthropic-compatible agents use `/messages`. Agent chat, model discovery, health, and credential inheritance derive from one provider-aware API base. A full endpoint with the correct provider suffix is supported. A suffix belonging to another provider does not authorize borrowing response credentials or probing an unrelated base.

Shared provider transport caps successful JSON at 2 MiB and error details at 16 KiB. Connected I/O and redirects share deadlines; cross-origin redirects discard credentials/cookies. Providers must honor identity response encoding. OS DNS resolver shutdown remains platform-dependent.

## Estimated audio capability

Model metadata, names, and local projection sidecars estimate audio support; they do not prove that a model can transcribe speech. The multimodal picker filters estimated text-only models unless the session’s All models override is enabled. Unusable choices remain visibly marked in red.

`POST /api/llm/probe-models` performs an explicit bounded audio request. Confirmed unsupported results enter the bounded persistent probe cache; transient failures are reported without becoming permanent unsupported claims. Local refresh triggers these probes deliberately; ordinary picker loading does not infer model quality from a model name or run a background probe loop.

`GET /api/audio-probe` reads that cache. `DELETE /api/audio-probe` clears a selected provider/base/model entry. Probe-cache presence is separate from response-provider activation and from successful end-to-end speech rehearsal.

## Health and verification

`/api/health` reports server-resolved URLs and model load blocks. Matching actual models URL/model/credentials reuse one probe within a request, while each role retains its configured display URL. `/api/ready` combines frontend, audio limits, selected STT, response model, TTS, and enabled agent checks. A reachable lazy Kokoro service is available before warm-up; a custom service without a configured health contract remains unverified.

Changes to the registry or provider behavior need API contract, URL/auth, discovery ownership, process ownership, and browser checks. Run the commands in [testing](testing.md), then rehearse actual services using the [hackathon guide](hackathon.md). [Observed verification](verification.md) distinguishes local software/model evidence from physical hardware and hosted CI.
