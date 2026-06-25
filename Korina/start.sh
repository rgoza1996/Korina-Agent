#!/usr/bin/env bash
# Korina Voice Lab — config-driven startup
# Reads Korina/config.json to determine which backend services are needed
# and starts the corresponding systemd --user units (if installed).
#
# Usage: ./start.sh [--no-wait] [--dry-run]
#
# Defaults are baked in if config.json is missing — the script will copy
# config/config.example.json into place as a one-time bootstrap.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KORINA_APP_DIR="${KORINA_APP_DIR:-$SCRIPT_DIR}"
CONFIG="$KORINA_APP_DIR/config.json"
EXAMPLE_CONFIG="$KORINA_APP_DIR/config/config.example.json"

NO_WAIT=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --no-wait) NO_WAIT=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "Usage: $0 [--no-wait] [--dry-run]"
      echo "  --no-wait   skip readiness waits (faster, less safe)"
      echo "  --dry-run   show what would be done, do not modify state"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# ── Config bootstrap ──────────────────────────────────────────────────
if [[ ! -f "$CONFIG" ]]; then
  echo "config.json missing at $CONFIG" >&2
  if [[ -f "$EXAMPLE_CONFIG" ]]; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "[dry-run] would bootstrap from $EXAMPLE_CONFIG"
    else
      echo "Bootstrapping from $EXAMPLE_CONFIG"
      cp "$EXAMPLE_CONFIG" "$CONFIG"
    fi
  else
    echo "ERROR: no $EXAMPLE_CONFIG either — cannot derive defaults" >&2
    exit 1
  fi
fi

# ── Config reader (python is already a hard dep of Korina) ────────────
# Parses JSON via python3 (always available — Korina itself requires it).
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
TTS_BASE_URL="$(cfg_get tts_base_url)"
LLM_PROVIDER="$(cfg_get llm_provider)"
LLM_BASE_URL="$(cfg_get llm_base_url)"
STT_BACKEND="$(cfg_get stt_backend)"
STT_LLM_BASE_URL="$(cfg_get stt_llm_base_url)"
STT_MODEL="$(cfg_get stt_model)"
AGENT_ENABLED="$(cfg_get agent_enabled)"
AGENT_BASE_URL="$(cfg_get agent_base_url)"

# Default fallback for empty values
: "${TTS_PORT:=8880}"
: "${TTS_PROVIDER:=kokoro}"
: "${LLM_PROVIDER:=llama.cpp}"
: "${STT_BACKEND:=faster-whisper}"
: "${AGENT_ENABLED:=off}"

# ── Helpers ───────────────────────────────────────────────────────────
is_listening() {
  ss -ltn "sport = :$1" 2>/dev/null | grep -q ":$1"
}

port_from_url() {
  # Extract the port from http://host:port/... — defaults to 80 for http / 443 for https
  python3 - "$1" <<'PY'
import sys, urllib.parse
u = urllib.parse.urlparse(sys.argv[1])
if u.port: print(u.port)
elif u.scheme == "https": print(443)
else: print(80)
PY
}

unit_installed() {
  systemctl --user cat "$1" >/dev/null 2>&1
}

start_unit() {
  local unit="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] systemctl --user start $unit"
    return 0
  fi
  if ! unit_installed "$unit"; then
    echo "  SKIP: $unit is not installed"
    return 1
  fi
  systemctl --user start "$unit"
  return 0
}

wait_for_listening() {
  local port="$1" label="$2" max_iters="${3:-40}"
  if [[ "$NO_WAIT" -eq 1 ]]; then return 0; fi
  for _ in $(seq 1 "$max_iters"); do
    if is_listening "$port"; then
      echo "  OK: $label listening on :$port"
      return 0
    fi
    sleep 0.25
  done
  echo "  TIMEOUT: $label did not bind :$port within $((max_iters/4))s" >&2
  return 1
}

# ── Decide which servers we need from config ──────────────────────────
echo "Reading config: $CONFIG"
echo
echo "Detected needs:"
echo "  tts_provider=$TTS_PROVIDER  port=$TTS_PORT"
echo "  llm_provider=$LLM_PROVIDER  url=$LLM_BASE_URL"
echo "  stt_backend=$STT_BACKEND    model=$STT_MODEL"
echo "  agent_enabled=$AGENT_ENABLED url=$AGENT_BASE_URL"
echo

needed_units=()   # units to start
needed_ports=()   # ports we expect to come up
needed_labels=()  # human labels for each port

# Kokoro TTS (local provider, on its own port)
if [[ "$TTS_PROVIDER" == "kokoro" ]]; then
  needed_units+=("kokoro-streaming-server.service")
  needed_ports+=("$TTS_PORT")
  needed_labels+=("Kokoro TTS")
fi

# LLM via llama.cpp (one server handles both chat and STT-via-llm)
llama_url="$LLM_BASE_URL"
stt_uses_llm=0
[[ "$STT_BACKEND" == "llm" ]] && [[ "$STT_LLM_BASE_URL" == "$LLM_BASE_URL" ]] && stt_uses_llm=1
# If STT-LLM points to a different URL, treat as a separate llama-server
separate_stt_url=""
if [[ "$STT_BACKEND" == "llm" ]]; then
  if [[ "$STT_LLM_BASE_URL" != "$LLM_BASE_URL" ]]; then
    separate_stt_url="$STT_LLM_BASE_URL"
  fi
fi

# Agent LLM (only if enabled and points somewhere separate)
agent_separate=0
if [[ "$AGENT_ENABLED" == "on" ]]; then
  if [[ -n "$AGENT_BASE_URL" ]] && [[ "$AGENT_BASE_URL" != "$LLM_BASE_URL" ]] && [[ "$AGENT_BASE_URL" != "$STT_LLM_BASE_URL" ]]; then
    agent_separate=1
  fi
fi

if [[ "$LLM_PROVIDER" == "llama.cpp" || "$LLM_PROVIDER" == "openai-compatible" ]]; then
  llama_port="$(port_from_url "$llama_url")"
  # Only register llama-server if the URL is local
  if [[ "$llama_url" == *"127.0.0.1"* || "$llama_url" == *"localhost"* ]]; then
    needed_units+=("llama-server.service")
    needed_ports+=("$llama_port")
    needed_labels+=("llama.cpp (response LLM)")
  fi
fi

if [[ -n "$separate_stt_url" ]]; then
  stt_port="$(port_from_url "$separate_stt_url")"
  if [[ "$separate_stt_url" == *"127.0.0.1"* || "$separate_stt_url" == *"localhost"* ]]; then
    needed_units+=("llama-server.service")  # same unit, but track expected port separately
    needed_ports+=("$stt_port")
    needed_labels+=("llama.cpp (STT-via-LLM)")
  fi
fi

if [[ "$agent_separate" -eq 1 ]]; then
  agent_port="$(port_from_url "$AGENT_BASE_URL")"
  if [[ "$AGENT_BASE_URL" == *"127.0.0.1"* || "$AGENT_BASE_URL" == *"localhost"* ]]; then
    needed_units+=("agent-llm.service")
    needed_ports+=("$agent_port")
    needed_labels+=("Agent LLM")
  fi
fi

# Whisper (local faster-whisper via stt_model)
if [[ "$STT_BACKEND" == "faster-whisper" ]]; then
  # faster-whisper runs in-process inside korina_voice_lab.py — no separate unit
  echo "  Note: STT backend is 'faster-whisper' — runs in-process; no extra unit."
fi

# Always: Korina itself
needed_units+=("korina-voice-lab.service")
needed_ports+=("8001")
needed_labels+=("Korina Voice Lab")

# ── De-duplicate unit list while preserving order ─────────────────────
declare -A seen_unit
unique_units=()
for u in "${needed_units[@]}"; do
  if [[ -z "${seen_unit[$u]:-}" ]]; then
    seen_unit[$u]=1
    unique_units+=("$u")
  fi
done

# ── Start ─────────────────────────────────────────────────────────────
echo "Plan: start ${#unique_units[@]} unit(s):"
for u in "${unique_units[@]}"; do
  if unit_installed "$u"; then
    echo "  - $u (installed)"
  else
    echo "  - $u (NOT INSTALLED — will warn)"
  fi
done
echo

no_op_start=1
for p in "${needed_ports[@]}"; do
  if ! is_listening "$p"; then no_op_start=0; break; fi
done

for u in "${unique_units[@]}"; do
  echo "Starting $u ..."
  start_unit "$u" || true
done

# ── Wait for ports ────────────────────────────────────────────────────
echo
echo "Waiting for readiness..."
for i in "${!needed_ports[@]}"; do
  wait_for_listening "${needed_ports[$i]}" "${needed_labels[$i]}" || true
done

# ── Final status ──────────────────────────────────────────────────────
echo
echo "────────────────────────────────────"
echo "Status:"
for i in "${!needed_ports[@]}"; do
  if is_listening "${needed_ports[$i]}"; then
    echo "  ✓ ${needed_labels[$i]} :${needed_ports[$i]}"
  else
    echo "  ✗ ${needed_labels[$i]} :${needed_ports[$i]} NOT LISTENING"
  fi
done

# Cross-check: if config says LLM is llama.cpp on :8080 and :1234 is up
# but :8080 isn't, that's a config-vs-reality mismatch.
llama_expected_port="$(port_from_url "$llama_url")"
if [[ "$LLM_PROVIDER" == "llama.cpp" || "$LLM_PROVIDER" == "openai-compatible" ]]; then
  if [[ "$llama_url" == *"127.0.0.1"* || "$llama_url" == *"localhost"* ]]; then
    if ! is_listening "$llama_expected_port"; then
      echo
      echo "WARNING: config wants LLM at $llama_url (:$llama_expected_port) but no listener there."
      if is_listening 1234; then
        echo "  → LM Studio is up on :1234. Update llm_base_url to http://127.0.0.1:1234/v1"
        echo "    or point llama-server at :$llama_expected_port."
      fi
    fi
  fi
fi

if [[ "$no_op_start" -eq 1 ]]; then
  echo
  echo "Note: start.sh is for first-launch; for restarts use \\`systemctl --user restart korina-voice-lab.service\\`."
fi

echo
echo "Korina page: http://$(hostname):8001/"