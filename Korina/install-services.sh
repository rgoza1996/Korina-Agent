#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$REPO_ROOT/deploy/systemd"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PYTHON_BIN="${KORINA_PYTHON:-${VIRTUAL_ENV:-$REPO_ROOT/.venv}/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

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
"$PYTHON_BIN" - "$REPO_ROOT" "$UNIT_SRC" "$UNIT_DST" <<'PYTHON'
import os
import sys
from pathlib import Path

repo, source, destination = map(Path, sys.argv[1:])

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
    "TTS_PYTHON": os.environ.get("KORINA_TTS_PYTHON", sys.executable),
    "TTS_SCRIPT": str(repo / "kokoro-streaming-server.py"),
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

if [[ "${1:-}" == "--start" ]]; then
  systemctl --user restart kokoro-streaming-server.service
  systemctl --user restart korina-voice-lab.service
fi
