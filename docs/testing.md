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

On 2026-10-23, immutable **`4158cd2` passed all four repeated frontend/build phases**: **466 Node tests**, expanded Ruff/ESLint and mypy over **67 modules**, source/wheel builds with **30 matching installed assets**, and **86/86 Chromium cases in 178.337 seconds**. Its managed browser server shut down gracefully. [The current incremental record](verification/4158cd2.json) retains the exact source for each phase; it is not a single-revision nine-phase rerun.

All five backend Python phases retain **`c430012`**: **1431 tests / 95% coverage** on Python 3.12.3 and **1430 tests plus one optional Whisper-decoder skip** on clean Python 3.10.20, 3.11.15, 3.13.14 and minimum-dependency Python 3.10. Python code, Python tests and dependencies are unchanged through `4158cd2`; Python 3.12 exercised the actual decoder. The separate CLI-free `b20c9bf` floor result above remains **1423 passed / eight skipped**.

The earlier `c430012` archive passed all eight non-browser phases, including its then-current **444 Node tests**, but only **84/86 Chromium cases**. Two existing fixtures still expected duplicate authenticated provider requests after discovery began sharing identical requests. `41cb62c` corrects those expectations and checks both role results; all three focused native cases passed. The complete browser repeat at **`5ef3c9d` passed 86/86 in 180.965 seconds** and shut down its managed server. Product code, Python/Node suites and bundled assets are byte-identical between `c430012` and `5ef3c9d`; the later commits change only that browser fixture, action tags and runner documentation. [That incremental record](verification/5ef3c9d.json) preserves each phase's actual source. The earlier `e5dbb05` Python matrix had one stale Meta request-count assertion, corrected in `749a223`; it is not a complete passing checkpoint.

The current Live gate uses **120 ms speech / 150 ms quiet** confirmation rather than rendered-frame counts, expiring unfinished candidates across observation gaps over **250 ms**. Clock-controlled regressions and a native full-page/video fixture verify speech admission, short/noise rejection, minimum utterance/endpoint rules and Stop/restart cleanup. The fixture used native microphone input, MediaRecorder and WebAudio with deterministic local STT/chat/TTS replies: the old controlled slow-cadence run admitted no final STT, while the three-module candidate admitted one. [Controlled reproduction](verification/vad-cadence-4158cd2.json). ACK hints also distinguish disabled, idle, queued and generating states without assuming missing clips mean generation is active.

All **nine hosted checks at `4158cd2` passed with zero annotations**, verified through Code Review at **15:14 UTC on October 23**. [Current hosted proof](verification/github-4158cd2.json). The four bundled actions use Node 24 `@v7` releases, while project tests remain on Node 22. Earlier [nine-success `5ef3c9d`](verification/github-5ef3c9d.json) and [nine-success `7725eb6`](verification/github-7725eb6.json) retain their own identities. Later `a6a0465` had eight successes and a floor timeout during FFmpeg installation before pytest; `40fb682` removes that floor install. The hosted floor at `2a51d26` was observed successful at 14:51 UTC, but its full browser outcome was not confirmed before the head changed. Historical billing failures remain separate.

A separate **actual `4158cd2` recording** also completed the typed Cedar/Friday-ten seed and generated-microphone question through base.en CPU/int8, authenticated Contributor with minimal effort/2048 tokens, Kokoro CPU and native playback. The correct reply retained both facts; voice usage reported 113 cached tokens. Readiness, Stop and shutdown passed with no page errors. Live-to-native-ended was 33.644 seconds with CPU/video instrumentation, not a latency benchmark. [Allowlisted actual evidence](verification/meta-recording-4158cd2.json) and [public demo](https://rgoza1996.github.io/Korina-Agent/Demo.html) keep the silent video separate from actual PCM and a gap-preserving derived replay; no AV synchronization or physical-audio claim is made. Task testing now totals **41 calls / estimated $0.005700232**, including the preserved failed `5ef3c9d` recording and all prior attempts.

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
