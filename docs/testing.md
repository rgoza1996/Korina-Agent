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

CI runs Python 3.10–3.13, coverage artifacts, JavaScript lint/tests on Node 22, Python lint/type checks, real Chromium smoke, and source/wheel builds. An isolated wheel check verifies that installed UI assets exist outside the source checkout. Tests are hosted on Linux; process management and user services are Linux-specific. Foreground API operation does not require systemd.

## Current verification checkpoint

On 2026-10-22, the core refactor at `22b401c` passed 347 Python tests with 85% coverage, 38 Node tests, 10 real Chromium smoke tests, mypy, Ruff/ESLint, source/wheel builds, and an isolated wheel install. GitHub Actions jobs did not start because GitHub reported an account payment/spending-limit problem. This is local evidence, not a passing hosted run. Exact current results are tracked in [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23).

## HTTP smoke without changing state

Start Korina, then run:

```bash
python scripts/check_stack.py --base http://127.0.0.1:8001
```

This reads the UI, stylesheet, JS entrypoint, capabilities, health, and readiness. It never changes configuration, resets sessions, switches providers, or sends prompts/audio. Exit 0 means every check passed. `--allow-degraded` accepts unavailable model dependencies while checking the app/asset layer; it is not a successful voice rehearsal.

The manually dispatched **Live voice-stack smoke** workflow uses a self-hosted runner labelled `korina-demo` and publishes its JSON report. Configure that runner where it can reach the demo stack; ordinary CI does not assume one exists.

## Rehearse with actual services

Follow [the hackathon guide](hackathon.md). Verify the selected LLM, Whisper/audio model, Kokoro voice, microphone permission, first-turn model loading, a second conversational turn, interruption, permission answer, provider failure, and recovery. Record model IDs, devices, endpoints, and observed timing in issue #23.

`tests/regression_smoke.py` is the historical integration suite. It includes **mutating** checks: session reset, ACK rebuild, provider activation, and a temporary configuration round trip, even with `--no-chat --no-transcribe`. Run it only against a disposable or deliberately selected local stack. The new read-only smoke gate is the default for monitoring and CI.

Passing mocked tests does not establish model quality, microphone behavior, GPU compatibility, or end-to-end latency. Those require the rehearsal above.
