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

# Detect a no-op start: if both target ports are already listening, this is
# likely a restart-after-start invocation. Print a one-liner so the operator
# knows to use `systemctl --user restart` for actual process refreshes.
no_op_start=0
if is_listening 8880 && is_listening 8001; then
  no_op_start=1
fi

systemctl --user start kokoro-streaming-server.service
systemctl --user start korina-voice-lab.service

# systemctl --user start for Type=simple is non-blocking and returns
# before the service has bound its socket. Wait briefly for both ports
# to come up so the is_listening checks below do not false-warn.
wait_for_listening() {
  local port="$1" max_iters="${2:-40}"
  for _ in $(seq 1 "$max_iters"); do
    if is_listening "$port"; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

wait_for_listening 8880 || true
wait_for_listening 8001 || true

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

if [[ "$no_op_start" -eq 1 ]]; then
  echo "Note: start.sh is for first-launch; for restarts use \`systemctl --user restart korina-voice-lab.service\`."
fi

echo "Korina page: http://$(hostname):8001/"
