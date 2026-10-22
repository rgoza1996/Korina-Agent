# Refactor verification — 22 October 2026

Work is tracked in [PR #24](https://github.com/rgoza1996/Korina-Agent/pull/24) and [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23). This page records observed local evidence; it does not claim event eligibility, physical microphone behavior, GPU performance, or a passing hosted Actions run.

## Deterministic and browser checks

Immutable checkpoint `5d0c7a4` passed **960 Python tests with 93% coverage on Python 3.12.3**, **324 executable Node tests**, and **60 real Chromium tests**. Clean Python 3.10.20, 3.11.15, and 3.13.14 environments each passed 959 tests with one intentional skip: the optional actual Whisper decoder is installed and exercised in 3.12. Real FFmpeg compressed-audio limits ran in every environment. Every matrix run has a separate JUnit report and isolated runtime state.

Ruff correctness checks across production code, tests, scripts, and package entrypoints; projectwide mypy over 66 modules; ESLint; and isolated source/wheel builds passed. A separate installed-wheel environment verified all **29 bundled assets**, byte-identical to the committed snapshot, every served JavaScript module, protocol discovery, rejected-settings disk preservation, and writable state outside the package.

Chromium used immutable assets and a freshly started isolated backend. Coverage includes native MediaRecorder/VAD capture, full decodable auto-stop and silent-idle rotation, buffered WAV/PCM SSE playback, suspended AudioContext resume and Stop, settings bootstrap/draft/save ownership, Done/Escape/backdrop close, activation failure/retry, model discovery/candidate credentials, channel discovery/save ownership, permissions, and agent off/on cancellation. Replaced source-text assertions now execute those behaviors; lower Python counts from deleting duplicate harnesses do not remove their runtime coverage.

Backend tests include role-aware provider retention, protected refresh/config conflicts, atomic model-folder edits, bounded catalogs/metadata/completions/events, shared STT capability/fallback policy, resident Whisper leases, malformed ACK recovery, generation-safe permission admission, executor-start retry, explicit channel recovery, connected agent/ACK/TTS cleanup, and real chat/STT socket disconnect/cancellation with the same pool worker recovering. ACK rebuild transactions survive client timeouts; waiting on their health snapshot does not freeze unrelated HTTP requests. Current health ownership rejects old successes and failures before they can change provider status, capture limits, or ACK results.

GitHub Actions jobs did not start because the account payment/spending limit blocked them. These local results do not claim a passing hosted run. The earlier fully passing `fb996a1` checkpoint and actual model observations below remain historical evidence for their stated snapshots.

## Transcription disconnect recovery

At `5d0c7a4`, all three transcription paths share chat's async request ownership. A local TCP provider deliberately held a real multipart/FFmpeg transcription request while the browser disconnected. Before the fix, the provider connection stayed open during a 650 ms observation, a fresh request remained blocked behind the sole pool token, and one temporary capture directory remained. After the fix, each provider connection closed within that window; a fresh request completed on the same worker before the held provider was released, with no borrowed pool token or capture directory remaining. The three cancel-to-fresh observations were 0.228, 0.230, and 0.253 seconds. These are local cancellation fixtures, not inference latency measurements.

Deterministic and socket cases cover ASGI 2.0/2.4, stalled headers/bodies, outer cancellation, model discovery, no fallback/cache mutation after retirement, and no later decoder/native admission. An independent raw-task probe held a native iterator step during cancellation: the lease and temporary directory remained until that active step returned, then cleanup released them and discarded its late segment. This does not force-cancel active native inference or establish that remote model computation stops.

A separate offline repeat used the archived `5d0c7a4` source and the actual cached Whisper weights described below. Buffered, partial, and SSE requests all returned HTTP 200 with nonempty transcripts in 4.134, 2.565, and 2.145 seconds respectively; SSE ended with `done` and no error. All three retained the known opening-word recognition error. One `tiny.en:cpu:int8` model remained cached and its active lease count was zero after the requests. This checks native inference compatibility after the ownership change; it is not a physical-microphone or controlled latency test.

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

Chrome for Testing 156.0.8078.4, driven by Playwright 1.64.1, completed live conversations through the real browser modules and local HTTP services. Chromium's fake microphone consumed espeak-ng speech with leading and trailing silence. Native `MediaRecorder` produced WebM/Opus chunks, browser VAD ended the utterance, and actual Whisper `tiny.en` CPU/int8 inference handled both partial and final transcription. The response model was a deterministic local OpenAI-compatible fixture; Kokoro synthesis and native WebAudio playback were real. No paid service or external response LLM was involved. The following table records the initial run.

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

After the partial-queue refactor, the same fake-microphone WAV and actual speech services completed another turn in **20.745 seconds**, compared with the initial **30.920 seconds**. These are two single observations, not a controlled latency benchmark. Full-final mode now discards unnecessary queued partial work instead of draining it before submitting the complete recording.

| Follow-up stage | Observed result |
| --- | --- |
| Partial work | One HTTP 200; the next request was aborted by the browser at 10.228 s |
| Final transcription | Started at 10.250 s; HTTP 200 SSE completed at 13.946 s; 8.82 s audio; 3.674 s inference |
| Response | HTTP 200; upstream payload contained exactly one system turn and one current user turn |
| Synthesis/playback | Real Kokoro HTTP 200; 60,600 samples at 24 kHz; 2.525 s native playback; RMS 0.04764 |
| Completion/cleanup | 20.745 s click-to-playback-ended; no page/console errors; Stop released microphone and recorder |

The transcript retained the same known small-model recognition errors. Aborting the browser request does not establish that already running backend inference was cancelled.

A repeat from an immutable archive of `64d74c7` exercised the bounded capture, strict SSE, shared HTTP transport, and leased Whisper cache together. Native playback reached its `ended` event **21.642 seconds** after Live was clicked. One partial request completed, the next was aborted, and final SSE handled 8.76 seconds of audio in 4.495 seconds. The local response fixture received the current user once; actual Kokoro returned 60,600 samples at 24 kHz, and the 2.525-second native buffer completed with nonzero RMS. The browser raised no page/console errors, and Stop released the microphone. This third timing is another single observation, not a controlled comparison.

## Browser pipeline with real response and agent inference

Immutable `fb996a1` also completed a separate CPU run with actual response and agent inference. Both roles used [HuggingFaceTB/SmolLM2-135M-Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct), pinned to revision `12fd25f77366fa6b3b4b768ec3050bf629380bac`. A disposable local OpenAI-compatible wrapper loaded safetensors with `trust_remote_code=False` and `local_files_only=True`; it applied the model's chat template without rewriting Korina's messages or returning fixture replies. No paid inference service was used.

Environment: Python 3.12.3, Torch 2.14.1+cpu, Transformers 5.20.2, float32, four intra-op threads and one inter-op thread. The wrapper limited the response alias to 96 new tokens, agent alias to 192, and context to 8192 tokens; actual completions were shorter. Whisper, Kokoro, Chromium, and the fake microphone used the versions and boundaries above. Speech weights were warmed before the turn; Whisper loaded during the run.

| Stage | Observed result |
| --- | --- |
| Final Whisper SSE | 8.82 seconds of audio / 141,120 samples; 4.637 seconds inference; HTTP 200 |
| Actual response model | 217 prompt tokens / 9 generated tokens; 5.527 seconds inference; “The World is a local speech test.” |
| Actual Kokoro and playback | One SSE chunk, 61,200 samples at 24 kHz; 2.55 seconds of native playback; nonzero RMS and `ended` |
| Complete voice turn | **29.365 seconds** from Live click through playback completion |
| Actual agent model | 465 prompt tokens / 64 generated tokens; 22.094 seconds inference; busy → idle, nonempty report, no error |
| Agent follow-up | The report appeared once in the next response's current message; actual response inference completed in 5.138 seconds |
| Readiness and cleanup | All six read-only smoke requests passed, including enabled agent readiness; no browser/console errors; Stop released capture; disposable services stopped |

The final transcript was “The World. This is a local speech test. The Blue Bicycle is beside the garden.” The intended opening was “Hello world.” The agent echoed its JSON input rather than producing a useful state report, and the follow-up mostly repeated the earlier answer. This small model establishes real inference and injection transport, **not presentation-quality conversation or agent reasoning**. Select and rehearse suitable event models separately. The timing is one observation and is not comparable as a performance benchmark with earlier fixture-response runs.

An earlier attempt completed real response speech in 39.268 seconds, but its harness stopped immediately after accepting agent work. That attempt did not establish agent completion. The successful repeat explicitly polled busy/idle status until a nonempty report and recorded the actual model calls.

### Repeat at `d14a371`

Immutable `d14a371` completed the same actual CPU model pipeline in **27.537 seconds**, with separate output files preserving the earlier observations. It used the same pinned weights, fake-microphone input, model prompts, CPU thread settings, and inference boundaries above. This is another single observation, not a controlled speed comparison.

Final Whisper handled 8.82 seconds / 141,120 samples in 4.669 seconds and returned the same transcript and known opening-word error. Actual response inference took 5.491 seconds (217 prompt / 9 generated tokens). Kokoro produced 61,200 samples at 24 kHz; the 2.55-second native buffer had nonzero RMS and reached `ended`. Actual agent inference took 20.278 seconds (465 / 64 tokens), and its report appeared exactly once in the next actual response (328 / 10 tokens, 7.079 seconds inference). All six read-only smoke requests passed, including enabled-agent readiness. Stop released capture, no page/console errors occurred, and disposable services shut down.

The response and agent still mostly echoed input. This repeat verifies the refactored transport, state, and playback together; it does not establish useful presentation-model quality or physical microphone/speaker performance.

## Agent and permission integration

Seven integration scenarios passed using the real backend, adapter/worker/event state, HTTP transport, and Chromium modules. Model responses came from a deterministic local OpenAI-compatible fixture; speech used 240 silent PCM frames. This check did not perform speech recognition, external model inference, or tool execution.

- A sanitized transcript reached the agent model prompt once and produced a normal report.
- The report entered exactly one next response without duplicating the current user turn.
- A real permission event flowed through browser recognition of “yes”, the permission-answer HTTP endpoint, and a follow-up report.
- Reset completed in 72 ms while a model request was deliberately blocked; its stale report was discarded and fresh queued context completed.
- Clear Session discarded a delayed chat reply and retained empty conversation state.
- Agent model HTTP concurrency remained one, and the browser reported no runtime or console errors.

The harness called exported browser delivery/poll functions for deterministic timing while ordinary intervals remained active with long delivery thresholds. Separate executable and Chromium regressions cover failed permission confirmation, retry coalescing, and preserving a newer pending question.

Transport follow-up tests hold connected agent and ACK HTTP requests across reset, disable, cache clear, or shutdown. Ownership changes close stale I/O and let the same worker complete fresh work; generation checks prevent stale publication. This establishes client connection/worker recovery, not cancellation of provider-side model computation. The in-loop watcher checks ownership about every 50 ms; operating-system DNS resolver shutdown remains platform-dependent.

## Still requiring rehearsal

- Physical microphone/headset capture, speakers, echo cancellation, and human interruptions.
- The selected presentation response LLM and enabled agent model, including useful model quality and full turn latency. The tiny CPU model above establishes integration only.
- GPU execution and remote HTTPS/CORS deployment if used at the event.
- Event-specific Meta requirements and successful hosted CI after account billing is resolved.

Use the [rehearsal checklist](hackathon.md) and record further evidence in issue #23.
