# Architecture Overview

Full source-grounded report: [docs/refactor/architecture-report.md](../refactor/architecture-report.md)

## Product split

- **Korina Converse** — local-first voice/design chat channel for agents. It owns the human-facing loop: microphone, VAD, STT, response LLM, TTS, barge-in, interrupts, and permission prompts.
- **Korina Agent** — local-first agent designed around Korina Converse's voice-input focus. It currently maintains state reports/events and is planned to grow into a fuller execution runtime.

## Runtime shape

```text
Browser / Korina Converse UI
  -> FastAPI backend on :8001
    -> STT path: faster-whisper or compatible multimodal/audio LLM
    -> Response LLM path: local/OpenAI-compatible provider
    -> TTS path: Kokoro/OpenAI-compatible endpoint
    -> Agent side channel: /api/agent/* events, injections, permission answers
```

## Current implementation highlights

- Python backend package under `korina/` with route modules and service modules.
- Browser frontend split into native ES modules under `Korina/js/`.
- Backend-owned provider registry exposed by `GET /api/capabilities`.
- Model catalog/capability helpers for endpoint-loaded models, local GGUFs, LM Studio catalogs, and mmproj sidecars.
- CI-safe pytest suite plus separate live regression smoke script.

## Main follow-up themes

1. Formal agent protocol for third-party agents.
2. Korina Agent execution/runtime layer beyond state reports.
3. Composite readiness across backend, LLM, STT, TTS, ACK, and static assets.
4. Portable install/config paths.
5. Probe-backed model capabilities.
6. Optional live/self-hosted regression tier.
7. Public-doc vs internal-runbook cleanup.
