# Agent and Converse protocol, version 1

This document describes the implemented contracts for attaching an agent to Korina. Protocol version 1 preserves the existing `/api/agent/*` HTTP API and adds read-only capability discovery. `/openapi.json` contains the HTTP request schemas.

The built-in Korina Agent produces state reports, events, and permission questions. It does not execute tools, shell commands, or external actions. Settings such as project trust and autonomous mode influence its report prompt; they do not establish an execution runtime.

## Three separate interfaces

| Interface | Purpose | Registration and consumers |
|---|---|---|
| `AgentAdapter` | Synchronous Python contract for one background agent: `id`, `capabilities`, `submit_turn(UserTurn)`, `poll_events(cursor)`, `answer_permission(PermissionAnswerEvent)`, `reset()`, `health_check()` | `korina.agents.register_adapter(adapter)` selects a process-wide adapter. `agent_gateway()` resolves it; the default is `KorinaAgentAdapter`. |
| `LegacyAgentHttpAdapter` | Optional richer Python extension: `snapshot()`, `submit_transcript_legacy(AgentTranscriptRequest)`, `answer_permission_legacy(AgentPermissionAnswer)` | `AgentHttpBridge` detects this extension structurally. It delegates complete legacy requests and preserves the extension's response dictionaries. Core adapters do not have to implement these methods. |
| `ConverseChannel` | Async Python channel interface: `send(ConverseRequest)`, `stream(ConverseRequest)`, `cancel()`, optional `health()` | Register channel objects with `register_converse_channel(channel)`. The channel registry and the selected agent adapter are separate registries. The default `korina` channel uses the current agent gateway. |

`POST /api/chat` remains the independent response-LLM endpoint used by the browser voice conversation. Selecting a Converse channel does not reroute `/api/chat`, microphone/STT requests, or TTS. There is currently no public HTTP `send`/`stream` route for the Python `ConverseChannel` interface.

Gateway selection and publication are synchronized. An explicit adapter registered while the lazy default is being constructed remains selected; concurrent first readers receive the same published adapter. Adapter construction and calls run outside the selection lock.

## Discover the selected adapter

```sh
curl -s http://127.0.0.1:8001/api/agent/capabilities
```

The built-in adapter returns:

```json
{
  "ok": true,
  "protocol_version": 1,
  "adapter_id": "korina",
  "adapter_capabilities": {
    "adapter_id": "korina",
    "supports_state_reports": true,
    "supports_injections": true,
    "supports_permission_flow": true,
    "supports_streaming": false,
    "http_compatible": false
  },
  "legacy_http_supported": true,
  "http_operations": {
    "status": true,
    "events": true,
    "reset": true,
    "transcript_prompt": true,
    "transcript_injection": true,
    "permission_answer": true
  }
}
```

Discovery reads adapter properties without calling a model, probing agent health, or submitting work. `adapter_capabilities` contains the adapter's declared Python capabilities. Its existing `http_compatible` flag is not a test for the legacy extension. Use `legacy_http_supported` to discover complete legacy delegation and `http_operations` for what the HTTP bridge exposes.

A core-only adapter reports `legacy_http_supported: false`. It still supports HTTP status, events, reset, and prompt submission through the bridge. HTTP injection is unavailable even if that adapter declares a native injection capability: the core `submit_turn` interface cannot represent full transcript injections. Permission answers are available for a core adapter only when `supports_permission_flow` is true. Availability does not mean the agent is enabled or that a model endpoint is ready.

## HTTP requests and limits

Agent JSON request bodies are limited to **2 MiB**, counting actual received bytes as well as declared `Content-Length`. Oversized bodies return HTTP 413 before request parsing. Invalid `Content-Length` returns HTTP 400. Nonempty JSON bodies require one `application/json` or `application/*+json` Content-Type, optionally with a charset; missing, ambiguous or other types return HTTP 415. Bodyless reset remains supported. Schema violations return HTTP 422 with a `detail` validation list containing only error type, location and message.

A transcript is an ordered list of at most **64** objects. Each object must contain exactly `role` and `content`, both strings. `role` is one of `user`, `assistant`, `system`, or `tool`; `content` is at most **8192 characters**. Extra per-turn keys are rejected. Unrecognized top-level model fields currently follow Pydantic's default ignore behavior; clients should send the documented fields.

| Endpoint | Request and behavior | Success envelope |
|---|---|---|
| `GET /api/agent/capabilities` | No body. Describes the current adapter and bridge. | Discovery object above. |
| `GET /api/agent/status` | No body. Reads a snapshot. | `{"ok":true,...snapshot}`. |
| `GET /api/agent/events?after=0` | `after` is a nonnegative integer; default `0`. Returns available events with IDs greater than the cursor. | `{"ok":true,"events":[...],...snapshot,"last_event_id":cursor}`. The poll result supplies the authoritative cursor. |
| `POST /api/agent/transcript` | `transcript: []`, `delivery_mode: "prompt"`, `reason: ""`, `turn_count: 0` are the defaults. Mode must be `prompt` or `injection`; reason is at most 512 characters; turn count is nonnegative. | Built-in adapter: `{"ok":true,"accepted":true,"queued_as":"prompt"|"injection","status":snapshot}`. Check `accepted`, not HTTP 200 alone. |
| `POST /api/agent/permission-answer` | `request_id: ""` (at most 128 characters), `answer: ""` (at most 512), `transcript: []`. | Built-in adapter returns the transcript-submission envelope. Core bridge returns `queued_as: "permission_answer"`. |
| `POST /api/agent/reset` | No body. Calls the current adapter's reset method. | `{"ok":true,"status":snapshot}`. Treat reset as complete only after successful HTTP status and `ok:true`. |
| `GET /api/agent/models` | Queries the configured built-in agent model endpoint. This route is independent of a replacement adapter's discovery contract. | `{"ok":true,"models":[...],"provider":"...","default":"...","error":null}`. Discovery failure is HTTP 200 with `ok:false`, empty models, and an error. |
| `POST /api/agent/models` | Discovers an unsaved candidate using optional strict string fields: `agent_provider` (64 characters), `agent_base_url` and `llm_base_url` (8192 each), `agent_api_key` (8192), `llm_api_key_env` (512). Unknown fields are rejected. No config save, provider activation, or agent submission occurs. | Same discovery envelope as GET. Failures expose HTTP status/error type; HTTP 422 validation omits raw inputs. |
| `POST /api/agent/state-report` | Built-in synchronous report-generation compatibility route, independent of a replacement adapter. Defaults: `transcript:[]`, `previous_report:""`, `model:null`, `max_tokens:512`. Previous report: at most 8192 characters; model: at most 512; max tokens: 1–8192. | `{"ok":true,"state_report":"...","model":"..."}`. Model failure returns HTTP 500 with `detail`. When disabled: `{"ok":true,"state_report":"","disabled":true}`. |

The built-in snapshot fields are:

```json
{
  "busy": false,
  "status": "idle",
  "last_report": "",
  "pending_injections": 0,
  "last_error": null,
  "last_event_id": 0
}
```

Candidate model discovery merges fields into one saved configuration snapshot in memory. Omitted fields retain saved values, except a saved direct agent key is inherited only when the candidate resolves to the same actual model-discovery API URL. Changed endpoints require an explicitly supplied key or matching-response-base credential fallback. An explicit blank agent key uses normal fallback rules. The browser supplies all five current form fields in the POST body, preserving query ownership while an ordinary settings save is pending; secrets stay out of URLs.

### Built-in model providers

The built-in model settings are separate from adapter registration. `GET /api/capabilities` lists `openai-compatible`, `meta` (Meta Model API), and `anthropic` in `agent_providers`. Generic and Anthropic bases are editable; the named Meta profile fixes its API base to `https://api.meta.ai/v1`, using `/models` for authenticated discovery and `/chat/completions` for reports. Discover and select an account-enabled model ID rather than assuming a fixed Meta model name. Selecting this profile does not activate a local provider or change the version-1 agent HTTP envelopes.

An explicit `agent_api_key` takes priority. Otherwise credentials can inherit the response environment key only for the same actual API base, or use the selected profile's own registry default. For Meta at its canonical base, that default is `MODEL_API_KEY`; a generic/custom endpoint does not acquire it merely from a Meta hostname. Explicit response environment-name overrides retain their scope, and a missing environment value yields no Authorization header. Candidate discovery retains these same rules, including the changed-URL direct-key boundary above.

Meta report requests omit the local response `reasoning` flag. Blank `agent_reasoning_effort` leaves the model default; an explicit supported value sends `reasoning_effort` from that report's own captured configuration. It does not inherit response effort or change STT behavior. This model profile adds no tool execution, durable memory, or browser voice rerouting. The focused demo keeps Whisper STT and Kokoro TTS local, with the ordinary `/api/chat` response using supplied current-session context. Mocked profile tests do not establish live Meta access or prompt-cache hits. See [provider contracts](capabilities.md) and the [hackathon preparation ledger](hackathon.md) for the one-interaction scope and actual kickoff-SHA record.

For Meta and OpenAI-compatible reports, empty text with `finish_reason: "length"` is an output-budget error. The worker exposes it through `last_error` and an `agent_error` event, preserving its previous report. Increase **State report max tokens** in Korina Agent settings deliberately before retrying; the default remains 512 and there is no automatic increase or retry. An intentional empty completion ending with `stop` remains suppressed without replacing memory, while visible truncated reports retain their existing behavior.

The HTTP bridge returns HTTP 200 with `accepted:false, disabled:true` for transcript submissions and permission answers while `agent_enabled` is off, before dispatching to either core or legacy adapters. The built-in service also guards its pending-work generation, rejecting submissions whose enabled read predates a disable/reset transition with `accepted:false, stale:true`. A core prompt submission with no nonempty user turn returns `accepted:false`. A core adapter receives only the **latest nonempty user content**, as a `UserTurn` with `session_id:"legacy-http"` and the submitted turn count. Assistant turns are never converted into user turns.

Unsupported core injection or permission operations return HTTP **409**, with a string `detail`; they do not submit work. Unexpected adapter failures may return HTTP 500. Clients must check HTTP status, handle non-JSON errors, and then inspect acceptance. When enabled, optional legacy adapters own their richer acceptance behavior and may add response fields.

## Submit and poll

With the agent enabled and its model endpoint configured, submit a complete transcript snapshot:

```sh
curl -s http://127.0.0.1:8001/api/agent/transcript \
  -H 'Content-Type: application/json' \
  -d '{"transcript":[{"role":"user","content":"Help me prepare the demo."}],"delivery_mode":"prompt","reason":"manual","turn_count":1}'
```

An accepted built-in response has this shape; state can advance while the response is being assembled:

```json
{
  "ok": true,
  "accepted": true,
  "queued_as": "prompt",
  "status": {
    "busy": true,
    "status": "working",
    "last_report": "",
    "pending_injections": 0,
    "last_error": null,
    "last_event_id": 0
  }
}
```

Poll with `GET /api/agent/events?after=0`, process the returned events in ID order, and retain the returned `last_event_id` for the next poll. A normal built-in report event contains:

```json
{
  "id": 2,
  "created_at": 1792656000.0,
  "type": "state_report",
  "priority": "normal",
  "report": "Priority: normal\nIntent: prepare the demo.",
  "message": "Priority: normal\nIntent: prepare the demo.",
  "delivery_mode": "prompt",
  "reason": "manual",
  "turn_count": 1,
  "agent_input": {
    "delivery_mode": "prompt",
    "reason": "manual",
    "turn_count": 1,
    "previous_report": "",
    "transcript": [{"role":"user","content":"Help me prepare the demo."}],
    "model": "configured-agent-model"
  },
  "duplicate_suppressed": false
}
```

IDs and timestamps are assigned by the built-in producer. The HTTP wire uses **flat event dictionaries**, not the nested `AgentEvent.payload` field from the optional Python schema. Consumers should tolerate additional event fields and unknown event types. `agent_input` is diagnostic context and includes transcript data.

The built-in event ring retains at most **100** events and **4 MiB** of serialized UTF-8 JSON, evicting the oldest whole events when either limit is reached. It is not a durable queue or a complete conversation log. A cursor older than retained history can miss events; there is no separate expiry marker. `last_report` in the status snapshot helps clients recover their current summary.

The complete events HTTP response, including its status snapshot, also has a **4 MiB** JSON budget. It returns the newest whole events that fit, with additive `events_truncated` and `events_omitted` fields describing omissions from this poll result. These fields do not count earlier ring eviction. Retain the authoritative `last_event_id` even when events were omitted. An invalid or oversized custom-adapter snapshot/event returns a bounded HTTP 502 error instead of a partial or malformed diagnostic.

Built-in IDs increase within a session and restart at zero on reset. If a returned authoritative cursor is lower than the client's cursor, treat that as a new server session and clear the old cursor/context. Multiple browser sessions share the process-wide adapter and reset state; there is no per-user isolation in this local API.

## Prompt, injection, completion, and reset

The built-in runtime runs one model request at a time. It retains one replaceable follow-up prompt and the latest five pending injection snapshots. A burst of submissions does not create one thread per request.

- `prompt` starts a report job when idle. While a job runs, the latest prompt becomes the next job; displaced queued prompts contribute bounded injection context. The compatibility response can label this `queued_as:"injection"` even though a follow-up prompt is scheduled.
- `injection` stores context for the next prompt. An injection delivered while idle does not itself start a model call or promise an immediate report.
- Report generation uses the last `agent_max_turns` turns (default 16, clamped to 1–64), includes only user/assistant roles, and trims each included turn to 2000 characters. The final previous-report context is trimmed to its last 4000 characters. The `agent_input` diagnostic records this same prepared context, using the same configuration snapshot as the model request. Accepted HTTP limits are larger than these prompt-building limits. Queued injection records retain only the last 6000 serialized characters that can survive the existing previous-report trim.
- Empty reports emit `agent_status` with `status:"empty_report_suppressed"`. Identical noncritical reports within 60 seconds emit `status:"duplicate_report_suppressed"` instead of another report. This status retains the bounded `report` for an awaiting Python channel; the browser continues to ignore status events. Model failures emit `agent_error` with priority `important` and a `message`.
- Reset clears retained events, cursor, reports, queued prompts/injections, error and duplicate-suppression state. Its generation change prevents in-flight old jobs from publishing into the new session. Owned background HTTP connections close when the next ownership check observes the reset (about every 50 ms), allowing the same worker to serve fresh context. Closing a connection does not establish cancellation of remote model computation. Direct `/state-report` calls keep their ordinary deadline and are not owned by the background session.
- Transcript and permission admission recheck their original built-in generation after configuration reads and dispatch. A reset or disable/re-enable during admission rejects that retired request before publishing its permission event or queuing work. If a worker cannot start, admission restores its prior idle/status state and retires the failed executor so a later request can retry.

Clients must suspend agent polling and delivery while a reset is pending, discard responses begun in the old local session, and resume only after confirmed reset success. A failed reset must keep delivery blocked until retried. The browser implements this behavior while allowing the human conversation to continue.

The enable checkbox has a separate agent-activity epoch. Disabling immediately retires queued reports, pending permissions, deferred interruptions, and owned browser agent requests/speech, while preserving conversation history, ordinary chat, and Live capture. Its explicit configuration writes are serialized without coalescing off/on transitions; ordinary form saves do not overwrite this controlled field. A failed disable remains ahead of a queued enable and leaves agent delivery paused until a toggle retries successfully. Only after an on-to-off configuration change is successfully persisted does the server clear the built-in agent's in-memory reports, events, and pending generation/queues, which closes old owned HTTP I/O. No custom adapter reset is invoked, and durable external memory is untouched. Re-enabling first baselines the selected adapter's event cursor, skipping its earlier backlog before fresh delivery starts. A pending chat reply that already included a retired agent report is discarded with a request to ask again; an ordinary chat reply continues. Provider/model edits do not implicitly reset an adapter, and Clear Session retains its explicit full-reset behavior.

## Priorities, permissions, and sanitization

Priorities are `low`, `normal`, `important`, and `critical`. After sanitization, the built-in classifier applies these rules in order, case-insensitively:

1. A `Permission request:` phrase (allowing whitespace before the colon), or any critical cue below, makes the report `critical` regardless of a lower declared priority.
2. The first line beginning with `Priority: low|normal|important|critical` supplies the explicit priority. Leading whitespace and whitespace before the colon are allowed. An explicit `critical` or `important` becomes `normal` if an uncertainty cue appears and rule 1 did not apply.
3. Any other explicit priority is preserved. Without one, an important cue makes the report `important`; otherwise it is `normal`.

Literal cues match whole phrases, with word boundaries at both ends:

| Cue group | Phrases |
|---|---|
| Critical | `permission request:`, `explicit permission`, `approve this command`, `authorization required`, `destructive command`, `delete data`, `data loss`, `secret leaked`, `credential exposed` |
| Uncertainty | `garbled`, `whisper`, `transcription uncertainty`, `stt`, `unclear transcript`, `<think>`, `state report`, `korina agent interrupt`, `acknowledgment loop`, `ack phrase` |
| Important | `critical:`, `urgent:`, `blocked:`, `security risk`, `cannot continue without`, `task is blocked` |

Hidden reasoning is removed before classification, so a hidden cue cannot escalate a visible report. These are lexical compatibility rules; a report's priority is not an authorization to execute an action.

A critical report containing `Permission request:` is emitted as `permission_request`; other report updates use `state_report`. `agent_status` is bookkeeping. `agent_error` carries failure information. A permission event's event ID is used as the browser's request identifier:

```sh
curl -s http://127.0.0.1:8001/api/agent/permission-answer \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"2","answer":"yes","transcript":[{"role":"user","content":"Yes, continue."}]}'
```

The server accepts an answer string; it does not restrict this field to a yes/no enum. The browser recognizes affirmative/negative utterances and sends `yes` or `no`. An empty built-in answer falls back to `no`. The built-in adapter records a `permission_answer` event and submits the supplied transcript for another report. A core-only adapter receives one `PermissionAnswerEvent`, whose transcript is the latest nonempty user turn, and owns its own follow-up behavior. There is no built-in tool execution or permission-enforcement engine behind this exchange.

The browser confirms an answer only after a successful HTTP response with `accepted:true`. Transport errors, invalid JSON, and rejected acceptance retain the pending question and allow retry without adding failed answer turns to history. Concurrent answer submissions are coalesced. A successful old answer cannot clear or speak over a newer pending question. Permission methods own follow-up work, so confirmation does not send a second immediate transcript request. The HTTP operation has no idempotency guarantee; a retry after an unknown transport outcome may reach the adapter again.

Generated built-in reports are sanitized case-insensitively before storage and classification: complete `<think>...</think>` blocks are removed, and an unfinished `<think>` removes the remaining suffix; `[Ack Phrase]` removes the following text through the first `.`, `?`, or `!`, while a bare `Ack Phrase` label is removed; `[Korina Agent Interrupt]` or `Korina Agent Interrupt:` removes the remainder of that line. Runs of three or more newlines become two, and outer whitespace is stripped. The synchronous `/state-report` compatibility utility returns the generator's raw text; this sanitization belongs to the background transcript pipeline.

The browser also filters acknowledgement/interrupt entries from model and agent history, removes priority headers from spoken text, and avoids storing spoken interrupts as ordinary model context. External clients must apply equivalent input filtering themselves; the transcript HTTP schema validates shape and length, not every UI label.

Normal reports become hidden context for the next response. Configured important reports can wait for a playback boundary; critical reports can interrupt playback. The browser's interrupt thresholds, permission interruption toggle, cooldown, and deferred-interrupt behavior are presentation policy, not wire-level delivery guarantees. Pending report text is budgeted with the user message to the `/api/chat` 8192-character ceiling; the user turn is preserved. A report is retained across failed chat requests and when no injection space remains. Successful responses must not erase a newer report received in flight.

## Python channel use and selection

`UserTurn` carries `text` (at most 8192 characters), `session_id` (at most 128), nonnegative `turn_count`, optional nonnegative `audio_seconds`, and a timestamp defaulting to the current time. `PermissionAnswerEvent` carries request ID, answer, and a text transcript. Core adapter methods must be safe for the worker-thread calls made by the HTTP bridge and default channel; background completion must be observable through `poll_events`.

`ConverseRequest` is a Python dataclass with `transcript`, `delivery_mode="prompt"`, `reason=""`, and `metadata={}`. `ConverseResponse` has `text=""`, `finished=True`, `error=None`, and `metadata={}`. These are Python interfaces, not additional HTTP request/response envelopes.

The default `KorinaConverseChannel` joins non-assistant transcript `text`/`content` values into a single user turn and reads `session_id`/`turn_count` from metadata. It does not implement legacy HTTP injection semantics from the dataclass's mode/reason fields. It submits once, captures the pre-submission event cursor, and waits for a fresh report, permission request, error, or explicit suppression event. Per-channel sends are serialized; each captures one adapter for submission, polling, and snapshot recovery. A replacement registered during that send applies to the next send. The report wait times out after 30 seconds. A timeout does not prove remote work stopped. `stream()` yields an initial unfinished thinking event and one final response; it does not expose model-token streaming.

For the built-in adapter, core turn admission requires an idle worker and captures its completion cursor atomically with admission. An unrelated active HTTP or channel job produces a definite busy rejection; it never silently queues that core turn behind another report. Legacy HTTP transcript submissions retain their replaceable follow-up queue. HTTP reset or agent off/on transitions retire an existing built-in channel wait as `error:"cancelled"`; they do not let a new session's report complete that old send. These checks use only built-in state and do not reset or impose its epoch on custom adapters.

`cancel()` invalidates the active send and resets that send's captured adapter, even if the gateway has since selected another adapter. With no active send, it resets the current gateway adapter. Results and failures returned by old blocking polls or snapshots are discarded as `error:"cancelled"`. Cancellation during the initial cursor poll prevents the turn from being submitted afterward. New sends wait until synchronous reset finishes; cancelling the `cancel()` caller does not stop that reset or release the barrier early. Callers should cancel their own queued `send()` tasks if they no longer want them submitted. The adapter owns cancellation of its internal work; an already-running synchronous call cannot be forcibly stopped by this channel.

If an admitted send times out, its caller is cancelled, or polling fails, the channel cannot reliably associate a later report with a new turn. A built-in event-retention gap likewise returns a truncation error instead of borrowing another turn's report. The channel returns a recovery error for further sends until an explicit `cancel()` successfully resets the captured adapter, including after gateway replacement. A failed reset leaves that barrier in place for retry. There is no automatic custom-adapter reset on timeout. Cancelling a queued send before admission leaves the active send unaffected; completed empty/error report events allow the next turn normally.

An adapter may raise `korina.agents.AgentAdmissionRejected` only when it guarantees that no background work was admitted. The channel returns that rejection immediately and permits a later send without resetting adapter memory. The built-in adapter uses it for disabled-agent, busy-worker, and retired-session admissions, so callers can retry after enabling the agent, finishing active work, or clearing a session. Other submission exceptions remain uncertain and retain the explicit recovery requirement.

The following standalone example uses its own process-local registry and built-in
worker. Configure and enable the built-in agent in that process's runtime settings
and provide its credentials before running it. It does not connect to an already
running Korina HTTP service. Application lifespan startup registers the default
channel automatically; standalone callers must register it explicitly.

```python
import asyncio
from korina.converse import ConverseRequest, ensure_default_registered, get_converse_channel
from korina.services.agent_service import shutdown_agent_worker

async def main():
    ensure_default_registered()
    try:
        response = await get_converse_channel().send(ConverseRequest(
            transcript=[{"role": "user", "text": "Prepare the demo."}],
            metadata={"session_id": "demo", "turn_count": 1},
        ))
        print(response.text, response.error)
    finally:
        shutdown_agent_worker()

asyncio.run(main())
```

Selection endpoints are:

| Endpoint | Response |
|---|---|
| `GET /api/converse/channels` | `{"channels":["korina", "..."]}` in registration order. |
| `GET /api/converse/channel` | `{"channel":"korina"}`. If none is active, `{"channel":null,"registered":[...]}`. |
| `POST /api/converse/channel/{name}` | `{"channel":"name","ok":true}` on success; HTTP 404 for an unregistered name. |

HTTP selection changes are serialized under the registry lock. The route first merges `converse.channel` into saved config atomically with other in-process config writers, then publishes the active selection. A persistence failure returns an error and leaves the previous channel active; it does not claim a successful switch. Python callers can use `set_active_converse_channel(name, persist=callback)` for the same save-before-publish behavior, or omit the callback for an in-memory selection only.

On startup, Korina restores the saved channel if registered; otherwise it selects `korina`. Register custom channels during startup before restoration. `HermesStubChannel` is an opt-in contract stub, not a network integration with Hermes.

`converse.allowed_origins` supplies the CORS origin list at app startup. The default is `http://127.0.0.1:8001` and `http://localhost:8001`. A malformed saved value (including a string instead of a list, or any non-string list entry) uses those loopback defaults without rewriting the file. A valid explicit `[]` allows no cross-origin requests; same-origin requests still work. Valid string lists are preserved, and wildcard access is enabled only by an explicit `"*"` entry. Restart the app after changing this setting.

## Compatibility and migration

Version 1 is additive: existing HTTP names, validated fields, flat event envelopes, and optional legacy adapter dictionaries remain supported. There is no new execution API, session-ID HTTP routing, WebSocket protocol, or automatic routing of the spoken response through the selected channel.

Existing full-transcript integrations can implement `LegacyAgentHttpAdapter` alongside the core protocol. New core-only integrations can start with prompt delivery and event polling, inspect operation discovery before requesting injections/permissions, and handle HTTP 409 explicitly. Use the selected adapter's events and cursor rather than importing the built-in state singleton. Built-in `/state-report` and `/models` remain separate compatibility utilities; they do not become replacement-adapter methods through registration.

Older servers can return HTTP 404 for `/api/agent/capabilities`; do not interpret a missing discovery route as permission to assume every optional operation exists. Clients should preserve known legacy behavior for their configured server or require explicit integration configuration. A future incompatible wire change should advertise a new protocol version; additive event fields must remain ignorable.
