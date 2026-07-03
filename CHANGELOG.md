# Changelog

All notable changes to Korina are documented in this file.

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