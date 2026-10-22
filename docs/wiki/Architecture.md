# Architecture

Korina Converse owns the human-facing voice loop. Korina Agent receives bounded conversation snapshots and emits background reports, injection context, or permission requests. The current agent does not execute external tools.

```text
Browser ES modules
  microphone → adaptive VAD → STT upload/stream
                           → response LLM → Kokoro/OpenAI-compatible TTS
  transcript snapshots → agent adapter → bounded worker → cursor-based events

FastAPI routes → small service functions → configured model endpoints
```

## Boundaries

- `app_factory.create_app()` composes routes, static assets, CORS, request limits, and lifecycle cleanup.
- `routes/stt.py` prepares audio and selects streaming/fallback paths; shared Whisper helpers produce consistent buffered/partial/SSE results. Blocking model work runs outside the event loop.
- Whisper retains one selected model/device/compute combination. Lazy iterators pin those weights until exhaustion, error, or close; concurrent requests for the same selection share them. A different selection receives a retryable busy response while inference is active, then replaces the idle cached model.
- `services/agent_service.py` owns one worker, one coalesced follow-up prompt, at most five pending injections, a 100-event ring, and reset generation tokens. Session reset invalidates stale in-flight results.
- `agents/` defines adapters. `converse/` defines channel registration and cursor-based response delivery. Hermes remains a stub.
- `services/provider_manager.py` owns local provider activation. Path discovery and executable/socket ownership checks replace personal host assumptions and broad process patterns.
- `runtime/limits.py` resolves shared audio budgets and rejects oversized bodies before request parsing: 2 MiB JSON/control requests and 50 MiB transcription uploads. Schemas also bound fields and conversation turns.
- `tts/` owns optional Kokoro runtime, bounded synthesis admission, and streaming/buffered responses.
- `Korina/js/state.js` holds active UI state. `agent-ui.js` is the agent delivery path; a dormant duplicate EventBus was removed.
- `Korina/js/bounded-recorder.js` owns duration/byte limits for complete manual, live, and interruption recordings. Native chunks remain one decodable file. Silent recordings rotate; confirmed speech becomes a complete turn; an oversized or late capture is discarded whole with recovery text.
- `Korina/js/sse.js` shares bounded UTF-8/event framing across STT and speech, including CRLF and truncated-stream rejection. `response-body.js` bounds downloaded audio and errors; speech playback validates cumulative PCM duration and native buffered metadata before accepting a long reply.

## Provider transport

`runtime/json_http.py` shares HTTPX/AnyIO transport across model discovery, health, response/agent generation, multimodal transcription, provider status, and ACK downloads. Synchronous callers run in workers; connected I/O is asynchronous under a total network deadline. Successful JSON is capped at 2 MiB, error details at 16 KiB, and ACK WAVs at 2 MiB. Raw identity responses avoid unbounded decompression. Redirects share the deadline and discard unread bodies; credentials and cookies cannot cross origins. Operating-system DNS resolver shutdown remains platform-dependent.

Blank role credentials inherit response credentials only at the same normalized API base URL. Explicit independent endpoints require explicit credentials. Model refresh merges only its owned settings under shared activation admission, preserving unrelated edits and probe-cache changes.

## Health and readiness

`GET /api/health` remains HTTP 200 for a live backend with unavailable models. Independent provider probes run concurrently; matching endpoint/model/credentials reuse a probe within that request. Runtime blocks describe response LLM, audio-model STT, agent, TTS, Whisper, and ACK state.

`GET /api/ready` returns a check map for frontend assets, response LLM, selected STT dependencies, TTS, and enabled agent. It returns 503 when a required check fails. A reachable lazy Kokoro pipeline counts as available; rehearsal must warm actual models.

Health advertises the effective upload and decoded duration budgets in read-only `audio_limits`. Browser captures leave encoding/multipart headroom and retain at most 24 MiB. Invalid server limit settings remain visible through `audio_limits_error`, fail readiness, and prevent new recordings. Stop, Clear Session, replacement, and recorder errors release capture timers and owned audio buffers.

## Assets and state

Source checkouts serve tracked `Korina/` files. Built wheels contain the same allowlisted assets; generated config/audio goes into the user data directory. `KORINA_APP_DIR` selects a complete alternate runtime tree. Optional binaries and model roots come from configuration, environment overrides, or discovery.

See [agent protocol](../agent-protocol.md), [setup](../../README.md), [testing](../testing.md), [hackathon rehearsal](../hackathon.md), and [tracker #23](https://github.com/rgoza1996/Korina-Agent/issues/23). Earlier architecture reports and phase plans are historical records, not the current deployment contract.
