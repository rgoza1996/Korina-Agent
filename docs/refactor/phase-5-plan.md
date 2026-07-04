# Phase 5 — Process Supervision Unification Plan

> **For Hermes:** Use `subagent-driven-development` to implement this plan task-by-task. Use a fresh subagent for each task and verify against the live runtime after every operational change.

**Goal:** Standardize Korina-owned long-running processes on one supervisor so start/stop/restart behavior is reliable, auditable, and boot-friendly.

**Architecture:** Phase 5 standardizes on `systemd --user` because roggoz already uses it for `korina-voice-lab.service` and `llama-server.service`. Tracked unit files live under `deploy/systemd/`; `Korina/install-services.sh` installs them into `~/.config/systemd/user/`; `Korina/start.sh` and `Korina/stop.sh` become compatibility wrappers around `systemctl --user`.

**Tech Stack:** Bash, `systemd --user`, FastAPI/Uvicorn service on `:8001`, Kokoro streaming TTS on `:8880`, existing provider-manager systemd integration for llama.cpp.

**Target branch:** `beta`

**Runtime/source split:** Source checkout is `/home/roggoz/Korina-Agent`; live runtime is `/home/roggoz/Korina`. Editing source does **not** update runtime until files are synced. Phase 5 changes under `Korina/` must be copied to `/home/roggoz/Korina/` before live wrapper tests.

**Current evidence baseline (2026-06-23):**

- `korina-voice-lab.service` already exists and is enabled under `systemd --user`, but it currently `Wants=llama-server.service` and is not tracked in the repo.
- Kokoro streaming TTS is running as a direct Python process on `:8880`; the existing disabled `kokoro-tts.service` points at old `miniconda3/envs/xttsenv` and `kokoro-server.py` paths and is **not** the current Korina Kokoro server.
- `Korina/start.sh` and `Korina/stop.sh` are stale: both reference `/home/roggoz/korina_voice_lab.py`, but the live entrypoint is `/home/roggoz/Korina/korina_voice_lab.py`.
- `llama-server.service` remains provider-managed by `korina/services/provider_manager.py`; Phase 5 must not make the UI always start a heavyweight local model server.

**Pre-execution audit gate:** A post-Phase-4 audit found Phase 4 follow-up issues in the audio-probe path. They are not process-supervision blockers, but the executor should report them before starting Phase 5 code execution so the user can decide whether to patch Phase 4 first.

---

## Phase 5.1 — Supervisor decision and scope lock

### Task 5.1.1 — Record the supervisor decision

**Objective:** Explicitly document that Phase 5 uses `systemd --user` and does not introduce s6/nohup/process-manager alternatives.

**Files:**

- Modify: `docs/refactor/PROGRESS.md` (after implementation verification)
- Modify: `README.md`

**Decision text:**

```markdown
Phase 5 standardizes Korina-owned services on `systemd --user`.

In scope:
- Korina Voice Lab / FastAPI on `:8001`
- Kokoro streaming TTS on `:8880`
- compatibility wrappers: `Korina/start.sh`, `Korina/stop.sh`

Out of scope:
- replacing LM Studio GUI launch behavior
- changing Ollama behavior
- making Korina Voice Lab require/want `llama-server.service`
- changing provider activation semantics in `korina/services/provider_manager.py`
- introducing s6 or another process supervisor
```

**Verification:**

```bash
cd /home/roggoz/Korina-Agent
grep -n "systemd --user\|llama-server" README.md docs/refactor/PROGRESS.md
```

**Commit:** This decision is committed with the README/docs task after the unit files exist.

---

## Phase 5.2 — Tracked systemd units

### Task 5.2.1 — Add tracked unit directory

**Objective:** Create a repo-owned home for user service units.

**Files:**

- Create: `deploy/systemd/`

**Step:**

```bash
cd /home/roggoz/Korina-Agent
mkdir -p deploy/systemd
```

**Verification:**

```bash
test -d deploy/systemd && echo "deploy/systemd exists"
```

---

### Task 5.2.2 — Add Korina Voice Lab user unit

**Objective:** Track the FastAPI/UI service unit in the repo.

**Files:**

- Create: `deploy/systemd/korina-voice-lab.service`

**Content:**

```ini
[Unit]
Description=Korina Voice Lab
After=network-online.target kokoro-streaming-server.service
Wants=network-online.target kokoro-streaming-server.service

[Service]
Type=simple
WorkingDirectory=%h/Korina
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/kokoro-env4/bin/python %h/Korina/korina_voice_lab.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

**Why no `llama-server.service` dependency:** Provider activation already manages llama.cpp. Starting the UI should not automatically load a heavyweight local model server.

**Verification:**

```bash
systemd-analyze --user verify deploy/systemd/korina-voice-lab.service
```

Expected: no errors.

**Commit:** included with Task 5.2.4.

---

### Task 5.2.3 — Add Kokoro streaming TTS user unit

**Objective:** Put the current Kokoro streaming server under tracked `systemd --user` control.

**Files:**

- Create: `deploy/systemd/kokoro-streaming-server.service`

**Content:**

```ini
[Unit]
Description=Kokoro Streaming TTS Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/kokoro-env4/bin/python %h/kokoro-streaming-server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

**Important:** Do **not** reuse the existing disabled `kokoro-tts.service`. It points at old paths (`%h/miniconda3/envs/xttsenv/bin/python`, `%h/kokoro-server.py`) and is not the current Korina Kokoro server.

**Verification:**

```bash
systemd-analyze --user verify deploy/systemd/kokoro-streaming-server.service
```

Expected: no errors.

---

### Task 5.2.4 — Commit tracked unit files

**Objective:** Land the unit-file source of truth before touching runtime service state.

**Files:**

- Add: `deploy/systemd/korina-voice-lab.service`
- Add: `deploy/systemd/kokoro-streaming-server.service`

**Commands:**

```bash
cd /home/roggoz/Korina-Agent
git add deploy/systemd/korina-voice-lab.service deploy/systemd/kokoro-streaming-server.service
git commit -m "chore: add systemd user units for Korina services"
git push origin beta
```

**Verification:**

```bash
git status --short
git ls-remote origin beta | cut -f1
git rev-parse HEAD
```

Expected: clean tree; remote `beta` SHA equals local `HEAD`.

---

## Phase 5.3 — Installer helper

### Task 5.3.1 — Add `Korina/install-services.sh`

**Objective:** Provide an idempotent installer for tracked user units.

**Files:**

- Create: `Korina/install-services.sh`

**Content:**

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$REPO_ROOT/deploy/systemd"
UNIT_DST="$HOME/.config/systemd/user"

require_unit() {
  local file="$1"
  if [[ ! -f "$UNIT_SRC/$file" ]]; then
    echo "ERROR: missing $UNIT_SRC/$file" >&2
    echo "Run this script from the source checkout, e.g.:" >&2
    echo "  cd /home/roggoz/Korina-Agent && ./Korina/install-services.sh" >&2
    exit 1
  fi
}

require_unit korina-voice-lab.service
require_unit kokoro-streaming-server.service
mkdir -p "$UNIT_DST"

install -m 0644 "$UNIT_SRC/korina-voice-lab.service" "$UNIT_DST/korina-voice-lab.service"
install -m 0644 "$UNIT_SRC/kokoro-streaming-server.service" "$UNIT_DST/kokoro-streaming-server.service"

systemctl --user daemon-reload
systemctl --user enable korina-voice-lab.service
systemctl --user enable kokoro-streaming-server.service

echo "Installed Korina user services:"
echo "  $UNIT_DST/korina-voice-lab.service"
echo "  $UNIT_DST/kokoro-streaming-server.service"
echo
echo "Start/restart:"
echo "  systemctl --user restart kokoro-streaming-server.service korina-voice-lab.service"
echo
echo "Status:"
echo "  systemctl --user status kokoro-streaming-server.service korina-voice-lab.service --no-pager -l"
echo
echo "Logs:"
echo "  journalctl --user -u korina-voice-lab.service -f"
echo "  journalctl --user -u kokoro-streaming-server.service -f"

if [[ "${1:-}" == "--start" ]]; then
  systemctl --user restart kokoro-streaming-server.service
  systemctl --user restart korina-voice-lab.service
fi
```

**Make executable:**

```bash
chmod +x Korina/install-services.sh
```

**Verification:**

```bash
bash -n Korina/install-services.sh
./Korina/install-services.sh --help 2>/dev/null || true
```

Note: script intentionally has no `--help` handler in the first version; `bash -n` is the syntax gate. Do not run installation until Task 5.6.

**Commit:** included with Task 5.3.2.

---

### Task 5.3.2 — Commit installer helper

**Objective:** Land the installer before changing lifecycle wrappers.

**Commands:**

```bash
cd /home/roggoz/Korina-Agent
git add Korina/install-services.sh
git commit -m "chore: add Korina systemd service installer"
git push origin beta
```

**Verification:** clean tree and remote matches `HEAD`.

---

## Phase 5.4 — Lifecycle compatibility wrappers

### Task 5.4.1 — Update `Korina/start.sh`

**Objective:** Replace direct `nohup` spawning with `systemctl --user start` while keeping the familiar wrapper command.

**Files:**

- Modify: `Korina/start.sh`

**Content:**

```bash
#!/usr/bin/env bash
set -euo pipefail

require_unit() {
  local unit="$1"
  if ! systemctl --user cat "$unit" >/dev/null 2>&1; then
    echo "ERROR: $unit is not installed." >&2
    echo "Install tracked units from the source checkout:" >&2
    echo "  cd /home/roggoz/Korina-Agent && ./Korina/install-services.sh" >&2
    exit 1
  fi
}

is_listening() {
  local port="$1"
  ss -ltn "sport = :$port" 2>/dev/null | grep -q ":$port"
}

require_unit kokoro-streaming-server.service
require_unit korina-voice-lab.service

systemctl --user start kokoro-streaming-server.service
systemctl --user start korina-voice-lab.service

systemctl --user --no-pager -l status kokoro-streaming-server.service korina-voice-lab.service || true

if is_listening 8880; then
  echo "Kokoro TTS is listening on :8880"
else
  echo "WARNING: Kokoro TTS is not listening on :8880" >&2
fi

if is_listening 8001; then
  echo "Korina Voice Lab is listening on :8001"
else
  echo "WARNING: Korina Voice Lab is not listening on :8001" >&2
fi

if is_listening 1234; then
  echo "LM Studio is listening on :1234"
else
  echo "WARNING: LM Studio is not listening on :1234. Chat relay will fail until LM Studio or another configured LLM endpoint is running."
fi

echo "Korina page: http://$(hostname):8001/"
```

**Verification:**

```bash
bash -n Korina/start.sh
```

**Commit:** included with Task 5.4.3.

---

### Task 5.4.2 — Update `Korina/stop.sh`

**Objective:** Replace stale path-specific `pgrep`/`kill` logic with `systemctl --user stop`.

**Files:**

- Modify: `Korina/stop.sh`

**Content:**

```bash
#!/usr/bin/env bash
set -euo pipefail

stop_unit() {
  local unit="$1"
  if systemctl --user cat "$unit" >/dev/null 2>&1; then
    systemctl --user stop "$unit" || true
  else
    echo "$unit is not installed; skipping."
  fi
}

stop_unit korina-voice-lab.service
stop_unit kokoro-streaming-server.service

echo "Remaining relevant listeners:"
ss -ltnp | grep -E ':8001|:8880|:1234' || true
```

**Do not stop `llama-server.service` here.** The provider manager owns local LLM provider lifecycle. Stopping the UI/TTS wrapper should not implicitly tear down the selected provider.

**Verification:**

```bash
bash -n Korina/stop.sh
```

---

### Task 5.4.3 — Commit lifecycle wrappers

**Commands:**

```bash
cd /home/roggoz/Korina-Agent
git add Korina/start.sh Korina/stop.sh
git commit -m "chore: route Korina lifecycle scripts through systemd"
git push origin beta
```

**Verification:** clean tree and remote matches `HEAD`.

---

## Phase 5.5 — Documentation

### Task 5.5.1 — Update README lifecycle docs

**Objective:** Document the new service installation, start/stop, status, and log flow.

**Files:**

- Modify: `README.md`

**Add or update section:**

```markdown
## Process supervision

Korina-owned long-running services are managed with `systemd --user`:

| Unit | Port | Purpose |
|---|---:|---|
| `korina-voice-lab.service` | 8001 | FastAPI app + browser UI |
| `kokoro-streaming-server.service` | 8880 | Kokoro streaming TTS |

Install/update user units from the source checkout:

```bash
cd /home/roggoz/Korina-Agent
./Korina/install-services.sh
```

Start/restart:

```bash
systemctl --user restart kokoro-streaming-server.service korina-voice-lab.service
```

Compatibility wrappers:

```bash
./Korina/start.sh
./Korina/stop.sh
```

Status and logs:

```bash
systemctl --user status kokoro-streaming-server.service korina-voice-lab.service --no-pager -l
journalctl --user -u korina-voice-lab.service -f
journalctl --user -u kokoro-streaming-server.service -f
```

Boot behavior requires user lingering:

```bash
loginctl show-user "$USER" -p Linger
```

If `Linger=no`, a privileged user can enable boot startup with:

```bash
sudo loginctl enable-linger "$USER"
```
```

**Verification:**

```bash
grep -n "Process supervision\|kokoro-streaming-server.service\|journalctl" README.md
```

---

### Task 5.5.2 — Commit documentation

**Commands:**

```bash
cd /home/roggoz/Korina-Agent
git add README.md
git commit -m "docs: document Korina systemd user service flow"
git push origin beta
```

**Verification:** clean tree and remote matches `HEAD`.

---

## Phase 5.6 — Runtime installation and verification

### Task 5.6.1 — Install units on roggoz

**Objective:** Apply tracked units to the user systemd directory.

**Commands:**

```bash
cd /home/roggoz/Korina-Agent
./Korina/install-services.sh
```

**Verification:**

```bash
systemctl --user daemon-reload
systemctl --user cat korina-voice-lab.service
systemctl --user cat kokoro-streaming-server.service
systemctl --user is-enabled korina-voice-lab.service
systemctl --user is-enabled kokoro-streaming-server.service
```

Expected: both units exist; both are enabled.

---

### Task 5.6.2 — Sync runtime wrappers

**Objective:** Ensure `/home/roggoz/Korina/start.sh` and `/home/roggoz/Korina/stop.sh` match source.

**Commands:**

```bash
rsync -av /home/roggoz/Korina-Agent/Korina/start.sh /home/roggoz/Korina/start.sh
rsync -av /home/roggoz/Korina-Agent/Korina/stop.sh /home/roggoz/Korina/stop.sh
chmod +x /home/roggoz/Korina/start.sh /home/roggoz/Korina/stop.sh
```

**Verification:**

```bash
cmp -s /home/roggoz/Korina-Agent/Korina/start.sh /home/roggoz/Korina/start.sh && echo start-sync-ok
cmp -s /home/roggoz/Korina-Agent/Korina/stop.sh /home/roggoz/Korina/stop.sh && echo stop-sync-ok
```

---

### Task 5.6.3 — Stop stale/manual processes carefully

**Objective:** Clear any non-systemd Kokoro process so the new unit can bind `:8880`.

**Commands:**

```bash
systemctl --user stop korina-voice-lab.service || true

# Exact-match only: do not kill broad python processes.
pids=$(pgrep -f '^/home/roggoz/kokoro-env4/bin/python /home/roggoz/kokoro-streaming-server.py$' || true)
if [[ -n "$pids" ]]; then
  kill $pids
fi

for _ in {1..20}; do
  if ! ss -ltnp | grep -Eq ':8001|:8880'; then
    break
  fi
  sleep 0.25
done

ss -ltnp | grep -E ':8001|:8880|:1234' || true
```

**Expected:** `:8001` and `:8880` are clear before restart. `:1234` may or may not be listening; Phase 5 does not own it.

---

### Task 5.6.4 — Start unified services

**Commands:**

```bash
systemctl --user restart kokoro-streaming-server.service
systemctl --user restart korina-voice-lab.service
systemctl --user status kokoro-streaming-server.service korina-voice-lab.service --no-pager -l
ss -ltnp | grep -E ':8001|:8880'
```

**Expected:**

- `:8001` is served by `/home/roggoz/kokoro-env4/bin/python /home/roggoz/Korina/korina_voice_lab.py`
- `:8880` is served by `/home/roggoz/kokoro-env4/bin/python /home/roggoz/kokoro-streaming-server.py`
- no restart loop in `systemctl --user status`

---

### Task 5.6.5 — Health and frontend static smoke

**Commands:**

```bash
curl -fsS http://127.0.0.1:8001/api/health >/tmp/korina-health.json
curl -fsS http://127.0.0.1:8880/health >/tmp/kokoro-health.json
curl -fsS http://127.0.0.1:8001/ >/tmp/korina-index.html
curl -fsS http://127.0.0.1:8001/styles.css >/tmp/korina-styles.css
curl -fsS http://127.0.0.1:8001/js/app.js >/tmp/korina-app.js
curl -fsS http://127.0.0.1:8001/js/providers-ui.js >/tmp/korina-providers-ui.js
python3 - <<'PY'
import json
print(json.load(open('/tmp/korina-health.json')).get('ok'))
print(json.load(open('/tmp/kokoro-health.json')).get('status'))
PY
```

**Expected:** Korina health prints `True`; Kokoro health prints `ok` (or the current Kokoro service’s healthy status string); all static asset curls return HTTP 200.

---

### Task 5.6.6 — Wrapper lifecycle test

**Objective:** Prove the compatibility wrappers control the new units.

**Commands:**

```bash
cd /home/roggoz/Korina
./stop.sh
ss -ltnp | grep -E ':8001|:8880' || true

./start.sh
ss -ltnp | grep -E ':8001|:8880'
curl -fsS http://127.0.0.1:8001/api/health >/tmp/korina-health-after-wrapper.json
curl -fsS http://127.0.0.1:8880/health >/tmp/kokoro-health-after-wrapper.json
```

**Expected:** `stop.sh` stops both units; `start.sh` starts both units; both health endpoints recover.

---

### Task 5.6.7 — Regression smoke

**Objective:** Confirm process-supervision changes did not break app behavior.

**Commands:**

```bash
cd /home/roggoz/Korina-Agent
python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe
```

**Expected:** all non-chat/non-transcribe regression checks pass.

**Optional full regression:** only run this if the selected LLM endpoint is known to be reachable, because full regression may activate providers and make real chat/transcribe calls.

```bash
python3 tests/regression_smoke.py --base http://127.0.0.1:8001
```

---

### Task 5.6.8 — Journal verification

**Commands:**

```bash
journalctl --user -u korina-voice-lab.service --since "10 minutes ago" --no-pager | tail -80
journalctl --user -u kokoro-streaming-server.service --since "10 minutes ago" --no-pager | tail -80
```

**Check for:**

- no restart loops
- no missing file errors
- no stale `/home/roggoz/korina_voice_lab.py` path
- Uvicorn started on `0.0.0.0:8001`
- Kokoro started on `0.0.0.0:8880`

---

## Phase 5.7 — Progress close-out

### Task 5.7.1 — Update PROGRESS.md

**Objective:** Mark Phase 5 complete only after live runtime verification passes.

**Files:**

- Modify: `docs/refactor/PROGRESS.md`

**Replace Phase 5 stub with:**

```markdown
## Phase 5 — Process supervision unification

Phase 5 result: Korina Voice Lab and Kokoro streaming TTS are both managed by `systemd --user`. `Korina/start.sh` and `Korina/stop.sh` are compatibility wrappers around `systemctl --user`. Runtime services were installed from tracked units under `deploy/systemd/` and verified on roggoz.

- [✓] 5.1 supervisor decision — standardized on `systemd --user`; llama.cpp remains managed by provider activation; LM Studio/Ollama behavior unchanged.
- [✓] 5.2 unit files — added `deploy/systemd/korina-voice-lab.service`, `deploy/systemd/kokoro-streaming-server.service`, and `Korina/install-services.sh`; updated lifecycle scripts and README.
- [✓] 5.3 verify — installed units on roggoz, stopped old/manual processes, restarted units, verified `:8001` and `:8880`, `/api/health`, Kokoro `/health`, frontend static assets, lifecycle wrappers, and regression smoke.
```

**Commit:**

```bash
cd /home/roggoz/Korina-Agent
git add docs/refactor/PROGRESS.md
git commit -m "docs: mark Phase 5 process supervision complete"
git push origin beta
```

---

## Required final report

When Phase 5 execution completes, report:

1. final `beta` HEAD SHA and `git ls-remote origin beta` SHA
2. installed unit paths under `~/.config/systemd/user/`
3. `systemctl --user is-enabled` and `is-active` for both services
4. listener evidence for `:8001` and `:8880`
5. health endpoint output summary
6. wrapper lifecycle test result
7. regression smoke command and pass count
8. whether user lingering is enabled (`loginctl show-user roggoz -p Linger`)

---

## Pitfalls / stop conditions

- **Do not use broad process kills.** Only stop exact known Korina/Kokoro processes or systemd units.
- **Do not make `korina-voice-lab.service` want/require `llama-server.service` unless the user explicitly approves.** Provider activation owns local LLM lifecycle.
- **Do not treat source edits as live.** Sync runtime wrappers before testing `/home/roggoz/Korina/start.sh` and `/home/roggoz/Korina/stop.sh`.
- **Do not overwrite runtime `config.json`.** Phase 5 should not touch runtime config.
- **Do not reuse old `kokoro-tts.service` without explicitly migrating it.** It points to old paths and a different server script.
- **If full regression fails only because the configured chat endpoint is down, do not claim Phase 5 broke chat.** Re-run the supervision-focused smoke with `--no-chat --no-transcribe`, and report the endpoint availability separately.
