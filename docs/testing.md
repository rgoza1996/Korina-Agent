# Testing Korina Agent

Korina has two test layers:

1. **pytest unit/API/static tests** — run anywhere, including GitHub Actions. These tests use a temporary `KORINA_APP_DIR`, mock startup ACK generation, and do not require llama.cpp, LM Studio, Kokoro, Whisper model weights, GGUF files, or `systemd --user`.
2. **live roggoz regression smoke** — run only on roggoz against the running service at `http://127.0.0.1:8001`.

## Local pytest

From the source checkout:

```bash
cd /home/roggoz/Korina-Agent
python3 -m venv /tmp/korina-phase6-venv
/tmp/korina-phase6-venv/bin/python -m pip install --upgrade pip
/tmp/korina-phase6-venv/bin/python -m pip install -e '.[test]'
/tmp/korina-phase6-venv/bin/python -m pytest -q
```

On GitHub Actions the workflow uses the same editable install shape:

```bash
python -m pip install -e '.[test]'
pytest -q
```

## Live roggoz regression

The live runtime is a deploy copy at `/home/roggoz/Korina`; the source checkout is `/home/roggoz/Korina-Agent`. Before live regression, sync source to runtime and restart the service:

```bash
rsync -a --delete /home/roggoz/Korina-Agent/korina/ /home/roggoz/Korina/korina/
rsync -a --delete /home/roggoz/Korina-Agent/Korina/js/ /home/roggoz/Korina/js/
rsync -a /home/roggoz/Korina-Agent/Korina/index.html /home/roggoz/Korina/index.html
rsync -a /home/roggoz/Korina-Agent/Korina/styles.css /home/roggoz/Korina/styles.css
rsync -a /home/roggoz/Korina-Agent/tests/regression_smoke.py /home/roggoz/Korina/tests/regression_smoke.py
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
cd /home/roggoz/Korina-Agent
python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe
```

Only run the full smoke (without `--no-chat --no-transcribe`) when the selected local LLM provider is reachable and expected to answer:

```bash
python3 tests/regression_smoke.py --base http://127.0.0.1:8001
```

If the no-chat/no-transcribe smoke passes but full smoke fails because llama.cpp is intentionally stopped, report that as a decoupled service state rather than a pytest/CI failure.
