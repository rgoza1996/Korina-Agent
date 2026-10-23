# Provider and model contracts

The backend registry in `korina/util/presets.py` describes provider defaults, editable fields, local process management, and model sources. The browser reads that registry through `GET /api/capabilities`; it caches a successful result for the page session and can refresh explicitly.

## Provider registry

The response `providers` map contains `llama.cpp`, `lmstudio`, `ollama`, `openai-compatible`, and `meta`. The `agent_providers` map contains `openai-compatible`, `meta`, and `anthropic`. Each entry declares `label`, `default_base_url`, `editable_base_url`, `manageable`, `model_sources`, `is_local`, `agent_only`, and `response_llm_only`. `default_api_key_env` supplies an optional registry-scoped credential default; an empty value supplies none.

| Response provider | Default API base | Editable URL | Managed locally | Model sources |
| --- | --- | --- | --- | --- |
| `llama.cpp` | `http://127.0.0.1:8080/v1` | No | Yes | Endpoint and local GGUFs |
| `lmstudio` | `http://127.0.0.1:1234/v1` | No | Yes | Endpoint and catalog |
| `ollama` | `http://127.0.0.1:11434/v1` | No | Yes | Endpoint |
| `openai-compatible` | User supplied | Yes | No server started | Endpoint |
| `meta` — Meta Model API | `https://api.meta.ai/v1` | No | No server started | Authenticated endpoint |

Generic OpenAI-compatible and Anthropic-compatible agent endpoints use editable URLs. The named Meta Model API agent profile uses the same fixed canonical base as the response profile. All three use externally supplied services; selecting an agent endpoint does not activate a local response-provider process.

The Meta profile uses `/chat/completions` and authenticated `/models` discovery. Select an ID returned for the team's account; Korina does not hardcode a Meta model ID or establish account access from a documentation example. Response and built-in agent requests leave reasoning at the model default: the Meta response path omits the local/generic `reasoning` field, and neither path adds `reasoning_effort`. The focused rehearsal keeps local Whisper STT and Kokoro TTS; a Meta model listing does not prove audio capabilities.

## Response token budget

The **Response token budget** control persists `llm_max_tokens`, an integer from **1 to 8192**, default **180**. An explicitly supplied `ChatRequest.max_tokens` overrides the saved budget for that request; omitting it uses the saved setting. The browser sends its selected response budget explicitly. Agent reports retain their separate `agent_max_tokens` setting, default **512**; changing the response budget does not change the agent budget.

Meta reasoning may consume this output budget before any visible answer, while the profile retains model-default reasoning. If a provider returns no visible text with `finish_reason: "length"`, `/api/chat` returns HTTP 500 with an actionable instruction to increase Response token budget in Settings or choose another response model. Korina does not silently increase the budget or retry. A nonempty truncated answer remains compatible with the existing response contract.

The built-in Meta/OpenAI-compatible agent reports the same empty-budget condition through its existing error status/events and preserves its previous report. Its **State report max tokens** control is independent; deliberate adjustment does not reset memory or silently retry. Intentional empty reports ending with `stop` remain suppressed normally.

## Saving and activation

`POST /api/config` validates and atomically saves settings. It does not activate providers. Independent STT and agent endpoints stay independent; settings saves cannot replace the server-owned audio-probe cache.

During provider activation or refresh, changed effective response/STT/agent settings return HTTP 409 before any save or reset. This closes the gap between checking role ownership and restarting a service. Unrelated settings and identical full-form role values remain saveable. The browser keeps a failed explicit agent transition queued ahead of later saves until retried; its error leaves agent delivery paused.

`POST /api/llm/provider/activate` accepts `{provider, model}`. Unsupported response providers are rejected before saving. It saves the response selection before activation; failures explicitly report that the selection remains saved without automatic rollback. A conflict with another active role returns HTTP 409. Other startup failures return HTTP 500.

Activation returns performed `started`/`stopped` actions, `retained` services with role/reason metadata, and any unconfirmed `stop_requested` actions or warnings. It preserves managed servers used by the selected generic response endpoint, active audio-model STT, or enabled agent. Retention follows actual loopback endpoints and listening process ownership, including generic provider labels. It does not start extra role models. Preflight validates models/executables before teardown and blocks protected single-model/port conflicts. Local process management remains Linux-specific. Generic and Meta activation start no server; unused managed services may be stopped.

The browser initializes a form baseline before wiring edits, sends only observed field changes, and keeps pending edits plus successful-save revisions across a delayed initial settings read. Untouched HTML defaults are never posted as guessed saved settings. A failed initial read can retry from Settings; provider activation waits for a known loaded baseline. Partial configuration application preserves omitted reasoning/model fields and activation snapshot keys.

Successful saves reconcile server-derived STT/agent values into untouched controls. For example, if the server moves following role models with a changed response model, the next transcription uses that confirmed model. Newer edits stay pending, independent endpoints keep their returned values, and a save does not advance the last activated-provider baseline or activate a provider.

The settings modal closes immediately, then serializes its save/apply work. A failed save prevents activation. Changing only STT settings does not activate or repurpose the response provider. Reopening settings and making new edits cannot let an older activation response overwrite the new form.

Llama refresh shares activation admission and preflight, then merges only refresh-owned fields against current settings. It preserves unrelated settings/probe writes and rejects a provider/model selection that changed while discovery was running. Refresh refuses to restart a running llama service used by active STT or Agent, including a shared same-model or custom-port process. Failed prerequisites leave settings/cache/unit unchanged; a failure after the atomic merge explicitly reports what was saved. Refresh starts no unrelated provider.

## Model discovery

`GET /api/models` lists endpoint models and discovered local/catalog choices, with role-specific status and estimated capability metadata. Query parameters can select response/STT candidate URLs, provider types, and API-key environment-variable names before saving. Omitted auth can inherit saved credentials only for the matching saved API base; an explicit blank disables that inheritance.

Supplying a nonblank `llm_api_key_env` or `stt_llm_api_key_env` query override also requires `X-Korina-Intent: model-discovery`. The bundled picker sends this header. It requires a CORS preflight from another browser origin, so an untrusted page cannot use a simple GET to forward a named server credential to its chosen candidate. Configured trusted origins may send it. This is a browser request-intent boundary, not API authentication; nonbrowser clients using explicit credential overrides must send the header too. Omitted or explicitly blank overrides keep their existing behavior.

`GET /api/agent/models` discovers models for saved agent settings. `POST /api/agent/models` accepts bounded candidate fields in JSON: `agent_provider`, `agent_base_url`, `agent_api_key`, `llm_base_url`, and `llm_api_key_env`. Candidate discovery resolves one in-memory configuration snapshot without saving settings or activating processes. Omitted fields retain saved values, except a saved direct agent key is not forwarded to a changed actual API URL unless the request explicitly supplies it. Normal same-response-base credential fallback still applies. Keys stay out of query strings and diagnostic responses; validation errors omit raw input. Both methods use the same model-list envelope.

Discovery normalizes valid UTF-8 IDs, trims/deduplicates them, and rejects IDs longer than 512 Unicode characters. Each role/source returns at most 512 IDs within 256 KiB of JSON-escaped UTF-8 identifiers. Shared metadata covers only the returned union, capped at 1024 IDs / 512 KiB. `catalog_info` reports skipped IDs, truncation, and local errors. Configured IDs found in a source are pinned through truncation. The browser retains an omitted current/saved choice as an annotated option, including known incompatibility warnings; discovery never silently selects or saves a replacement. Blank STT selection retains explicit inheritance.

Local discovery visits at most 10,000 directory entries per source and bounds manifest/LM Studio CLI input to 2 MiB; CLI listing has a 30-second deadline. A request-scoped metadata scan shares another 10,000-entry budget and caches each parent, avoiding repeated sidecar scans. Exhaustion/errors are labeled incomplete estimates. Provider activation validates an explicit selected model independently of display traversal limits, including a legitimate LM Studio ID or existing absolute GGUF path beyond the displayed catalog.

Browser discovery caps declared and streamed bodies at 8 MiB before strict UTF-8 and JSON parsing. It also bounds returned IDs and metadata when connected to an older server. Oversized or malformed responses preserve prior options/cache and remain retryable.

The browser accepts picker/cache writes only from the latest request for the current settings query. Failed or partial discovery stays retryable. Late results cannot restore an old model selection or replace current options. Local refresh probes use the accepted STT/local model pool and its explicit credential scope.

`GET /api/models/roots` returns path/existence/directory diagnostics. `POST` replaces the configured list by default; `{roots:[...], append:true}` atomically appends, as used by Add Directory. `DELETE ?path=...` atomically removes against the latest list and always returns the same root metadata shape. New lists allow at most 128 directories, with paths of at most 4096 characters; invalid or unavailable new paths are rejected before saving. Historical omitted/default-empty lists still use environment roots. An explicit new empty list or deleting the final root records a server-owned override, keeping the configured list empty across reloads. Clients cannot unset that marker directly. The independent LM Studio hub catalog remains discoverable. Older invalid roots remain visible with diagnostics and are skipped during catalog scans without rewriting the saved file.

## Credentials and API paths

Response credentials come from the environment variable named by `llm_api_key_env`. With that setting blank, the named Meta profile defaults to `MODEL_API_KEY` only for its canonical API base. A generic/custom profile does not gain that default merely by using a Meta hostname. Explicit configured environment-variable names take precedence.

STT can name its own `stt_llm_api_key_env`; blank STT credentials inherit the response key only when the actual normalized API base matches. Agent credentials use an explicit `agent_api_key` first, then matching-response-base environment credentials or the selected named profile's own default. An independent Meta agent can therefore use its canonical `MODEL_API_KEY` default; credentials for other independent endpoints must be configured explicitly when needed. An unset default environment variable produces no Authorization header. The local `agent_api_key` setting remains a development convenience stored in runtime configuration.

OpenAI-compatible chat uses `/chat/completions`; Anthropic-compatible agents use `/messages`. Agent chat, model discovery, health, and credential inheritance derive from one provider-aware API base. A full endpoint with the correct provider suffix is supported. A suffix belonging to another provider does not authorize borrowing response credentials or probing an unrelated base.

Shared provider transport caps successful JSON at 2 MiB and error details at 16 KiB. Connected I/O and redirects share deadlines; cross-origin redirects discard credentials/cookies. Providers must honor identity response encoding. OS DNS resolver shutdown remains platform-dependent.

## Estimated audio capability

Model metadata, names, and local projection sidecars estimate audio support; they do not prove that a model can transcribe speech. The multimodal picker filters estimated text-only models unless the session’s All models override is enabled. Unusable choices remain visibly marked in red.

`POST /api/llm/probe-models` performs an explicit bounded audio request. Confirmed unsupported results enter the bounded persistent probe cache; transient failures are reported without becoming permanent unsupported claims. Local refresh triggers these probes deliberately; ordinary picker loading does not infer model quality from a model name or run a background probe loop. Batches admit at most 64 unique nonblank IDs and have a 60-second admission/network budget, with each HTTP request capped at 15 seconds or the remaining budget. Completed results are retained; `skipped_models` and `timed_out` identify remaining IDs without marking them unsupported. The local refresh UI prioritizes the selected STT model within its 64-model batch and visibly reports omitted catalog choices and probes skipped for time.

Probe batches share chat/STT request ownership. Disconnect or cancellation closes connected provider I/O and prevents another candidate, fallback, or cache write from starting for that request. Cache mutations check ownership inside the settings lock, including writes from ordinary STT fallback; a retired request cannot publish after waiting for another settings transaction. Earlier completed writes remain valid. Temporary probe WAVs are removed even when generation or writing fails. Active native computation and remote model computation are not forcibly stopped.

`GET /api/audio-probe` reads that cache. Browser reads have a five-second deadline covering headers and body, a 16 MiB body limit, and validation of at most 512 entries. Failed or malformed reads preserve confirmed warnings and show a retryable error; a confirmed empty response clears them. Query changes and forced refresh retire older reads, including a quick return to a previous query.

`DELETE /api/audio-probe` clears a selected provider/base/model entry. After the server confirms the exact entry and removal result, Reset clears only that captured local entry and becomes available immediately; optional model discovery continues in the background. Probe-cache presence is separate from response-provider activation and from successful end-to-end speech rehearsal.

## Health and verification

`/api/health` reports server-resolved URLs and model load blocks. Matching actual models URL/model/credentials reuse one probe within a request, while each role retains its configured display URL. `/api/ready` combines frontend, audio limits, selected STT, response model, TTS, and enabled agent checks. A reachable lazy Kokoro service is available before warm-up; a custom service without a configured health contract remains unverified. TTS health must contain recognized readiness signals with real boolean flags and valid text fields. Malformed data degrades TTS readiness while Korina liveness stays HTTP 200. Loaded-model diagnostics use the same identifier/count/byte bounds as discovery and pin the matched selected model; `models_info` reports omitted/invalid IDs. Two explicit GGUF paths do not match merely because their basenames agree.

Changes to the registry or provider behavior need API contract, URL/auth, discovery ownership, process ownership, and browser checks. Run the commands in [testing](testing.md), then rehearse actual services using the [hackathon guide](hackathon.md). [Observed verification](verification.md) distinguishes local software/model evidence from physical hardware and hosted CI.

Meta profile checks use mocked provider responses. They do not establish an authenticated live Meta call, useful presentation output, or prompt-cache hits. The hackathon target remains one context-dependent voice interaction with a fact retained in the current session; record the actual kickoff SHA and later event additions in the [preparation ledger](hackathon.md). Current history/report state is not durable memory.
