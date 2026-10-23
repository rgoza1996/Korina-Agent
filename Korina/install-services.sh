#!/usr/bin/env bash
set -euo pipefail

usage() { echo "Usage: $0 [--start]"; }
START_SERVICES=0
if [[ "$#" -gt 1 ]]; then
  echo "ERROR: expected at most one argument." >&2
  usage >&2
  exit 2
fi
if [[ "$#" -eq 1 ]]; then
  case "$1" in
    --start) START_SERVICES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/Korina/service-common.sh"
UNIT_SRC="$REPO_ROOT/deploy/systemd"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PYTHON_BIN="$(korina_select_python KORINA_PYTHON "${KORINA_PYTHON:-}" "${VIRTUAL_ENV:-$REPO_ROOT/.venv}/bin/python")"
TTS_PYTHON_BIN="$(korina_select_python KORINA_TTS_PYTHON "${KORINA_TTS_PYTHON:-}" "$PYTHON_BIN")"
KORINA_APP_DIR="$(korina_normalize_app_dir "$PYTHON_BIN" "${KORINA_APP_DIR:-$REPO_ROOT/Korina}")"
export KORINA_APP_DIR

require_unit() {
  local file="$1"
  if [[ ! -f "$UNIT_SRC/$file" ]]; then
    echo "ERROR: missing $UNIT_SRC/$file" >&2
    echo "Run this script from the source checkout, e.g.:" >&2
    echo "  cd /path/to/Korina-Agent && ./Korina/install-services.sh" >&2
    exit 1
  fi
}

require_unit korina-voice-lab.service
require_unit kokoro-streaming-server.service
mkdir -p "$UNIT_DST"

# Render real checkout/interpreter paths; templates are not tied to a home layout.
"$PYTHON_BIN" - "$REPO_ROOT" "$UNIT_SRC" "$UNIT_DST" "$TTS_PYTHON_BIN" <<'PYTHON'
import os
import shutil
import sys
from pathlib import Path

repo, source, destination = map(Path, sys.argv[1:4])
launcher = shutil.which('env', path=os.defpath)
if not launcher:
    raise RuntimeError('Installing Linux user services requires the env executable')

def quote(value, command=False):
    if any(ord(char) < 32 for char in value):
        raise ValueError("Service paths cannot contain control characters")
    if command:
        value = value.replace('$', '$$')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

values = {
    "REPO_ROOT": str(repo),
    "APP_DIR_ENV": "KORINA_APP_DIR=" + os.environ.get("KORINA_APP_DIR", str(repo / "Korina")),
    "PYTHON": sys.executable,
    "TTS_PYTHON": sys.argv[4],
    "TTS_SCRIPT": str(repo / "kokoro-streaming-server.py"),
    "ENV": launcher,
}
for name in ("korina-voice-lab.service", "kokoro-streaming-server.service"):
    text = (source / name).read_text()
    for key, value in values.items():
        if key == "REPO_ROOT":
            replacement = value.replace("%", "%%")
        else:
            replacement = quote(value, command=key in ("PYTHON", "TTS_PYTHON", "TTS_SCRIPT"))
        text = text.replace("@" + key + "@", replacement)
    target = destination / name
    target.write_text(text)
    target.chmod(0o644)
PYTHON

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

if [[ "$START_SERVICES" -eq 1 ]]; then
  systemctl --user restart kokoro-streaming-server.service
  systemctl --user restart korina-voice-lab.service
fi
