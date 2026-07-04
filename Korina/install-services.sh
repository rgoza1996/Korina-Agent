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
