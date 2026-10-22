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

CI runs Python 3.10–3.13, coverage artifacts, JavaScript lint/tests on Node 22, Python lint/type checks, real Chromium smoke, and source/wheel builds. An isolated wheel check serves every installed JavaScript module, compares the bundled assets with source, verifies protocol discovery, and checks validated settings writes in disposable state outside the package. Hosted jobs pin Ubuntu 24.04 for a stable native-runtime base; process management and user services are Linux-specific. Foreground API operation does not require systemd.

To repeat the installed-wheel check, install the built wheel in a separate environment, change to a directory outside the checkout, then run that environment’s Python with the absolute path to `scripts/check_installed.py --source-assets /absolute/path/to/Korina-Agent/Korina`. The script refuses a source installation and never uses your runtime configuration.

## Current verification checkpoint

On 2026-10-22, immutable checkpoint `d14a371` passed 932 Python tests with 93% coverage on Python 3.12.3, 313 executable Node tests, 57 real Chromium tests, expanded Ruff/ESLint, mypy over 65 modules, isolated source/wheel builds, and an installed-wheel check of all 29 matching assets. Clean Python 3.10.20, 3.11.15, and 3.13.14 each passed 931 tests with only the optional Whisper decoder skipped; Python 3.12 exercised it. GitHub Actions jobs did not start because of the account payment/spending-limit problem. See [observed verification](verification.md) and [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23) for exact boundaries and later work.

## HTTP smoke without changing state

Start Korina, then run:

```bash
python scripts/check_stack.py --base http://127.0.0.1:8001
```

This reads the UI, stylesheet, JS entrypoint, capabilities, health, and readiness. It never changes configuration, resets sessions, switches providers, or sends prompts/audio. Exit 0 means every check passed. `--allow-degraded` accepts unavailable model dependencies while checking the app/asset layer; it is not a successful voice rehearsal.

The gate uses only Python’s standard library, caps each response at 2 MiB, and validates readiness flags against their check map and HTTP status. Degraded mode does not relax failed frontend or audio-limit checks. Use the canonical stack URL: redirects are rejected. Its 15-second socket timeout measures inactivity, while the self-hosted workflow supplies a four-minute gate limit inside its five-minute job budget.

The manually dispatched **Live voice-stack smoke** workflow uses a self-hosted runner labelled `korina-demo` and publishes its JSON report. Configure that runner where it can reach the demo stack; ordinary CI does not assume one exists.

## Actual speech-runtime evidence

[Observed verification](verification.md) records real CPU Whisper buffered/partial/SSE inference, real Kokoro buffered/SSE synthesis, and a complete browser turn with pinned local SmolLM2 response/agent inference, exact package/model versions, timings, and limits. The Whisper runtime job also decodes a real WAV without downloading model weights, catching dependency API breaks such as PyAV 19.

## Rehearse with actual services

Follow [the hackathon guide](hackathon.md). Verify the selected LLM, Whisper/audio model, Kokoro voice, microphone permission, first-turn model loading, a second conversational turn, interruption, permission answer, provider failure, and recovery. Record model IDs, devices, endpoints, and observed timing in issue #23.

`tests/regression_smoke.py` is retired. Its compatibility entrypoint prints replacement commands and exits 2, including with the old `--no-chat --no-transcribe` flags; `--help` exits 0. It performs no requests or runtime operations. Use `scripts/check_stack.py` for read-only monitoring, the deterministic suites above for regression checks, and the hackathon guide for an actual model and hardware rehearsal. Historical plans describe the old mutating suite and do not prescribe current commands.

Passing mocked tests does not establish model quality, microphone behavior, GPU compatibility, or end-to-end latency. Those require the rehearsal above.
