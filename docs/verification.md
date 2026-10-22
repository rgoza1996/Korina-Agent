# Refactor verification — 22 October 2026

Work is tracked in [PR #24](https://github.com/rgoza1996/Korina-Agent/pull/24) and [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23). This page records observed local evidence; it does not claim event eligibility, physical microphone behavior, GPU performance, or a passing hosted Actions run.

## Deterministic and browser checks

The core refactor at `22b401c` passed 347 Python tests in clean Python 3.10.20, 3.11.15, 3.12.3, and 3.13.14 environments. Python coverage was 85%. Real FFmpeg compressed-audio limits ran without skips. Ruff, projectwide mypy, ESLint, source/wheel builds, and an isolated wheel serving every JS asset plus CSS/index/config/console entrypoints passed.

The integrated follow-up checkpoint passed **388 Python tests with 86% coverage, 44 executable Node tests (+17 label assertions), and 13 real Chromium tests**. Ruff's complete Pyflakes correctness rules, projectwide mypy, ESLint, source/wheel builds, and a fresh isolated wheel check passed. The wheel check served every UI asset, exercised protocol discovery/health/writable configuration, and confirmed the PyAV compatibility bound in package metadata.

The Chromium suite includes actual app boot/settings/channel rendering and controlled provider-response fixtures for delayed saves/activations, endpoint independence, HTTP failure recovery, reset races, and current-turn history. The four-version matrix above belongs to the earlier core checkpoint; the later total was run on Python 3.12.3.

GitHub Actions checks did not start: the account payment/spending limit blocked jobs. Local results are not a substitute claim for successful hosted CI.

## Actual Whisper inference

Environment: Linux aarch64, CPython 3.12.3, four CPU threads, `tiny.en`, int8, faster-whisper 1.2.1, CTranslate2 4.8.3, PyAV 18.1.0, ONNX Runtime 1.31.0, FFmpeg 6.1.1. Public model: `Systran/faster-whisper-tiny.en`, revision `0d3d19a32d3338f10357c0889762bd8d64bbdeba`.

The source was generated espeak-ng speech, 5.786 seconds / 92,581 samples at 16 kHz after conversion. No inference function was mocked.

| API | Observed result | Wall time |
| --- | --- | ---: |
| `/api/transcribe` | HTTP 200; real transcript | 5.998 s, cold process/model load with weights already cached |
| `/api/transcribe/partial` | HTTP 200; real transcript | 3.617 s |
| `/api/transcribe/stream` | HTTP 200; converting → transcribing → segment → done; no error | 3.693 s |

Expected: “Hello world. This is a local speech test. The blue bicycle is beside the garden.” All three paths returned “The World. This is a local speech test. The Blue Bicycle is beside the garden.” The small model's initial recognition error is recorded rather than treated as exact accuracy. This is a runtime smoke, not an accuracy benchmark or physical microphone test.

The first fresh install selected PyAV 19 and failed every path with `metadata_errors` rejected by `av.open()`. PyAV 18.1.0 fixed the actual requests. The Whisper extra now constrains `av>=11,<19`, with a model-free real WAV decoder regression. See the [upstream compatibility issue](https://github.com/SYSTRAN/faster-whisper/issues/1589).

## Actual Kokoro synthesis

Environment: Linux aarch64, CPython 3.12.3, Kokoro 0.9.4, Torch 2.14.1+cpu, Misaki 0.9.4, Transformers 5.20.2, four Torch threads, `af_heart`, model `hexgrad/Kokoro-82M`, and the automatically loaded `en_core_web_sm` 3.8.0 language model. CUDA was unavailable.

| Check | Observed result |
| --- | --- |
| First real chunk | 19.01 s including model/language-model warm-up; 57,000 samples / 2.375 s speech |
| WAV validity | Mono, 24 kHz, signed 16-bit PCM, correct header/frame count, nonzero audio RMS |
| Buffered local HTTP | HTTP 200 `audio/wav`; 62,400 frames / 2.6 s speech; 4.56 s generation |
| SSE local HTTP | HTTP 200; first chunk at 3.99 s; two chunks / 111,600 total samples; complete at 8.80 s |
| Busy admission | HTTP 503 with `Retry-After: 1`; health remained available |
| Post-inference health | Loaded CPU pipeline, runtime available, no CUDA |

CPU generation on this host was slower than the resulting audio duration. Warm models and measure latency on the actual presentation hardware. These timings exclude a real response LLM and a physical audio device.

Generic PyPI Torch attempted to pull CUDA dependencies on this CPU host. Installing Torch from its official CPU wheel index first, then `.[tts]`, produced a clean `pip check` and successful inference. The setup guide documents that reproducible CPU path.

## Actual browser voice pipeline

Chrome for Testing 156.0.8078.4, driven by Playwright 1.64.1, completed one live conversation through the real browser modules and local HTTP services. Chromium's fake microphone consumed espeak-ng speech with leading and trailing silence. Native `MediaRecorder` produced WebM/Opus chunks, browser VAD ended the utterance, and actual Whisper `tiny.en` CPU/int8 inference handled both partial and final transcription. The response model was a deterministic local OpenAI-compatible fixture; Kokoro synthesis and native WebAudio playback were real. No paid service or external response LLM was involved.

| Stage | Observed result |
| --- | --- |
| Rolling transcription | Three real `/api/transcribe/partial` requests, all HTTP 200 |
| Final transcription | HTTP 200 SSE; status → status → segment → done; 8.82 s recorded audio at 16 kHz; 2.872 s inference |
| Response | HTTP 200 `/api/chat`; local fixture replied “The local voice test is complete.” |
| Synthesis | HTTP 200 Kokoro SSE; one chunk, 60,600 samples at 24 kHz; 5.382 s request |
| Playback | Native running AudioContext; nonzero audio RMS 0.0477; 2.525 s buffer reached its `ended` event |
| Complete turn | 30.920 s from clicking Live Conversation to playback completion, including the utterance, queued partial inference, final STT, and synthesis |
| Shutdown | Live mode stopped; microphone stream released; recorder inactive; disposable services stopped |

The page raised no JavaScript or console errors. The final transcript was “The Long World. This is a local speech test. The Blue Mysicle is beside the garden.” The intended opening and bicycle differed, so this also records the small model's recognition errors rather than claiming accuracy. WebAudio scheduling and completion do not establish physical speaker audibility; fake capture does not establish physical microphone behavior.

This run exposed a duplicated current user turn in the upstream chat payload. The frontend now sends only prior turns in `history`, leaving the backend to append `message` once. It retains the raw current turn in visible/agent history, while interruption context and a pending agent report belong only to the current model message. Executable Node, Python payload, and Chromium regressions cover that contract. A separate follow-up Chromium → actual backend → local upstream fixture run confirmed the current user appears once for both ordinary and interrupted/injected turns; that check intentionally intercepted TTS with empty SSE and did not repeat speech inference. Node regressions also cover adopting a lower authoritative agent cursor after a server restart, clearing stale agent context, and rejecting old in-flight delivery results.

## Still requiring rehearsal

- Physical microphone/headset capture, speakers, echo cancellation, and human interruptions.
- The selected real response LLM and enabled agent model, including model quality and full turn latency.
- GPU execution and remote HTTPS/CORS deployment if used at the event.
- Event-specific Meta requirements and successful hosted CI after account billing is resolved.

Use the [rehearsal checklist](hackathon.md) and record further evidence in issue #23.
