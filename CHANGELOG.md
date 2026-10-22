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
- Preserve complete native microphone recordings within duration/byte budgets; rotate silent captures and reject oversized recordings without cropping WebM.
- Share bounded provider HTTP, including connected-I/O deadlines, redirect credential isolation, finite JSON validation, and capped error details.
- Keep one selected Whisper model resident, pin lazy inference safely, and return retryable busy responses for conflicting model switches.
- Validate STT/TTS stream framing and downloaded audio; keep Stop active through native playback and reject truncated or overlong speech.
- Scope inherited credentials to matching API bases, preserve current settings during model refresh, and reject stale model-discovery results.
- Discover unsaved agent endpoints without forcing settings writes; keep candidate credentials out of URLs and diagnostics.
- Serialize agent enable transitions, retire disabled in-memory work, and preserve ordinary conversation across off/on recovery.
- Preserve managed providers used by another active role; preflight conflicts before teardown and report unconfirmed stops accurately.
- Share refresh preflight and reject racing role changes while providers are starting; preserve unrelated settings saves.
- Make model-folder append/delete atomic, preserve deliberate empty lists, and reject invalid new paths before saving.
- Keep Kokoro inference admission occupied until active native work and generator cleanup finish, including concurrent disconnect/close.
- Return encodable field-level validation errors for malformed numbers/Unicode without echoing raw inputs.
- Add Python coverage/type/lint, executable Node tests, real Chromium smoke, wheel builds, and an optional read-only self-hosted gate.
- Refresh setup, architecture, rehearsal, and repository-backed pages; remove the obsolete one-off migration script.

Blank STT/agent credentials now inherit the response key only at the same normalized API base URL. Configure explicit role credentials for an independent endpoint. A model/device switch during active Whisper transcription returns a retryable busy response; the idle model cache is replaced once current inference releases it.

Actual CPU Whisper/Kokoro and native browser capture/playback have local evidence. Physical audio devices, the selected real response/agent LLMs, GPU hardware, and event-specific rehearsal are required before promoting this work to the release branch.

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
