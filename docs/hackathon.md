# Hackathon rehearsal

Updated 2026-10-22. The refactor is tracked in [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23) and the `codex/hackathon-refactor` branch. The event-specific Meta requirements have not yet been supplied; this guide prepares the existing local voice product without claiming a particular Meta integration or eligibility.

## Demo story

Korina separates a responsive voice conversation from a background agent. The presenter talks naturally, receives spoken answers, interrupts a reply, and shows an agent report entering the next response without recursive self-triggering. The agent currently maintains reports and permission requests; tool execution is future work.

## Before the event

1. Install from the [README](../README.md), including `ffmpeg`, Whisper, Kokoro, and a separately managed response LLM.
2. Select the exact model IDs and endpoints in Settings. Prefer Whisper CPU for the first rehearsal; use audio-model STT only after verifying actual support.
3. Keep microphone access on localhost or HTTPS. Check browser-visible TTS access and configured CORS origins for a remote UI.
4. Warm the selected Whisper model and Kokoro voice, then send a real chat turn. Finish any model downloads before presenting.
5. Run `python scripts/check_stack.py`. Resolve every false check from `/api/ready`.
6. Save a known working configuration outside Git and keep a spare headset and a typed-input fallback available.

## Rehearsal sequence

| Step | Expected evidence |
| --- | --- |
| Open the UI | Module/CSS requests load; provider pills show server-resolved URLs |
| Speak a short request | Transcript and audible reply; no console ReferenceError |
| Ask a second question | Response follows the new turn and uses the current conversation context |
| Interrupt during speech | Audio stops, interruption is transcribed, live capture resumes |
| Enable the agent | Transcript delivery appears in debug panel; cursor advances once |
| Receive a report | Context is injected once into the next successful response |
| Clear the session mid-report | Old in-flight output does not reappear in the new session |
| Stop the response provider | Live status identifies the failed provider; no silent success |
| Restart the provider | One successful poll restores accurate readiness |
| Speak again | Model, audio, and agent flow recover without a page reload |

Record actual model IDs, device, first-turn and warm-turn timings, browser, and any unresolved failures in issue #23. Unit tests and mock providers cannot supply that evidence.

## Current verification boundary

The refactor adds portable checkout/wheel assets, shared STT payloads and SSE handling, bounded background agent work, request validation/body caps, accurate health/readiness, executable frontend checks, and portable service templates. The optional Kokoro implementation removes shared temporary WAV files and unbounded request threads.

Real microphone capture, real model synthesis/transcription, GPU behavior, remote HTTPS deployment, and event-specific requirements remain separate validation steps. Do not describe Hermes stub support or saved execution settings as working tool execution.
