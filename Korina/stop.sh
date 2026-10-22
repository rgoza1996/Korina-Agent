#!/usr/bin/env bash
# Korina Voice Lab — complete local shutdown.
#
# Stops Korina and every local server Korina can run:
# - korina-voice-lab.service
# - kokoro-streaming-server.service
# - llama-server.service
# - LM Studio server/GUI helper processes
# - Ollama service / `ollama serve`

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KORINA_APP_DIR="${KORINA_APP_DIR:-$SCRIPT_DIR}"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${KORINA_PYTHON:-${VIRTUAL_ENV:-$REPO_ROOT/.venv}/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "Usage: $0 [--dry-run]"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  [dry-run]'; printf ' %q' "$@"; echo
  else
    "$@" || true
  fi
}

stop_unit() {
  local unit="$1"
  if systemctl --user cat "$unit" >/dev/null 2>&1; then
    echo "Stopping $unit..."
    run systemctl --user stop "$unit"
  else
    echo "Skipping $unit (not installed)."
  fi
}

echo "Stopping Korina app and local backends..."
stop_unit korina-voice-lab.service

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  [dry-run] would run provider_manager stop functions"
else
  export KORINA_APP_DIR
  (cd "$REPO_ROOT" && "$PYTHON_BIN" - <<'PY') || true
from korina.services.provider_manager import (
    stop_llama_server, stop_lmstudio_server, stop_lmstudio, stop_ollama,
)
for fn in (stop_llama_server, stop_lmstudio_server, stop_lmstudio, stop_ollama):
    try:
        fn()
    except Exception as e:
        print(f"WARN: {fn.__name__} failed: {e}")
PY
fi

stop_unit kokoro-streaming-server.service
stop_unit korina-streaming-server.service
stop_unit llama-server.service
stop_unit ollama.service

# Provider helpers match only current-user executables and actual listeners.
# Services started manually should be stopped through their owning terminal.

echo
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run complete."
else
  sleep 1
  echo "Remaining relevant listeners:"
  ss -ltnp 2>/dev/null | grep -E ':(8001|8880|8080|1234|11434)' || echo "  (none)"
fi
