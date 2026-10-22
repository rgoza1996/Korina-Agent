# Changelog

All notable changes to Korina are documented in this file.

## [Unreleased] — hackathon refactor

- Serve portable checkout and wheel assets with user-writable generated state.
- Share STT payload/fallback handling; bound decoded audio and keep blocking inference off the event loop.
- Bound agent work, preserve reset isolation, and support core-only adapters through a compatibility bridge.
- Remove duplicate frontend delivery state; repair live speech, microphone cancellation, failed-save recovery, and independent endpoint settings.
- Separate liveness/readiness, authenticate health probes, and report unverified custom TTS accurately.
- Replace shared Kokoro WAV files and unbounded threads with bounded synthesis and correct incremental audio.
- Bound ACK cache work and prevent cleared/disabled jobs from publishing stale audio.
- Add Python coverage/type/lint, executable Node tests, real Chromium smoke, wheel builds, and an optional read-only self-hosted gate.
- Refresh setup, architecture, rehearsal, and repository-backed pages; remove the obsolete one-off migration script.

Real microphone/model/GPU rehearsal is required before promoting this work to the release branch.

## [0.0.1] — 2026-07-03

First public release of Korina — a local voice conversation agent.

### Highlights
- Browser-based voice conversation UI with modular frontend (`Korina/`).
- FastAPI backend (`korina/`) exposing STT, TTS, agent, and provider routes.
- STT integration paths: faster-whisper (local) and multimodal LLMs with audio capability.
- TTS integration via Kokoro streaming server (`kokoro-streaming-server.py`).
- Provider activation for LM Studio and llama.cpp, with health probes and graceful fallback.
- Settings UI for local model/provider configuration and GGUF roots.
- Endpoints modal for live provider/model introspection.
- Regression test suite (`tests/`) and GitHub Actions matrix (Python 3.10 / 3.11 / 3.12).
- Documented release-gate workflow (`README.md` §Release status, `docs/testing.md`).