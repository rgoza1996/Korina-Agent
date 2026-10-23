# Testing Korina

## Deterministic checks

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest --cov=korina --cov-report=term-missing -q
ruff check korina tests scripts build_support.py kokoro-streaming-server.py
npm ci --ignore-scripts
npm run lint
npm test
python -m build
npx playwright install chromium
npm run test:browser
```

The Python suite uses a temporary runtime tree and fake provider/model boundaries. It needs no Whisper/Kokoro weights, GPU, external LLM, or working systemd session. JavaScript tests execute native modules under Node and exercise health failure recovery, agent delivery, and pending-report handling. Playwright serves the actual app in Chromium and checks boot, settings persistence/failure, independent providers, and channel selection; ESLint rejects undefined references across the frontend.

Keep the Python environment active when running Playwright: its server command uses `python3` from that environment. Once Python starts, the default browser server uses an empty temporary command path, offline Hugging Face access, and a temporary model cache. Its provider process helper reads an empty temporary process directory, so activation cannot discover or signal host provider processes. It copies only the tracked HTML, stylesheet, JavaScript modules, ACK manifest, and example configuration into a temporary runtime. It starts with ACKs and the agent off, blank model selections and credentials, closed loopback provider endpoints, and empty model catalogs. Settings writes and resets in these tests affect that disposable tree, which is removed when the server exits. Saved checkout configuration, generated WAVs, and service scripts are never copied.

The default server binds `127.0.0.1:8765` and refuses to reuse an existing listener, so an occupied port fails the run. An explicit `KORINA_TEST_BASE_URL` bypasses the managed server. Browser tests include real settings writes and session resets: use that override only for a disposable test target. Use `scripts/check_stack.py` for read-only checks against a running demo.

CI runs Python 3.10–3.13, a separate minimum-dependency Python 3.10 tier, coverage artifacts, JavaScript lint/tests on Node 22, Python lint/type checks, real Chromium smoke, and source/wheel builds. An isolated wheel check serves every installed JavaScript module, compares the bundled assets with source, verifies protocol discovery, and checks validated settings writes in disposable state outside the package. Hosted jobs pin Ubuntu 24.04 for a stable native-runtime base; process management and user services are Linux-specific. Foreground API operation does not require systemd.

Automatic runs cover pushes to `alpha`, `beta` and `master`, plus every pull request. Feature branches use a draft PR instead of running the same nine jobs on both push and PR events. The workflow also defines manual `workflow_dispatch` for a branch without a PR or a merge-conflicted PR, whose ordinary PR workflow GitHub does not run. GitHub enables that manual trigger once this workflow reaches the default branch; until then, use the local checks. See [GitHub's event behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows). This avoids duplicate feature-branch work without an API lookup or extra token permissions.

The floor tier installs `.[test]` with `-c tests/constraints/minimum.txt`, runs `pip check`, then the complete Python suite. Use a separate fresh environment for that install. FastAPI 0.118 is required for streamed upload lifetime; Starlette 0.42 is required for the tested streaming cancellation behavior. Boundary tests reproduced failures on their immediate predecessors. Sniffio is deliberately included in test environments so a copied async-context marker cannot hide the worker-entry regression. The HTTP bridge checks the actual asyncio loop rather than mistaking that marker for a running loop. See the [FastAPI release](https://fastapi.tiangolo.com/release-notes/#01180) and [Starlette release](https://www.starlette.io/release-notes/#0420-december-14-2024).

The floor job does not install the FFmpeg CLI. If it is absent, exactly seven CLI integration cases skip: six WAV/WebM upload cases and one compressed-expansion case. Python 3.12 retains its explicit FFmpeg install for that coverage; the separate Python 3.12 speech job exercises the actual PyAV decoder without model weights or a CLI dependency. The local `c430012` floor record had FFmpeg available and retains its original 1430 passes plus one optional Whisper-decoder skip; it is not relabeled as a CLI-free run. A separate immutable `b20c9bf` floor run genuinely hid both CLI executables and passed **1423 tests with eight intentional skips**. [CLI-free outcomes and environment proof](verification/floor-cli-free-b20c9bf.json).

To repeat the installed-wheel check, install the built wheel in a separate environment, change to a directory outside the checkout, then run that environment’s Python with the absolute path to `scripts/check_installed.py --source-assets /absolute/path/to/Korina-Agent/Korina`. The script refuses a source installation and never uses your runtime configuration.

## Current verification checkpoint

The current passing incremental checkpoint is **`58f50d1`**. Eight non-browser phases retain their actual immutable **`410336c`** source; the full browser repeat uses **`58f50d1`**: **1466 Python tests / 95% coverage** on Python 3.12; **1465 plus one optional decoder skip** on Python 3.10, 3.11, 3.13 and minimum dependencies; **475 Node tests**; Ruff/ESLint and mypy over **67 modules**; source/wheel builds with **30 matching installed assets**; and **86/86 Chromium cases**. Python and browser phases ran serially with separate disposable state and one owner of the managed browser port. [Per-phase source identities, versions, outcomes and hashes](verification/58f50d1.json). Only the shared browser fixture changed; product, Python/Node tests, dependencies and assets are byte-identical. This is not a single-revision nine-phase rerun. Automated model replies are fixtures.

This source includes bounded invalid saved-model-path admission, server-resolved Endpoints diagnostics, propagation of filesystem configuration-read errors before mutation, installer argument validation before side effects, and completion of already admitted partial speech after a live mode change. The focused regressions reproduced each original failure; no provider credential, trusted-local control or network exposure policy changed.

The original **`410336c`** browser run passed **85/86**, timing out during initial speech setup before interruption or Stop. Controlled actual Live callbacks proved its wall-time predicate can pass with too little observed speech. Test-only **`58f50d1`** waits for observed voiced duration and retains every native playback/barge/Stop assertion and timeout. [Original failed run](verification/410336c-incomplete.json), [controlled fixture proof](verification/barge-fixture-observed-span.json). Hosted `410336c` subsequently completed **all nine jobs successfully, with zero annotations**, observed through Code Review at **16:50:04 UTC**. [Complete hosted proof](verification/github-410336c.json). The [earlier pending observation](verification/github-410336c-incomplete.json) remains preserved; hosted results do not erase the local setup failure.

The earlier local **`1b4094c`** run was incomplete: both Python tiers failed because another listener owned the test port, and Chromium passed **85/86** with its initial combined playback/barge-recorder precondition timing out. Root had started those phases together; current phases run serially. A separate isolated replay passed **1/1**; it does not replace the failed full run. No CPU cause was established for the browser timeout. Hosted `1b4094c` separately passed all nine jobs with zero annotations. [Preserved local failures](verification/1b4094c-incomplete.json), [separate hosted proof](verification/github-1b4094c.json).

Earlier incremental `4158cd2` separately passed 466 Node checks, lint/build and 86 Chromium cases, retaining five unchanged backend Python phases at `c430012` (1431/95% on Python 3.12; 1430+1 on the other tiers). [That incremental record](verification/4158cd2.json) retains its actual source identities; it is superseded by the current passing incremental record. The genuinely CLI-free `b20c9bf` floor remains 1423 passed/eight skipped.

The earlier `c430012` archive passed all eight non-browser phases, including its then-current **444 Node tests**, but only **84/86 Chromium cases**. Two existing fixtures still expected duplicate authenticated provider requests after discovery began sharing identical requests. `41cb62c` corrects those expectations and checks both role results; all three focused native cases passed. The complete browser repeat at **`5ef3c9d` passed 86/86 in 180.965 seconds** and shut down its managed server. Product code, Python/Node suites and bundled assets are byte-identical between `c430012` and `5ef3c9d`; the later commits change only that browser fixture, action tags and runner documentation. [That incremental record](verification/5ef3c9d.json) preserves each phase's actual source. The earlier `e5dbb05` Python matrix had one stale Meta request-count assertion, corrected in `749a223`; it is not a complete passing checkpoint.

The current Live gate uses **120 ms speech / 150 ms quiet** confirmation rather than rendered-frame counts, expiring unfinished candidates across observation gaps over **250 ms**. Clock-controlled regressions and a native full-page/video fixture verify speech admission, short/noise rejection, minimum utterance/endpoint rules and Stop/restart cleanup. The fixture used native microphone input, MediaRecorder and WebAudio with deterministic local STT/chat/TTS replies: the old controlled slow-cadence run admitted no final STT, while the three-module candidate admitted one. [Controlled reproduction](verification/vad-cadence-4158cd2.json). ACK hints also distinguish disabled, idle, queued and generating states without assuming missing clips mean generation is active.

All **nine hosted checks at `4158cd2` passed with zero annotations**, verified through Code Review at **15:14 UTC on October 23**. [Earlier hosted proof](verification/github-4158cd2.json). The four bundled actions use Node 24 `@v7` releases, while project tests remain on Node 22. Earlier [nine-success `5ef3c9d`](verification/github-5ef3c9d.json) and [nine-success `7725eb6`](verification/github-7725eb6.json) retain their own identities. Later `a6a0465` had eight successes and a floor timeout during FFmpeg installation before pytest; `40fb682` removes that floor install. The hosted floor at `2a51d26` was observed successful at 14:51 UTC, but its full browser outcome was not confirmed before the head changed. Historical billing failures remain separate.

The separate [actual audiovisual `4158cd2` rehearsal](verification/meta-av-4158cd2.json) completed a typed seed and generated-microphone question through real local Whisper, authenticated Meta and local Kokoro. One native browser capture records the screen and actual tab audio together; its MP4 keeps original timing, with three English captions. The voice panel matched 246 input / 88 output / 62 reasoning / 0 cached tokens. All 19 browser checks, readiness, Stop and cleanup passed with no page errors. Live-to-native-ended was 47.710 seconds with instrumentation, not a benchmark. Task testing through this observation totals 43 calls / estimated $0.005781132 of $3. [Watch and hear the demo](https://rgoza1996.github.io/Korina-Agent/Demo.html); physical headset/human checks remain separate. The earlier silent-video/separate-PCM observation retains its own 41-call scope.

The earlier complete local archive **`a27789c`** remains recorded as **1418 Python tests / 95% coverage**, **1417 plus one skip** in each other Python tier, **444 Node / 86 Chromium cases**, mypy over 67 modules and **30 matching installed assets**. [Its versioned summary](verification/a27789c.json) retains its original source identity.

The complete snapshot includes Meta settings/config→next-request, response/agent budget failure recovery, independent credential isolation and truthful UI labels, zero cooldown persistence, credentialed discovery intent and explicit JSON media-type admission. The latter preserves multipart redirects and bodyless actions across the supported dependency range. The follow-up also holds new full-final partial work during endpoint silence and rechecks Whisper ownership after cache/inference-lock waits, with active native work draining safely. Independent optional Meta effort, explicit-blank drafts, saved-agent snapshots and late-activation merge recovery are also covered. Empty HTTP 200 completions now remain transient across all three STT paths and active probes; two-turn recovery preserves the cache and existing ownership guards. The Last response usage panel also preserves the original request snapshot, zero/null counts and latest-dispatch ownership, with Clear/Stop and speech/history separation. Provider replies in these automated checks are fixtures. [Current evidence](verification.md#current-archived-verification) distinguishes each revision and its outcomes. Earlier actual CPU speech/model observations retain their original source identity; the separate [authenticated Meta rehearsal](verification.md#authenticated-meta-effort-rehearsal-at-bc7c8c5) retains its own input, usage and audio boundaries.

The archive also covers unused-state cleanup, mixed-case GGUF admission, default-channel publication, projector preflight, atomic unit/config persistence, first-launch seed errors and the 95-second channel polling ceiling. Earlier `00422de` passed eight non-browser phases but only 85/86 Chromium cases; the test-only `0afe433` timing correction is included in the complete `f6e655f` pass. [Detailed failure boundaries and checkpoint history](verification.md#current-archived-verification).

All of this work is pre-event preparation. The actual kickoff SHA and final submission artifacts remain pending.

## HTTP smoke without changing state

Start Korina, then run:

```bash
python scripts/check_stack.py --base http://127.0.0.1:8001
```

This reads the UI, stylesheet, JS entrypoint, capabilities, health, and readiness. It never changes configuration, resets sessions, switches providers, or sends prompts/audio. Exit 0 means every check passed. `--allow-degraded` accepts unavailable model dependencies while checking the app/asset layer; it is not a successful voice rehearsal.

The gate uses only Python’s standard library, caps each response at 2 MiB, and validates readiness flags against their check map and HTTP status. Degraded mode does not relax failed frontend or audio-limit checks. Use the canonical stack URL: redirects are rejected. Its 15-second socket timeout measures inactivity, while the self-hosted workflow supplies a four-minute gate limit inside its five-minute job budget.

The manually dispatched **Live voice-stack smoke** workflow uses a self-hosted runner labelled `korina-demo` and publishes its JSON report. This runner needs [Actions Runner 2.327.1 or newer](https://github.com/actions/runner/releases/tag/v2.327.1) to execute the Node 24 actions. Configure that runner where it can reach the demo stack; ordinary CI does not assume one exists. GitHub enables this manual workflow once its file reaches the default branch. Before that, invoke the same read-only script locally.

## Actual speech-runtime evidence

[Observed verification](verification.md) records real CPU Whisper buffered/partial/SSE inference, real Kokoro buffered/SSE synthesis, and a complete browser turn with pinned local SmolLM2 response/agent inference, exact package/model versions, timings, and limits. The Whisper runtime job also decodes a real WAV without downloading model weights, catching dependency API breaks such as PyAV 19.

## Rehearse with actual services

Follow [the hackathon guide](hackathon.md). Verify the selected LLM, Whisper/audio model, Kokoro voice, microphone permission, first-turn model loading, a second conversational turn, interruption, permission answer, provider failure, and recovery. Record model IDs, devices, endpoints, and observed timing in issue #23.

`tests/regression_smoke.py` is retired. Its compatibility entrypoint prints replacement commands and exits 2, including with the old `--no-chat --no-transcribe` flags; `--help` exits 0. It performs no requests or runtime operations. Use `scripts/check_stack.py` for read-only monitoring, the deterministic suites above for regression checks, and the hackathon guide for an actual model and hardware rehearsal. Historical plans describe the old mutating suite and do not prescribe current commands.

Passing mocked tests does not establish model quality, microphone behavior, GPU compatibility, or end-to-end latency. Those require the rehearsal above.
