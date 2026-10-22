# Testing Korina

## Deterministic checks

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest --cov=korina --cov-report=term-missing -q
ruff check korina
npm ci --ignore-scripts
npm run lint
npm test
python -m build
npx playwright install chromium
npm run test:browser
```

The Python suite uses a temporary runtime tree and fake provider/model boundaries. It needs no Whisper/Kokoro weights, GPU, external LLM, or working systemd session. JavaScript tests execute native modules under Node and exercise health failure recovery, agent delivery, and pending-report handling. Playwright serves the actual app in Chromium and checks boot, settings persistence/failure, independent providers, and channel selection; ESLint rejects undefined references across the frontend.

CI runs Python 3.10–3.13, coverage artifacts, JavaScript lint/tests on Node 22, Python lint/type checks, real Chromium smoke, and source/wheel builds. An isolated wheel check serves every installed JavaScript module, compares the bundled assets with source, verifies protocol discovery, and checks validated settings writes in disposable state outside the package. Hosted jobs pin Ubuntu 24.04 for a stable native-runtime base; process management and user services are Linux-specific. Foreground API operation does not require systemd.

To repeat the installed-wheel check, install the built wheel and `httpx` in a separate environment, change to a directory outside the checkout, then run that environment’s Python with the absolute path to `scripts/check_installed.py --source-assets /absolute/path/to/Korina-Agent/Korina`. The script refuses a source installation and never uses your runtime configuration.

## Current verification checkpoint

On 2026-10-22, capture checkpoint `8d93cd9` passed 496 Python tests with 87% coverage, 131 Node tests (+17 label assertions), 27 real Chromium tests, mypy, Ruff/ESLint, source/wheel builds, and an isolated wheel install. Clean Python 3.11 and 3.13 each passed 495 tests with only the optional Whisper decoder skipped; Python 3.12 exercised it. The earlier `dd7f990` baseline also passed all 483 tests on Python 3.10. GitHub Actions jobs did not start because GitHub reported an account payment/spending-limit problem. This is local evidence, not a passing hosted run. See [observed verification](verification.md) and [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23) for precise boundaries and later results.

## HTTP smoke without changing state

Start Korina, then run:

```bash
python scripts/check_stack.py --base http://127.0.0.1:8001
```

This reads the UI, stylesheet, JS entrypoint, capabilities, health, and readiness. It never changes configuration, resets sessions, switches providers, or sends prompts/audio. Exit 0 means every check passed. `--allow-degraded` accepts unavailable model dependencies while checking the app/asset layer; it is not a successful voice rehearsal.

The manually dispatched **Live voice-stack smoke** workflow uses a self-hosted runner labelled `korina-demo` and publishes its JSON report. Configure that runner where it can reach the demo stack; ordinary CI does not assume one exists.

## Actual speech-runtime evidence

[Observed verification](verification.md) records real CPU Whisper buffered/partial/SSE inference and real Kokoro buffered/SSE synthesis, exact package/model versions, timings, and limits. The Whisper runtime job also decodes a real WAV without downloading model weights, catching dependency API breaks such as PyAV 19.

## Rehearse with actual services

Follow [the hackathon guide](hackathon.md). Verify the selected LLM, Whisper/audio model, Kokoro voice, microphone permission, first-turn model loading, a second conversational turn, interruption, permission answer, provider failure, and recovery. Record model IDs, devices, endpoints, and observed timing in issue #23.

`tests/regression_smoke.py` is the historical integration suite. It includes **mutating** checks: session reset, ACK rebuild, provider activation, and a temporary configuration round trip, even with `--no-chat --no-transcribe`. Run it only against a disposable or deliberately selected local stack. The new read-only smoke gate is the default for monitoring and CI.

Passing mocked tests does not establish model quality, microphone behavior, GPU compatibility, or end-to-end latency. Those require the rehearsal above.
