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
