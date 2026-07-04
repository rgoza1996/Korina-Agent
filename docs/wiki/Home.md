# Korina Agent / Korina Converse

Korina Converse is the local-first, voice-first design chat channel. It owns microphone input, VAD/endpointing, STT, response-LLM calls, TTS playback, barge-in, agent interrupts, and permission prompts.

Korina Agent is the local-first agent built for that channel. Today it receives transcript deliveries, maintains compact state, emits background reports/events, and asks Converse to inject context or speak important/critical interrupts. The intended direction is for Converse to host Korina Agent plus other compatible agents such as Hermes Agent, OpenClaw, or another local/OpenAI-style agent.

## Key repo docs

- [README](../../README.md)
- [Current architecture report](../refactor/architecture-report.md)
- [Provider capability contract](../capabilities.md)
- [Testing guide](../testing.md)

## Architecture backlog mirrored to GitHub Issues

Created from the 2026-06-25 architecture refresh on `beta`. These are the long-term direction items, not bug reports.

- [#3 Formalize Korina Converse agent protocol](https://github.com/rgoza1996/Korina-Agent/issues/3) — high
- [#4 Finish Korina Agent local-first execution runtime](https://github.com/rgoza1996/Korina-Agent/issues/4) — high
- [#5 Add composite readiness for local voice stack](https://github.com/rgoza1996/Korina-Agent/issues/5) — medium
- [#6 Make deployment paths, model roots, and local binaries portable](https://github.com/rgoza1996/Korina-Agent/issues/6) — medium
- [#7 Replace static model capability assumptions with probe-backed metadata](https://github.com/rgoza1996/Korina-Agent/issues/7) — low
- [#8 Add optional live/self-hosted regression tier](https://github.com/rgoza1996/Korina-Agent/issues/8) — low
- [#9 Audit public docs for environment-specific/private details](https://github.com/rgoza1996/Korina-Agent/issues/9) — low

## Open bug backlog (#10–#16)

Real correctness/robustness issues filed from the 2026-06-25 dogfood pass. Untouched on this branch.

- [#10 CORS misconfiguration is invalid + overly permissive](https://github.com/rgoza1996/Korina-Agent/issues/10) — `allow_credentials=True` with `allow_origins=["*"]` is rejected by browsers.
- [#11 Synchronous urllib I/O blocks the event loop across services](https://github.com/rgoza1996/Korina-Agent/issues/11) — five files block the uvicorn worker thread.
- [#12 Pydantic schemas accept unbounded input (DoS surface)](https://github.com/rgoza1996/Korina-Agent/issues/12) — no `max_length` / `max_items` on request bodies.
- [#13 Unbounded daemon threads + brittle priority classifier in agent subsystem](https://github.com/rgoza1996/Korina-Agent/issues/13) — thread-per-request spawn + unbounded `event_seq`.
- [#14 /api/transcribe/stream is a 343-line single-function monolith](https://github.com/rgoza1996/Korina-Agent/issues/14) — needs to be split into helpers.
- [#15 CI missing lint, type-check, frontend test, and the real runtime deps](https://github.com/rgoza1996/Korina-Agent/issues/15) — workflow only runs pytest with a monkeypatched env.
- [#16 Hardcoded roggoz-specific paths + fragile pkill regex in provider_manager](https://github.com/rgoza1996/Korina-Agent/issues/16) — `/opt/LM-Studio/lm-studio` and `uid=1000` baked in.

## Debug-strip dogfood pass — 2026-06-26

Five debug-strip issues were filed and addressed in a single dogfood pass. Four are closed; one is partial; one is queued.

### Resolved (closed 2026-06-26)

- [#17 ttsHealth pill says 'Kokoro offline' when Kokoro works](https://github.com/rgoza1996/Korina-Agent/issues/17) — closed. `ttsHealth` now reads `tts.ok` from server-aggregated `/api/health`. Fixed by `213cf5e` + `cde45fe`.
- [#19 hostInfo pill shows form values, not server-resolved URLs](https://github.com/rgoza1996/Korina-Agent/issues/19) — closed. `hostInfo` replaced by `#providerReady`, which reads `response_llm.{provider,loaded_model,base_url}` from `/api/health`. Fixed by `cde45fe`.
- [#20 ttsHealth error label hardcodes 'Kokoro' for any TTS failure](https://github.com/rgoza1996/Korina-Agent/issues/20) — closed. Catch path now uses `j.tts_provider` (server-resolved) instead of the form `<select>`. Fixed by `213cf5e` + `cde45fe`.
- [#22 /api/health does not aggregate TTS runtime state](https://github.com/rgoza1996/Korina-Agent/issues/22) — closed. `GET /api/health` now returns a `tts` block with `ok`, `loaded`, `device`, `cuda_available`, `provider`, `base_url`, `error`. Fixed by `213cf5e`.

### Partial — left open

- [#18 pageHealth pill flips red on transient poll failures](https://github.com/rgoza1996/Korina-Agent/issues/18) — partial. The new `providerReady` path is server-truth and no longer depends on form-derived URLs, but the explicit grace-period tracking for the `pageHealth` poll itself is still pending. Comment left on the issue.

### Queued — left open

- [#21 settingsInfo mixes load-time banner with live LLM/STT status](https://github.com/rgoza1996/Korina-Agent/issues/21) — untouched in this pass. Queued for the next dogfood pass with an open question about whether live status should live in the header next to `providerReady` instead of inside the settings modal.

## Note on GitHub Wiki

The GitHub wiki feature is enabled for the repository, but the `.wiki.git` repository was not available to clone or push during the 2026-06-25 docs pass. These `docs/wiki/` pages are the repo-backed fallback and can be copied directly into the GitHub Wiki once it is initialized from the web UI.

The corresponding rendered site lives on GitHub Pages at <https://rgoza1996.github.io/Korina-Agent/Issues.html> (generated from the `gh-pages` branch).