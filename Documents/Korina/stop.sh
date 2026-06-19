#!/usr/bin/env bash
set -euo pipefail

PY="/home/roggoz/kokoro-env4/bin/python"
KORINA_APP="/home/roggoz/korina_voice_lab.py"
KOKORO_APP="/home/roggoz/kokoro-streaming-server.py"

kill_exact_app() {
  local name="$1" app="$2"
  local pids
  pids=$(pgrep -f "^$PY $app$" || true)
  if [[ -z "$pids" ]]; then
    echo "$name is not running."
    return 0
  fi
  echo "Stopping $name: $pids"
  kill $pids 2>/dev/null || true
  for _ in {1..20}; do
    local remaining
    remaining=$(pgrep -f "^$PY $app$" || true)
    if [[ -z "$remaining" ]]; then
      echo "$name stopped."
      return 0
    fi
    sleep 0.25
  done
  echo "$name still running; sending SIGKILL."
  kill -9 $(pgrep -f "^$PY $app$" || true) 2>/dev/null || true
}

kill_exact_app "Korina Voice Lab" "$KORINA_APP"
kill_exact_app "Kokoro TTS" "$KOKORO_APP"

echo "Remaining relevant listeners:"
ss -ltnp | grep -E ':8001|:8880|:1234' || true
