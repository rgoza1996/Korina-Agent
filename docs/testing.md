# Testing Korina Agent

Korina has two test layers:

1. **pytest unit/API/static tests** — run anywhere, including GitHub Actions. These tests use a temporary `KORINA_APP_DIR`, mock startup ACK generation, and do not require llama.cpp, LM Studio, Kokoro, Whisper model weights, GGUF files, or `systemd --user`.
2. **live regression smoke** — run against a running local Korina service at `http://127.0.0.1:8001`.


## Current release gate

The first `master` release was promoted after these checks passed:

- `pytest -q` — 101 tests passing
- `tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe` — 49 live smoke checks passing

The no-chat/no-transcribe live smoke is the default release gate because it validates the running service and frontend/API contracts without requiring local LLM or transcription model availability.

## Local pytest

From the source checkout:

```bash
cd /path/to/Korina-Agent
python3 -m venv /tmp/korina-test-venv
/tmp/korina-test-venv/bin/python -m pip install --upgrade pip
/tmp/korina-test-venv/bin/python -m pip install -e '.[test]'
/tmp/korina-test-venv/bin/python -m pytest -q
```

On GitHub Actions the workflow uses the same editable install shape:

```bash
python -m pip install -e '.[test]'
pytest -q
```

## Live regression

Before live regression, sync the source checkout into the runtime directory and restart the service:

```bash
rsync -a --delete /path/to/Korina-Agent/korina/ /path/to/Korina-runtime/korina/
rsync -a --delete /path/to/Korina-Agent/Korina/js/ /path/to/Korina-runtime/js/
rsync -a /path/to/Korina-Agent/Korina/index.html /path/to/Korina-runtime/index.html
rsync -a /path/to/Korina-Agent/Korina/styles.css /path/to/Korina-runtime/styles.css
rsync -a /path/to/Korina-Agent/tests/regression_smoke.py /path/to/Korina-runtime/tests/regression_smoke.py
systemctl --user restart korina-voice-lab.service
```

Then verify the served frontend is the modularized runtime:

```bash
curl -fsS http://127.0.0.1:8001/ | grep -E 'type="module"|js/app\.js'
for path in /js/app.js /js/state.js /js/providers-ui.js /js/capability-filter.js /styles.css; do
  echo -n "$path: "
  curl -fsS -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8001$path"
done
```

Run the live regression from the **source checkout**, not the runtime dir:

```bash
cd /path/to/Korina-Agent
python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe
```

Only run the full smoke (without `--no-chat --no-transcribe`) when the selected local LLM provider is reachable and expected to answer:

```bash
python3 tests/regression_smoke.py --base http://127.0.0.1:8001
```

If the no-chat/no-transcribe smoke passes but full smoke fails because llama.cpp is intentionally stopped, report that as a decoupled service state rather than a pytest/CI failure.
