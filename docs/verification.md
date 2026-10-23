# Refactor verification — 23 October 2026

Work is tracked in [PR #24](https://github.com/rgoza1996/Korina-Agent/pull/24) and [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23). This page records observed local evidence; it does not claim event eligibility, physical microphone behavior, GPU performance, or a passing hosted Actions run.

## Current archived verification

Work resumed after the earlier 15% usage stop under the updated boundary: stop at **10% weekly usage remaining or when the weekly allowance resets to 100%**, whichever arrives first. Immutable code `8441911` passed **1171 Python tests / 94% coverage** on Python 3.12.3. Clean 3.10.20, 3.11.15 and 3.13.14 environments, plus a separate minimum-dependency 3.10 environment, each passed **1170 tests with one intentional optional-decoder skip**. Python 3.12 exercised the actual decoder. All **411 Node tests, 85 real Chromium tests**, expanded Ruff/ESLint, mypy over 67 modules, isolated source/wheel builds, and all **30 matching installed assets** passed.

The [versioned local summary](verification/8441911.json) records the full source SHA, per-tier outcomes, dependency versions, and asset hashes. Each Python tier used isolated runtime state and JUnit output from an immutable Git archive; every tier finished before Chromium started. Sniffio 1.3.1 is deliberately present in all environments. The constrained floor tier passed with FastAPI 0.118, Starlette 0.42, AnyIO 4.4 and HTTPX 0.27. Immediate predecessor failures and the copied-context worker-entry regression explain those boundaries in [testing](testing.md).

Meta fixtures follow actual capabilities → browser discovery query → configuration/model/auth handling, replacing only outbound HTTP. Independent negative audits cover ten browser-query cases, seven agent credential cases and four redirect chains: explicit/default keys, changed candidates, generic/custom endpoint isolation, normalized suffixes, literal direct keys, and credential stripping across hosts or HTTPS downgrade. Returning to the original host does not restore a stripped key. Native Chromium verifies both provider controls, saved blank credential controls, model-default reasoning hints, real settings persistence and the next response request's deliberate 2048-token limit.

Both response and Meta/OpenAI-compatible agent paths now distinguish empty output with `finish_reason: "length"` from an intentional empty completion. Response errors preserve pending agent context without adding blank speech/history. Agent errors use existing `last_error`/`agent_error`, preserve the previous report, and allow the same worker to recover after a deliberate budget change. Ten new worker/API cases cover default 512, manual 2048, no automatic retry, intentional empty/stop compatibility and visible truncated reports. Independent current/floor subsets passed before the complete checkpoint. Shared STT parsing and Anthropic behavior are unchanged.

The October 23 checkpoint also preserves zero interrupt padding through saves/runtime and removes inactive cloud-STT controls without deleting legacy settings. Candidate credential-env discovery requires the picker’s explicit intent header before config/network work. Nonempty JSON bodies require a JSON media type consistently on current and minimum dependencies; multipart/slash redirects, bodyless actions and size-limit precedence remain covered. Three native cases exercise the cross-origin boundary, same-origin picker and untyped settings body.

The combined checkpoint includes truthful remote-response wording, credential-precedence hints and response capability labels. Inferred missing audio support is not displayed as a verified response-model limitation; red failure labels preserve known capabilities. Capability data and STT filtering are unchanged. Earlier [complete `d48a374`](verification/d48a374.json) and [focused UI `8583016`](verification/8583016.json) summaries retain their own source identities.

These fixtures establish software behavior. The separate live Meta observations below establish authenticated inference and measured cache reuse for their stated inputs. Recent hosted jobs did not start because GitHub reports account billing/spending limits. Physical hardware and the final event acceptance remain separate.

The follow-up at `10a844a` holds new full-final partial dispatch during endpoint silence and resumes it when speech returns. At `8441911`, Whisper checks request ownership after cache/inference-lock waits before admitting native work; active steps drain safely. Five synchronized backend regressions cover waiting factory/iterator admission and active-step cleanup; three executable frontend cases cover silence/resumption behavior. These changes do not establish a measured latency improvement.

A separate offline actual-Whisper smoke at **`8441911`** passed buffered, partial and SSE endpoints using cached **base.en CPU/int8**. All three recognized the same question correctly, returned HTTP 200, reused the cached model and released their leases. Observed request times were 5.6521, 4.9649 and 4.5071 seconds; SSE ended with `done` and no error. This is sequential native compatibility, not cancellation, physical-audio or controlled timing evidence. [Source hashes, runtime and boundaries](verification/whisper-8441911.json).

## Authenticated Meta rehearsal at `637e43c`

On October 23, an isolated server and browser used immutable source **`637e43c60169b624c9e1c99da2553d3ac5c593e8`**, the direct `https://api.meta.ai/v1` endpoint, and account-discovered **`muse-spark-1.3-contributor`**. Response and optional agent budgets were deliberately set to **2048**, with model-default reasoning and no tools or automatic retry. Credentials remained outside Git and browser state. [Redacted inputs, usage, versions, checks and cleanup](verification/meta-637e43c.json).

The browser first supplied the fictional fact through actual `askAndSpeak`: Cedar's rehearsal is Friday at ten in the morning. A generated espeak-ng microphone question then asked, “What is my project called, and when is our practice?” Actual Whisper **base.en CPU/int8** returned “What is my project called and when is our practice?” The Meta reply was: “Your project is called Cedar. Your practice is Friday at ten in the morning.” The request contained the seed user/assistant history once and its current question once. This demonstrates context supplied by Korina and retained in the current session, not durable memory.

Whisper processed 6.48 seconds / 103,680 samples in 9.191 seconds. The voice response model call took 6.024 seconds. Kokoro 0.9.4 `af_heart` on CPU returned two complete PCM SSE chunks totaling **124,800 samples at 24 kHz / 5.2 seconds**. Both native buffers reached `ended` with nonzero audio. The turn took **37.654 seconds from Live click through native playback completion**. Stop released capture, no page errors occurred, all six read-only readiness checks passed, and disposable service ports closed. This is one local observation, not a latency benchmark or physical speaker/headset proof.

The optional actual Meta agent produced a useful compact report in 6.830 seconds; its bounded report entered one successful follow-up request and was consumed. Buffered Kokoro also returned valid mono 24 kHz, 16-bit WAV with 64,200 frames in 5.774 seconds. A separate Python channel completed a nonempty actual agent report without error in 8.602 seconds, then shut down its worker. These are report/transport checks, not tool execution.

An initial tiny.en attempt was stopped: its generated microphone loop triggered unintended interruptions and the first question was misrecognized. It is not a passing voice run. The accepted repeat used a single utterance followed by silence, warmed base.en, and a clearer question. A warm-up harness field-name error was corrected before that repeat and made no generation call. Failure logs remain separate, and their paid usage remains included below.

Two earlier direct calls through Korina's unchanged response/config/HTTP code correctly retained the same synthetic fact in 4.040 and 3.472 seconds. Their response-module bytes match the verified `69f7965` code; the browser run above has the full archived source identity. The two earlier direct calls and the accepted seed/voice pair reported zero cached tokens.

A separate synthetic catalog experiment used an identical 1,806-token prompt prefix with repeated/changing final questions. Returned `usage.prompt_tokens_details.cached_tokens` was **0, 1777, 1777**; all answers selected the correct fictional code/room. No `prompt_cache_key` was added. Those positive metrics establish reuse in that experiment; they do not guarantee short-prompt hits, cache retention or latency savings. The response UI still does not expose provider usage.

All **16 generation calls**, including the stopped initial voice attempt, have an **estimated token cost of $0.002674362** at the [published Contributor rates](https://dev.meta.ai/docs/pricing-rate-limits), below the authorized $3 budget. This is calculated from returned usage, not an invoice or account spending cap, and excludes unrelated account traffic. Contributor inputs/outputs may train Meta models; this rehearsal used wholly synthetic content. Both Drive-provided credentials authenticated, but there were **zero paid OpenRouter generations**.

### Follow-up latency experiment

Three later synthetic calls reused the accepted context through the archived response function. A scratch wrapper added Meta's documented `reasoning_effort` for two calls; **no production setting or default changed**. Default, minimal and low returned the correct fact in 8.635, 1.487 and 2.609 seconds respectively, with 347, 50 and 300 reasoning tokens. Each is a single sequential observation; earlier default calls varied materially, so these are compatibility observations rather than a causal latency/quality benchmark. [Exact parameters, usage and scope](verification/meta-reasoning-637e43c.json).

These calls add an estimated $0.0002280; aggregate task usage is **19 calls / $0.002902362**. The earlier 16-call voice/cache record retains its historical scope. A future explicit Meta effort control should preserve blank/model-default behavior and remain separate from the local on/off reasoning setting.

## Earlier complete archived checkpoint

Immutable checkpoint `96a8d5c` passed **1051 Python tests with 94% coverage on Python 3.12.3**, **357 executable Node tests**, and **76 real Chromium tests**. Clean Python 3.10.20, 3.11.15, and 3.13.14 environments each passed 1050 tests with one intentional skip: the optional actual Whisper decoder is installed and exercised in 3.12. Real FFmpeg compressed-audio limits ran in every environment. Every matrix run has a separate JUnit report and isolated runtime state.

Ruff correctness checks across production code, tests, scripts, and package entrypoints; projectwide mypy over 66 modules; ESLint; and isolated source/wheel builds passed. A separate installed-wheel environment verified all **30 bundled assets**, byte-identical to the committed snapshot, every served JavaScript module, protocol discovery, rejected-settings disk preservation, and writable state outside the package.

Chromium used immutable assets and a freshly started isolated backend. Coverage includes native MediaRecorder/VAD capture, full decodable auto-stop and silent-idle rotation, buffered WAV/PCM SSE playback, suspended AudioContext resume and Stop, settings bootstrap/draft/save ownership, Done/Escape/backdrop close, activation failure/retry, model discovery/candidate credentials, channel discovery/save ownership, permissions, and agent off/on cancellation. Replaced source-text assertions now execute those behaviors; lower Python counts from deleting duplicate harnesses do not remove their runtime coverage.

Backend tests include role-aware provider retention, protected refresh/config conflicts, atomic model-folder edits, bounded catalogs/metadata/completions/events, shared STT capability/fallback policy, resident Whisper leases, malformed ACK recovery, generation-safe permission admission, executor-start retry, explicit channel recovery, connected agent/ACK/TTS cleanup, and real chat/STT socket disconnect/cancellation with the same pool worker recovering. ACK rebuild transactions survive client timeouts; waiting on their health snapshot does not freeze unrelated HTTP requests. Current health ownership rejects old successes and failures before they can change provider status, capture limits, or ACK results.

GitHub Actions jobs did not start because the account payment/spending limit blocked them. These local results do not claim a passing hosted run. The earlier fully passing `fb996a1` checkpoint and actual model observations below remain historical evidence for their stated snapshots.

## Actual model pipeline at `96a8d5c`

This archived source used actual Whisper tiny.en CPU/int8, SmolLM2-135M-Instruct pinned to `12fd25f77366fa6b3b4b768ec3050bf629380bac`, and Kokoro 0.9.4 with `af_heart`. Python 3.12.3, Torch 2.14.1+cpu, Transformers 5.20.2, and four CPU threads ran cached weights offline without fixture model replies or paid inference. Chromium 156.0.8078.4 / Playwright 1.64.1 used a generated fake-microphone input; physical audio remains separate. Native playback finished **36.608 seconds** after Live was clicked. Whisper handled 8.82 seconds / 141,120 samples in 3.241 seconds, returning “The Long World. This is a local speech test. The Blue Mysicle is beside the garden.” Both the opening and bicycle recognition differ from the intended speech. The response mostly repeated that input; its 218 prompt / 21 generated tokens took 8.443 seconds.

Kokoro returned three real SSE chunks totaling 161,400 samples at 24 kHz, or 6.725 seconds of speech. All three native buffers had nonzero RMS and reached `ended`. The response and audio were longer than the earlier repeat, so these single observations do not establish a performance regression or improvement.

Actual agent inference completed in 18.653 seconds (480 / 54 tokens), and its 235-character report entered the next actual response exactly once. That response took 8.125 seconds (331 / 21 tokens). Full read-only readiness passed with the agent enabled; no page/console errors occurred, Stop released capture, and all three disposable service ports closed.

Two additional checks used the same running services. Buffered Kokoro returned HTTP 200 with valid mono 24 kHz, 16-bit WAV: 60,600 frames, nonzero RMS, 4.543 seconds generation. A separate in-process Python channel completed actual agent inference and returned a finished nonempty report without error in 13.687 seconds; its model call took 13.526 seconds (437 / 36 tokens). Its worker shut down afterward.

The tiny model still echoed input and reporting instructions. This verifies actual inference, transport, completion ownership, and native playback. Useful presentation-model quality and physical audio still require rehearsal.

## Later focused probe and gateway checks

After the complete `96a8d5c` checkpoint, `437283e`, `acca9fe`, and `98f8955` address probe ownership in the browser, explicit probe batches, and STT fallback. Real HTTP fixtures cover held response headers/bodies under disconnect, scope cancellation, and raw task cancellation: connected I/O closes, no later candidate or stale cache write starts, and the same sole pool worker serves a fresh request. Held settings-lock cases verify that cache reads and mutations recheck ownership after waiting. Browser socket cases exercise the five-second deadline, query/forced-refresh retirement, preserved failure state, and Reset completion independent of discovery. Targeted native picker, discovery, and boot checks passed. These are focused results; the complete checkpoint above retains its own source identity and counts.

At `f571274`, four gateway publication cases verify explicit registration during lazy default construction, concurrent first readers, and falsey custom adapters. At `fd24762`, real temporary-file regressions verify close/unlink after synthetic WAV generation or partial-write failure, without provider calls or cache changes. Independent review passed for both fixes.

## Synthesis and channel ownership

At `96a8d5c`, buffered WAV, streamed WAV, and PCM SSE synthesis share request ownership through model preparation and playback. Twenty-four real HTTP fixture cases cover disconnect and AnyIO cancellation during load, first inference, and a later step, plus raw task cancellation for both stream formats. They verify one ASGI receive consumer, no later native step after retirement, retained inference admission until active work drains, and a fresh successful request on the same cached runtime. Eight preparation-error cases retain JSON HTTP 400/500/503 responses before stream headers.

The original four-case streaming audit also reproduced the boundary: previously, closing a client during held model loading still admitted a new first inference step and left fresh speech at HTTP 503. After the fix, loading may finish safely, but its retired request starts no first step; the same cached runtime serves fresh speech. A first step already running finishes before cleanup. These cases use controlled native-work fixtures and do not establish model latency or force cancellation of native computation.

Built-in channel tests cover overlapping HTTP work, reset/off-on transitions, completed reports between initial polling and admission, duplicate suppression followed by newer HTTP output, and event-ring eviction. Admission captures its cursor atomically while the worker is idle; busy requests admit no work and can retry. Reset cancels the old wait, duplicate status carries its own bounded report, and lost history requires explicit recovery. Custom adapter memory and legacy HTTP coalescing retain their existing contracts.

## Transcription disconnect recovery

At `5d0c7a4`, all three transcription paths share chat's async request ownership. A local TCP provider deliberately held a real multipart/FFmpeg transcription request while the browser disconnected. Before the fix, the provider connection stayed open during a 650 ms observation, a fresh request remained blocked behind the sole pool token, and one temporary capture directory remained. After the fix, each provider connection closed within that window; a fresh request completed on the same worker before the held provider was released, with no borrowed pool token or capture directory remaining. The three cancel-to-fresh observations were 0.228, 0.230, and 0.253 seconds. These are local cancellation fixtures, not inference latency measurements.

Deterministic and socket cases cover ASGI 2.0/2.4, stalled headers/bodies, outer cancellation, model discovery, no fallback/cache mutation after retirement, and no later decoder/native admission. An independent raw-task probe held a native iterator step during cancellation: the lease and temporary directory remained until that active step returned, then cleanup released them and discarded its late segment. This does not force-cancel active native inference or establish that remote model computation stops.

A separate offline repeat used the archived `5d0c7a4` source and the actual cached Whisper weights described below. Buffered, partial, and SSE requests all returned HTTP 200 with nonempty transcripts in 4.134, 2.565, and 2.145 seconds respectively; SSE ended with `done` and no error. All three retained the known opening-word recognition error. One `tiny.en:cpu:int8` model remained cached and its active lease count was zero after the requests. This checks native inference compatibility after the ownership change; it is not a physical-microphone or controlled latency test.

## Native interruption and rendering

At `340dd8b`, Chromium completed a normal live turn, stopped native playback when interruption was detected, recorded and natively decoded the complete interruption WebM, supplied its text and approximate interruption context once to the next chat request, played the revised reply, and resumed capture. A second case stopped Live while that interruption's chat was held: its fetch aborted, capture was released, and the late reply produced no history entry or speech. These cases use a fake microphone and controlled meter/STT/model/TTS responses; MediaRecorder, Opus/WebM encoding and decoding, and buffered audio playback are native. They do not establish human interruption timing or actual-model quality.

At `6df3960`, the waveform renderer retained its bars between frames and adjusted their count on resize. A local 300-draw fixture created 136 divs instead of 40,800, with identical 136 bar heights and geometry. Native identity/resize checks and existing capture/interruption tests passed. Its observed draw time changed from 188 to 47 ms in that fixture; this is not a general rendering or voice-latency benchmark.

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

## Earlier browser pipeline with real response and agent inference

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
- The actual presentation headset and longer conversations with the selected response/agent models, including useful output and first/warm timing on that hardware.
- GPU execution and remote HTTPS/CORS deployment if used at the event.
- Final Official Rules/submission artifacts, actual kickoff SHA, and successful hosted CI after account billing is resolved.

Use the [rehearsal checklist](hackathon.md) and record further evidence in issue #23.
