#!/usr/bin/env bash
# Korina Voice Lab — config-driven startup.
#
# Starts Korina plus the local backend declared in config.json.
# - llama.cpp  -> writes/restarts llama-server.service via provider_manager
# - lmstudio   -> starts LM Studio server/model via provider_manager
# - ollama     -> starts ollama via provider_manager
# - openai-compatible / anthropic -> no local LLM server is started
# Kokoro is started when tts_provider=kokoro.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KORINA_APP_DIR="${KORINA_APP_DIR:-$SCRIPT_DIR}"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/service-common.sh"

NO_WAIT=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --no-wait) NO_WAIT=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      echo "Usage: $0 [--no-wait] [--dry-run]"
      exit 0
      ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

PYTHON_BIN="$(korina_select_python KORINA_PYTHON "${KORINA_PYTHON:-}" "${VIRTUAL_ENV:-$REPO_ROOT/.venv}/bin/python")"
KORINA_APP_DIR="$(korina_normalize_app_dir "$PYTHON_BIN" "$KORINA_APP_DIR")"
CONFIG="$KORINA_APP_DIR/config.json"

if [[ ! -f "$CONFIG" ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    CONFIG="$REPO_ROOT/Korina/config/config.example.json"
  else
    (cd "$REPO_ROOT" && KORINA_APP_DIR="$KORINA_APP_DIR" "$PYTHON_BIN" -c 'from korina.config import load_config; load_config()')
  fi
fi

cfg_json() {
  "$PYTHON_BIN" - "$CONFIG" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    c=json.load(f)
keys = [
    'llm_provider','lm_model','llm_base_url',
    'stt_backend','stt_llm_provider','stt_llm_model','stt_llm_base_url',
    'tts_provider','tts_port','tts_base_url','tts_model',
]
print(json.dumps({k:c.get(k) for k in keys}, sort_keys=True))
PY
}

cfg_get() {
  "$PYTHON_BIN" - "$CONFIG" "$1" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    c=json.load(f)
v = c.get(sys.argv[2], '')
print('' if v is None else v)
PY
}

is_listening() { ss -ltn "sport = :$1" 2>/dev/null | grep -q ":$1"; }
wait_port() {
  local port="$1" label="$2" max_iters="${3:-120}"
  [[ "$NO_WAIT" -eq 1 || "$DRY_RUN" -eq 1 ]] && return 0
  for _ in $(seq 1 "$max_iters"); do
    if is_listening "$port"; then
      echo "  OK: $label listening on :$port"
      return 0
    fi
    sleep 0.5
  done
  echo "  TIMEOUT: $label did not listen on :$port" >&2
  return 1
}

start_unit() {
  local unit="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "  [dry-run] systemctl --user start $unit"
    return 0
  fi
  if ! systemctl --user start "$unit"; then
    echo "ERROR: could not start $unit; inspect systemctl --user status $unit" >&2
    return 1
  fi
}

require_unit() {
  local unit="$1"
  if ! systemctl --user cat "$unit" >/dev/null 2>&1; then
    echo "ERROR: required user service $unit is unavailable." >&2
    echo "Run ./Korina/install-services.sh first, or use the foreground commands in README.md." >&2
    return 1
  fi
}

LLM_PROVIDER="$(cfg_get llm_provider)"; LLM_PROVIDER="${LLM_PROVIDER:-llama.cpp}"
LM_MODEL="$(cfg_get lm_model)"
TTS_PROVIDER="$(cfg_get tts_provider)"; TTS_PROVIDER="${TTS_PROVIDER:-kokoro}"
# The bundled units use their entrypoints' defaults. Shell bind overrides and
# client endpoint settings do not rewrite an already installed service.
TTS_PORT=8880

echo "Korina config: $CONFIG"
cfg_json
echo
echo "Startup plan:"
echo "  - Korina app: korina-voice-lab.service (:8001)"
echo "  - TTS: ${TTS_PROVIDER}"
echo "  - LLM provider: ${LLM_PROVIDER}"
echo "  Readiness uses bundled ports 8001/8880; customized unit ports require --no-wait."

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "  [dry-run] would start configured provider via korina.services.provider_manager"
else
  # Check every required unit before starting anything or activating a provider.
  require_unit korina-voice-lab.service
  if [[ "$TTS_PROVIDER" == "kokoro" ]]; then require_unit kokoro-streaming-server.service; fi
  export KORINA_APP_DIR
  if [[ "$TTS_PROVIDER" == "kokoro" ]]; then
    echo "Starting Kokoro TTS..."
    start_unit kokoro-streaming-server.service
  else
    echo "TTS provider is $TTS_PROVIDER; no local Kokoro start requested."
  fi

  case "$LLM_PROVIDER" in
    llama.cpp|lmstudio|ollama)
      echo "Activating local LLM provider $LLM_PROVIDER..."
      (cd "$REPO_ROOT" && "$PYTHON_BIN" - "$LLM_PROVIDER" "$LM_MODEL" <<'PY')
import json, sys
from korina.config import load_config
from korina.services.provider_manager import activate_llm_provider
provider = sys.argv[1]
model = sys.argv[2]
config = load_config()
result = activate_llm_provider(provider, config, model=model)
print(json.dumps(result, indent=2, sort_keys=True))
PY
      ;;
    *)
      echo "Provider $LLM_PROVIDER is endpoint-backed; not starting a local LLM server."
      ;;
  esac

  echo "Starting Korina app..."
  start_unit korina-voice-lab.service
fi

echo
if [[ "$TTS_PROVIDER" == "kokoro" ]]; then wait_port "$TTS_PORT" "Kokoro TTS"; fi
case "$LLM_PROVIDER" in
  llama.cpp) wait_port 8080 "llama.cpp" ;;
  lmstudio) wait_port 1234 "LM Studio" ;;
  ollama) wait_port 11434 "Ollama" ;;
esac
wait_port 8001 "Korina"

echo
echo "Relevant listeners:"
ss -ltnp 2>/dev/null | grep -E ':(8001|8880|8080|1234|11434)' || echo "  (none)"
echo
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run complete; no services were started."
elif [[ "$NO_WAIT" -eq 1 ]]; then
  echo "Start commands completed; readiness was not checked."
else
  echo "Required TCP listeners are available; use scripts/check_stack.py for HTTP/model readiness."
fi
echo "Bundled Korina page: http://127.0.0.1:8001/ (custom units may use another address)"
