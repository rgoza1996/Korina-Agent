#!/usr/bin/env bash
# Korina Voice Lab — config-driven shutdown
# Reads Korina/config.json to determine which backend services to stop.
#
# Usage: ./stop.sh [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${KORINA_APP_DIR:-$SCRIPT_DIR}/config.json"

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

# Use defaults if config is missing — just stop the core app and Kokoro
# (mirrors the historical behavior of the original stop.sh).
TTS_PROVIDER="kokoro"
TTS_PORT="8880"
LLM_PROVIDER=""
STT_BACKEND=""
AGENT_ENABLED="off"

if [[ -f "$CONFIG" ]]; then
  cfg_get() {
    python3 -c "
import json, sys
with open('$CONFIG') as f: c = json.load(f)
v = c.get('$1', '')
print(v if v is not None else '')
" 2>/dev/null || true
  }
  TTS_PROVIDER="$(cfg_get tts_provider)"
  TTS_PORT="$(cfg_get tts_port)"
  LLM_PROVIDER="$(cfg_get llm_provider)"
  STT_BACKEND="$(cfg_get stt_backend)"
  AGENT_ENABLED="$(cfg_get agent_enabled)"
  LLM_BASE_URL="$(cfg_get llm_base_url)"
  AGENT_BASE_URL="$(cfg_get agent_base_url)"
  STT_LLM_BASE_URL="$(cfg_get stt_llm_base_url)"

  : "${TTS_PORT:=8880}"
  : "${TTS_PROVIDER:=kokoro}"
  : "${LLM_PROVIDER:=llama.cpp}"
  : "${STT_BACKEND:=faster-whisper}"
  : "${AGENT_ENABLED:=off}"
fi

port_from_url() {
  python3 - "$1" <<'PY'
import sys, urllib.parse
u = urllib.parse.urlparse(sys.argv[1])
if u.port: print(u.port)
elif u.scheme == "https": print(443)
else: print(80)
PY
}

stop_unit() {
  local unit="$1"
  if ! systemctl --user cat "$unit" >/dev/null 2>&1; then
    echo "  $unit is not installed; skipping."
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] systemctl --user stop $unit"
    return 0
  fi
  systemctl --user stop "$unit" || true
}

# Build stop order: app first, then backends (so in-flight requests can finish)
units_to_stop=()

# Always: app
units_to_stop+=("korina-voice-lab.service")

# Kokoro (only if config uses it)
if [[ "$TTS_PROVIDER" == "kokoro" ]]; then
  units_to_stop+=("kokoro-streaming-server.service")
fi

# llama.cpp (only if config points to local llama.cpp and unit is installed)
if [[ "$LLM_PROVIDER" == "llama.cpp" || "$LLM_PROVIDER" == "openai-compatible" ]]; then
  if [[ "$LLM_BASE_URL" == *"127.0.0.1"* || "$LLM_BASE_URL" == *"localhost"* ]]; then
    if systemctl --user cat "llama-server.service" >/dev/null 2>&1; then
      units_to_stop+=("llama-server.service")
    fi
  fi
fi

# Separate agent LLM
if [[ "$AGENT_ENABLED" == "on" ]] && [[ -n "${AGENT_BASE_URL:-}" ]]; then
  if [[ "$AGENT_BASE_URL" == *"127.0.0.1"* || "$AGENT_BASE_URL" == *"localhost"* ]]; then
    if [[ "$AGENT_BASE_URL" != "${LLM_BASE_URL:-}" ]] && [[ "$AGENT_BASE_URL" != "${STT_LLM_BASE_URL:-}" ]]; then
      if systemctl --user cat "agent-llm.service" >/dev/null 2>&1; then
        units_to_stop+=("agent-llm.service")
      fi
    fi
  fi
fi

echo "Stopping ${#units_to_stop[@]} unit(s):"
for u in "${units_to_stop[@]}"; do
  echo "  - $u"
done
echo

# Stop in order, deduplicated
declare -A seen
for u in "${units_to_stop[@]}"; do
  if [[ -z "${seen[$u]:-}" ]]; then
    seen[$u]=1
    stop_unit "$u"
  fi
done

echo
echo "Remaining relevant listeners:"
ss -ltnp 2>/dev/null | grep -E ':8001|:8880|:1234|:8080' || echo "  (none)"