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
source "$SCRIPT_DIR/service-common.sh"

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

PYTHON_BIN="$(korina_select_python KORINA_PYTHON "${KORINA_PYTHON:-}" "${VIRTUAL_ENV:-$REPO_ROOT/.venv}/bin/python")"
KORINA_APP_DIR="$(korina_normalize_app_dir "$PYTHON_BIN" "$KORINA_APP_DIR")"
FAILURES=()

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  [dry-run]'; printf ' %q' "$@"; echo
  else
    if ! "$@"; then
      printf 'ERROR: stop command failed:' >&2
      printf ' %q' "$@" >&2
      printf '\n' >&2
      FAILURES+=("$*")
    fi
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
stop_unit kokoro-streaming-server.service
stop_unit korina-streaming-server.service
LLAMA_UNIT="$(basename -- "${KORINA_LLAMA_SERVER_USER_UNIT:-llama-server.service}")"
stop_unit "$LLAMA_UNIT"
if [[ "$LLAMA_UNIT" != "llama-server.service" ]]; then stop_unit llama-server.service; fi
stop_unit ollama.service

# Stop owning user units before process fallback so supervised workers cannot
# be restarted merely because their process received a termination signal.
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  [dry-run] would run provider_manager stop functions"
else
  export KORINA_APP_DIR
  if (cd "$REPO_ROOT" && "$PYTHON_BIN" - <<'PY')
import sys
from korina.services.provider_manager import (
    stop_lmstudio_server, stop_lmstudio, stop_ollama,
)
failed = False
for name, fn in (
    ('stop_lmstudio_server', stop_lmstudio_server),
    ('stop_lmstudio', stop_lmstudio),
    ('stop_ollama', lambda: stop_ollama(stop_service=False)),
):
    try:
        result = fn()
        for warning in result.warnings:
            print(f'WARN: {name}: {warning}', file=sys.stderr)
        if result.stop_requested:
            print(f'WARN: {name}: process exit was not confirmed for {", ".join(result.stop_requested)}', file=sys.stderr)
        failed |= bool(result.warnings or result.stop_requested)
    except Exception as e:
        failed = True
        print(f'ERROR: {name} failed: {e}', file=sys.stderr)
raise SystemExit(1 if failed else 0)
PY
  then
    :
  else
    FAILURES+=("provider cleanup")
  fi
fi

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

if [[ "${#FAILURES[@]}" -gt 0 ]]; then
  printf 'ERROR: shutdown incomplete; one or more stop operations failed or were not confirmed.\n' >&2
  exit 1
fi
