#!/usr/bin/env bash
set -euo pipefail

KORINA_ROOT="/home/roggoz/Documents/Korina"
PY="/home/roggoz/kokoro-env4/bin/python"
KORINA_APP="/home/roggoz/korina_voice_lab.py"
KOKORO_APP="/home/roggoz/kokoro-streaming-server.py"
LOG_DIR="$KORINA_ROOT/logs"
KORINA_LOG="$LOG_DIR/korina-voice-lab.log"
KOKORO_LOG="$LOG_DIR/kokoro-streaming-server.log"
mkdir -p "$LOG_DIR"

is_listening() {
  local port="$1"
  ss -ltn "sport = :$port" 2>/dev/null | grep -q ":$port"
}

pid_for_exact_app() {
  local app="$1"
  pgrep -f "^$PY $app$" || true
}

start_app() {
  local name="$1" port="$2" app="$3" log="$4"
  if is_listening "$port"; then
    echo "$name already listening on :$port"
    return 0
  fi
  if [[ ! -f "$app" ]]; then
    echo "ERROR: missing $app" >&2
    return 1
  fi
  echo "Starting $name on :$port..."
  nohup "$PY" "$app" >> "$log" 2>&1 &
  local pid=$!
  for _ in {1..30}; do
    if is_listening "$port"; then
      echo "$name started on :$port (pid $pid, log $log)"
      return 0
    fi
    sleep 0.5
  done
  echo "ERROR: $name did not start on :$port. Last log lines:" >&2
  tail -40 "$log" >&2 || true
  return 1
}

start_app "Kokoro TTS" 8880 "$KOKORO_APP" "$KOKORO_LOG"
start_app "Korina Voice Lab" 8001 "$KORINA_APP" "$KORINA_LOG"

if is_listening 1234; then
  echo "LM Studio is listening on :1234"
else
  echo "WARNING: LM Studio is not listening on :1234. Chat relay will fail until LM Studio is running."
fi

echo "Korina page: http://$(hostname):8001/"
