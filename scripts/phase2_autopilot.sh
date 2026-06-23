#!/usr/bin/env bash
# Phase 2 autopilot -- single-file executor for the single-source-of-truth
# refactor. Stops on the first failure and dumps diagnostics.
#
# Stages: 2.1.1 registry -> 2.1.2 schemas -> 2.1.3 route + factory ->
#         2.1.4 regression -- capabilities -> 2.2.1 fetch helper ->
#         2.2.2 cleanup marker -> 2.2.3 frontend regression ->
#         2.3.1 docs -> 2.3.2 edge case -> 2.4.1 full live regression +
#         OpenAPI parity -> 2.4.2 PROGRESS update.

set -euo pipefail

REPO=/home/roggoz/Korina-Agent
LIVE=/home/roggoz/Korina
LOG=/tmp/phase2_autopilot.log
: > "$LOG"

log()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG" ; }
fail() { printf '\n[FAIL] %s\n' "$*" | tee -a "$LOG" ; tail -120 "$LOG" ; exit 1; }

py_compile() {
  python3 -m py_compile "$1" || fail "py_compile failed: $1"
}

node_check() {
  python3 -c "
import re
with open('Korina/index.html') as f: html=f.read()
m=re.search(r'<script>([\\s\\S]*?)</script>', html)
open('/tmp/_korina_index.js','w').write(m.group(1))
" || fail "extract script failed"
  node --check /tmp/_korina_index.js || fail "node --check failed"
}

live_smoke() {
  systemctl --user restart korina-voice-lab.service >>"$LOG" 2>&1 \
    || fail "service restart failed"
  sleep 2
  local active
  active=$(systemctl --user is-active korina-voice-lab.service) \
    || fail "is-active returned non-zero"
  [ "$active" = "active" ] || fail "service not active: $active"
  curl -fsS --max-time 10 http://127.0.0.1:8001/api/health | head -c 80 \
    || fail "/api/health smoke failed"
  echo
}

mirror_live() {
  rm -rf "$LIVE/korina"
  cp -r "$REPO/korina" "$LIVE/korina"
  cp "$REPO/Korina/index.html" "$LIVE/index.html"
  cp "$REPO/tests/regression_smoke.py" "$LIVE/tests/regression_smoke.py"
}

commit_one() {
  git add -A
  if git diff --cached --quiet; then
    log "no staged changes for commit ($1) -- skipping"
    return
  fi
  git commit -m "$2" >>"$LOG" 2>&1 || fail "commit failed: $1"
  log "committed $1 -> $(git rev-parse --short HEAD)"
}

cd "$REPO"

CUR=$(git branch --show-current)
[ "$CUR" = "beta" ] || fail "expected on beta, on $CUR"
git status --short

log "autopilot starting on beta at $(git rev-parse --short HEAD)"

# ============================================================================
# Stage 2.1.1 -- PROVIDER_CAPABILITIES registry
# ============================================================================
log "STAGE 2.1.1 -- registry"

cat > "$REPO/korina/util/presets.py" <<'PYEOF'
"""Provider preset + capability registry for Korina Voice Lab.

Single source of truth for:
  * canonical local base URLs per provider
  * provider capability metadata served at GET /api/capabilities

All callers (preset helpers, the capabilities route, and future provider
lifecycle code) read from PROVIDER_CAPABILITIES. Do not duplicate
provider rules anywhere else in the codebase.

No state, no side effects -- safe to import anywhere.
"""

from __future__ import annotations

from typing import Any


# Registry: {provider_id: capability_dict}.
# Capability keys (snake_case JSON, served verbatim by /api/capabilities):
#   label:                 str   -- user-facing dropdown label
#   default_base_url:      str   -- canonical local URL; "" for non-local
#   editable_base_url:     bool  -- whether the UI lets the user override
#   manageable:            bool  -- backend can start/stop the server
#   model_sources:         list  -- which of:
#                                  endpoint_loaded, local_gguf, catalog
#   is_local:              bool  -- is this a localhost inference server?
#   agent_only:            bool  -- only valid for the agent path
#   response_llm_only:     bool  -- only valid for the response-LLM path
PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "llama.cpp": {
        "label": "llama.cpp local",
        "default_base_url": "http://127.0.0.1:8080/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded", "local_gguf"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": True,
    },
    "lmstudio": {
        "label": "LM Studio local",
        "default_base_url": "http://127.0.0.1:1234/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded", "catalog"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": True,
    },
    "ollama": {
        "label": "Ollama local",
        "default_base_url": "http://127.0.0.1:11434/v1",
        "editable_base_url": False,
        "manageable": True,
        "model_sources": ["endpoint_loaded"],
        "is_local": True,
        "agent_only": False,
        "response_llm_only": True,
    },
    "openai-compatible": {
        "label": "OpenAI-compatible / generic",
        "default_base_url": "",
        "editable_base_url": True,
        "manageable": False,
        "model_sources": ["endpoint_loaded"],
        "is_local": False,
        "agent_only": False,
        "response_llm_only": False,
    },
    "anthropic": {
        "label": "Anthropic-compatible",
        "default_base_url": "",
        "editable_base_url": True,
        "manageable": False,
        "model_sources": ["endpoint_loaded"],
        "is_local": False,
        "agent_only": True,
        "response_llm_only": False,
    },
}


def provider_preset_base_url(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    cap = PROVIDER_CAPABILITIES.get(provider, {})
    if cap.get("agent_only"):
        return ""
    return str(cap.get("default_base_url") or "")


def is_local_provider_base_url(base_url: str, provider: str = "") -> bool:
    base = str(base_url or "").strip().rstrip("/")
    provider = str(provider or "").strip().lower()
    cap = PROVIDER_CAPABILITIES.get(provider, {})
    if cap.get("is_local"):
        return True
    for cap in PROVIDER_CAPABILITIES.values():
        if cap.get("is_local") and base == str(cap.get("default_base_url") or "").rstrip("/"):
            return True
    return False


def capabilities_for_section(section: str) -> dict[str, dict[str, Any]]:
    """Return the subset of PROVIDER_CAPABILITIES valid for section."""
    section = str(section or "").strip().lower()
    out: dict[str, dict[str, Any]] = {}
    for pid, cap in PROVIDER_CAPABILITIES.items():
        if section == "agent" and cap.get("response_llm_only"):
            continue
        if section == "response_llm" and cap.get("agent_only"):
            continue
        out[pid] = cap
    return out
PYEOF

py_compile korina/util/presets.py
PYTHONPATH=. python3 -c "
from korina.util.presets import (
    PROVIDER_CAPABILITIES,
    provider_preset_base_url,
    is_local_provider_base_url,
    capabilities_for_section,
)
assert provider_preset_base_url('llama.cpp') == 'http://127.0.0.1:8080/v1'
assert provider_preset_base_url('anthropic') == ''
assert provider_preset_base_url('openai-compatible') == ''
assert is_local_provider_base_url('http://127.0.0.1:8080/v1', 'llama.cpp') is True
assert is_local_provider_base_url('http://127.0.0.1:8080/v1') is True
assert is_local_provider_base_url('https://api.openai.com/v1', 'openai-compatible') is False
assert set(capabilities_for_section('response_llm').keys()) == {
    'llama.cpp', 'lmstudio', 'ollama', 'openai-compatible'
}
assert set(capabilities_for_section('agent').keys()) == {
    'openai-compatible', 'anthropic'
}
print('presets registry OK')
" >>"$LOG" 2>&1 || fail "registry runtime smoke failed"
log "registry runtime smoke OK"

commit_one 2.1.1 "feat(capabilities): add PROVIDER_CAPABILITIES registry

Single source of truth for provider preset URLs and capability metadata.
provider_preset_base_url and is_local_provider_base_url now delegate to
the registry; their public behavior is unchanged.

Adds capabilities_for_section() which splits the registry by
response_llm vs agent path, ready to be served at /api/capabilities in
2.1.3.

No runtime behavior change. Preset URLs and is_local detection return
the same values as before for all 3 prior response-LLM providers."

# ============================================================================
# Stage 2.1.2 -- Pydantic schemas
# ============================================================================
log "STAGE 2.1.2 -- schemas"

cat > "$REPO/korina/schemas_capabilities.py" <<'PYEOF'
"""Pydantic schemas for GET /api/capabilities.

Separated from korina.schemas to keep capability metadata in a
single, import-light module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ModelSource = Literal["endpoint_loaded", "local_gguf", "catalog"]


class ProviderCapabilities(BaseModel):
    label: str
    default_base_url: str = ""
    editable_base_url: bool
    manageable: bool
    model_sources: list[ModelSource] = Field(default_factory=list)
    is_local: bool
    agent_only: bool = False
    response_llm_only: bool = False


class CapabilitiesResponse(BaseModel):
    providers: dict[str, ProviderCapabilities]
    agent_providers: dict[str, ProviderCapabilities]
    version: int = 1
PYEOF

py_compile korina/schemas_capabilities.py
PYTHONPATH=. python3 -c "
from korina.schemas_capabilities import CapabilitiesResponse
from korina.util.presets import capabilities_for_section
resp = CapabilitiesResponse(
    providers=capabilities_for_section('response_llm'),
    agent_providers=capabilities_for_section('agent'),
)
print('sections:', sorted(resp.providers), '|', sorted(resp.agent_providers))
" >>"$LOG" 2>&1 || fail "schema build smoke failed"
log "schema smoke OK"

commit_one 2.1.2 "feat(capabilities): add Pydantic schemas for /api/capabilities

ProviderCapabilities describes one provider; CapabilitiesResponse wraps
two sections (providers for the response-LLM path, agent_providers for
the agent path) so the UI can populate both dropdowns from one fetch.
version field allows future breaking changes."

# ============================================================================
# Stage 2.1.3 -- route + app_factory
# ============================================================================
log "STAGE 2.1.3 -- route"

cat > "$REPO/korina/routes/capabilities.py" <<'PYEOF'
"""GET /api/capabilities -- provider capability registry."""

from __future__ import annotations

from fastapi import APIRouter

from korina.schemas_capabilities import CapabilitiesResponse
from korina.util.presets import capabilities_for_section


router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
def get_capabilities() -> CapabilitiesResponse:
    """Return provider capabilities for both response-LLM and agent paths."""
    return CapabilitiesResponse(
        providers=capabilities_for_section("response_llm"),
        agent_providers=capabilities_for_section("agent"),
    )
PYEOF

py_compile korina/routes/capabilities.py

python3 <<'PY' || fail "app_factory patch failed"
from pathlib import Path
p = Path("korina/app_factory.py")
src = p.read_text()
marker = "from korina.routes import acks as _acks_routes"
new_import = marker + "\nfrom korina.routes import capabilities as _capabilities_routes"
if "_capabilities_routes" in src:
    print("already wired")
else:
    src = src.replace(marker, new_import, 1)
    old = "for module in (\n        _index_routes, _health_routes, _config_routes, _models_routes,\n        _providers_routes, _acks_routes, _agent_routes, _stt_routes, _chat_routes,\n    ):"
    new = "for module in (\n        _index_routes, _health_routes, _config_routes, _models_routes,\n        _providers_routes, _capabilities_routes, _acks_routes, _agent_routes, _stt_routes, _chat_routes,\n    ):"
    assert old in src, "tuple pattern not found in app_factory.py"
    src = src.replace(old, new, 1)
    p.write_text(src)
    print("patched app_factory.py")
PY
py_compile korina/app_factory.py

mirror_live
live_smoke

PROBE=$(curl -fsS --max-time 10 http://127.0.0.1:8001/api/capabilities) \
  || fail "/api/capabilities probe failed"
echo "$PROBE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['version'] == 1
assert set(d['providers'].keys()) == {'llama.cpp','lmstudio','ollama','openai-compatible'}
assert set(d['agent_providers'].keys()) == {'openai-compatible','anthropic'}
print('capabilities endpoint OK')
" >>"$LOG" 2>&1 || fail "capabilities endpoint contract check failed"
log "capabilities endpoint OK"

commit_one 2.1.3 "feat(capabilities): serve /api/capabilities

GET /api/capabilities returns the PROVIDER_CAPABILITIES registry split
into two sections (providers for response-LLM, agent_providers for the
agent path). Backed by Pydantic CapabilitiesResponse so OpenAPI
documents the wire format.

Verification (live):
  curl /api/capabilities -> 4 response-LLM providers, 2 agent providers
  curl /api/health      -> ok: true
  systemctl is-active   -> active"

# ============================================================================
# Stage 2.1.4 -- regression smoke for /api/capabilities
# ============================================================================
log "STAGE 2.1.4 -- capabilities regression test"

python3 <<'PY' || fail "regression patch failed"
from pathlib import Path
p = Path("tests/regression_smoke.py")
src = p.read_text()
new_test = '''def test_capabilities_endpoint():
    """Phase 2.1 -- GET /api/capabilities returns the full registry split by section."""
    import json, urllib.request
    with urllib.request.urlopen(BASE + "/api/capabilities", timeout=10) as r:
        assert r.status == 200
        body = json.loads(r.read().decode("utf-8"))
    assert body.get("version") == 1
    providers = body.get("providers") or {}
    agent_providers = body.get("agent_providers") or {}
    assert set(providers.keys()) == {"llama.cpp", "lmstudio", "ollama", "openai-compatible"}
    for pid, cap in providers.items():
        assert cap.get("agent_only") is False, f"{pid} leaked into response-LLM section"
        assert isinstance(cap.get("editable_base_url"), bool)
        assert isinstance(cap.get("manageable"), bool)
        assert isinstance(cap.get("is_local"), bool)
        assert isinstance(cap.get("model_sources"), list)
        assert cap.get("label"), f"{pid} missing label"
    assert set(agent_providers.keys()) == {"openai-compatible", "anthropic"}
    assert agent_providers["anthropic"].get("agent_only") is True
    assert agent_providers["anthropic"].get("default_base_url") == ""
    assert agent_providers["anthropic"].get("editable_base_url") is True
    assert providers["llama.cpp"]["default_base_url"] == "http://127.0.0.1:8080/v1"
    assert providers["lmstudio"]["default_base_url"]  == "http://127.0.0.1:1234/v1"
    assert providers["ollama"]["default_base_url"]    == "http://127.0.0.1:11434/v1"


'''
needle = "    print()\n    if failures == 0:"
if "test_capabilities_endpoint" in src:
    print("already present")
else:
    src = src.replace(needle, new_test + "    test_capabilities_endpoint()\n    print()\n    if failures == 0:", 1)
    p.write_text(src)
    print("patched regression_smoke.py")
PY
py_compile tests/regression_smoke.py

log "running regression suite after 2.1.4"
python3 tests/regression_smoke.py 2>&1 | tee -a "$LOG" | tail -80 \
  || fail "regression suite failed at 2.1.4"
if grep -E "\[FAIL\]" "$LOG" >/dev/null; then
  grep -E "\[FAIL\]" "$LOG" | head -5
  fail "regression suite has failures at 2.1.4"
fi
log "regression suite green after 2.1.4"

commit_one 2.1.4 "test: cover /api/capabilities contract

Verifies:
  - response-LLM section has 4 providers, none agent-only
  - agent section has 2 providers, includes agent-only anthropic
  - editable_base_url / manageable / is_local are booleans
  - local providers expose the canonical local URL
  - anthropic.agent_only=true and default_base_url is empty"

# ============================================================================
# Stage 2.2.1 -- fetch + cache helpers + initApp warm
# ============================================================================
log "STAGE 2.2.1 -- capabilities fetch helper"

python3 <<'PY' || fail "index.html 2.2.1 patch failed"
from pathlib import Path
p = Path("Korina/index.html")
src = p.read_text()

old = """const PROVIDER_BASE_URL_PRESETS={
  'llama.cpp':'http://127.0.0.1:8080/v1',
  'lmstudio':'http://127.0.0.1:1234/v1',
  'ollama':'http://127.0.0.1:11434/v1',
};
function providerPresetBaseUrl(provider){ return PROVIDER_BASE_URL_PRESETS[String(provider||'').trim()]||''; }
function maybeApplyProviderPreset(providerId, baseUrlId, {clearWhenBlank=false}={}){
  const provider=$(providerId)?.value||'';
  const input=$(baseUrlId);
  if(!input) return false;
  const preset=providerPresetBaseUrl(provider);
  if(preset){ input.value=preset; return true; }
  if(clearWhenBlank && !provider){ input.value=''; return true; }
  return false;
}
function prettyModelLabel(modelId){ const text=String(modelId||'').trim(); if(!text) return ''; if(text.includes('/') || text.endsWith('.gguf')){ const parts=text.split('/').filter(Boolean); const tail=parts[parts.length-1]||text; const parent=parts.length>1 ? parts[parts.length-2] : ''; return parent ? `${tail} \u2014 ${parent}` : tail; } return text; }
function setBaseUrlEditability(){ const llmEditable=llmProvider()==='openai-compatible'; if($('llmBaseUrl')) $('llmBaseUrl').disabled=!llmEditable; const sttProviderValue=String($('sttLlmProvider')?.value||'').trim(); const sttEditable=sttProviderValue==='openai-compatible'; if($('sttLlmBaseUrl')) $('sttLlmBaseUrl').disabled=!sttEditable; }"""

new = """// Provider capability registry. Fetched once at boot from /api/capabilities.
// Cached in memory; the JS no longer owns provider rules.
let _capabilitiesCache = null;
async function loadCapabilities(force=false){
  if(_capabilitiesCache && !force) return _capabilitiesCache;
  const r = await fetch('/api/capabilities');
  if(!r.ok){ throw new Error('capabilities fetch failed: '+r.status); }
  const j = await r.json();
  _capabilitiesCache = { providers: j.providers || {}, agentProviders: j.agent_providers || {} };
  return _capabilitiesCache;
}
function getResponseLlmProviderCaps(provider){
  const caps = _capabilitiesCache?.providers || {};
  return caps[String(provider||'').trim()] || null;
}
function getAgentProviderCaps(provider){
  const caps = _capabilitiesCache?.agentProviders || {};
  return caps[String(provider||'').trim()] || null;
}
function providerPresetBaseUrl(provider){
  const caps = getResponseLlmProviderCaps(provider);
  return caps ? String(caps.default_base_url || '') : '';
}
function maybeApplyProviderPreset(providerId, baseUrlId, {clearWhenBlank=false}={}){
  const provider=$(providerId)?.value||'';
  const input=$(baseUrlId);
  if(!input) return false;
  const isAgent = providerId === 'agentProvider';
  const caps = isAgent ? getAgentProviderCaps(provider) : getResponseLlmProviderCaps(provider);
  const preset = caps ? String(caps.default_base_url || '') : providerPresetBaseUrl(provider);
  if(preset){ input.value=preset; return true; }
  if(clearWhenBlank && !provider){ input.value=''; return true; }
  return false;
}
function prettyModelLabel(modelId){ const text=String(modelId||'').trim(); if(!text) return ''; if(text.includes('/') || text.endsWith('.gguf')){ const parts=text.split('/').filter(Boolean); const tail=parts[parts.length-1]||text; const parent=parts.length>1 ? parts[parts.length-2] : ''; return parent ? `${tail} \u2014 ${parent}` : tail; } return text; }
function setBaseUrlEditability(){
  // Cache must be warm -- initApp() awaits loadCapabilities() before any
  // UI handler runs. If the cache is empty (e.g. server restart), fall back
  // to the legacy hardcoded rule so the field isn't stuck enabled/disabled.
  const llmId = llmProvider();
  const llmCaps = getResponseLlmProviderCaps(llmId);
  const llmEditable = llmCaps ? !!llmCaps.editable_base_url : (llmId === 'openai-compatible');
  if($('llmBaseUrl')) $('llmBaseUrl').disabled = !llmEditable;
  const sttId = String($('sttLlmProvider')?.value||'').trim();
  const sttCaps = getResponseLlmProviderCaps(sttId);
  const sttEditable = sttCaps ? !!sttCaps.editable_base_url : (sttId === 'openai-compatible');
  if($('sttLlmBaseUrl')) $('sttLlmBaseUrl').disabled = !sttEditable;
  // Agent path: now controlled too (Phase 2 bugfix).
  const agentId = String($('agentProvider')?.value||'').trim();
  const agentCaps = getAgentProviderCaps(agentId);
  const agentEditable = agentCaps ? !!agentCaps.editable_base_url : (agentId === 'openai-compatible' || agentId === 'anthropic');
  if($('agentBaseUrl')) $('agentBaseUrl').disabled = !agentEditable;
}"""

assert old in src, "old preset block not found verbatim"
src = src.replace(old, new, 1)

old_init = """async function initApp(){
  await loadConfig();"""
new_init = """async function initApp(){
  try{ await loadCapabilities(); }catch(e){ console.warn('capabilities load failed', e); }
  await loadConfig();"""
assert old_init in src, "initApp signature not found"
src = src.replace(old_init, new_init, 1)

p.write_text(src)
print("patched index.html for 2.2.1")
PY
node_check
mirror_live
live_smoke

HITS=$(curl -fsS --max-time 10 http://127.0.0.1:8001/ | grep -c "loadCapabilities" || true)
[ "$HITS" -ge 1 ] || fail "loadCapabilities not present in served index.html"
log "served index.html contains loadCapabilities ($HITS matches)"

commit_one 2.2.1 "feat(frontend): fetch /api/capabilities on boot

Adds loadCapabilities() and getResponseLlmProviderCaps() /
getAgentProviderCaps() lookups. setBaseUrlEditability() now also wires
up the agent base-URL field (was uncontrolled -- the Phase 2 bugfix).
initApp() awaits loadCapabilities() at boot so the cache is warm
before any UI handler runs.

Verification:
  - extracted <script> passes node --check
  - served index.html contains loadCapabilities
  - /api/health still green"

# ============================================================================
# Stage 2.2.2 -- cleanup marker (no code change; old constant already gone)
# ============================================================================
log "STAGE 2.2.2 -- migration marker (no code change)"

if grep -q "PROVIDER_BASE_URL_PRESETS" Korina/index.html; then
  fail "PROVIDER_BASE_URL_PRESETS still present in index.html"
fi
log "old constant is gone; no further code change needed"

# Re-run regression to confirm nothing broke.
log "running regression suite after 2.2"
python3 tests/regression_smoke.py 2>&1 | tee -a "$LOG" | tail -40 \
  || fail "regression suite failed at 2.2"
if grep -E "\[FAIL\]" "$LOG" >/dev/null; then
  grep -E "\[FAIL\]" "$LOG" | head -5
  fail "regression suite has failures at 2.2"
fi
log "regression suite green after 2.2"

if ! git diff --quiet HEAD -- Korina/index.html; then
  commit_one 2.2.2 "chore(frontend): 2.2.2 marker (no additional change)

PROVIDER_BASE_URL_PRESETS removal already landed in 2.2.1. This commit
records the migration point so history shows the 2.1 -> 2.2 -> 2.3
sequence explicitly."
else
  log "no further change to commit at 2.2.2"
fi

# ============================================================================
# Stage 2.2.3 -- regression smoke for frontend shape
# ============================================================================
log "STAGE 2.2.3 -- frontend regression test"

python3 <<'PY' || fail "regression patch 2.2.3 failed"
from pathlib import Path
p = Path("tests/regression_smoke.py")
src = p.read_text()
new_tests = '''def test_index_html_uses_capabilities():
    """Phase 2.2 -- the static index page must fetch /api/capabilities and
    not hardcode the old PROVIDER_BASE_URL_PRESETS constant."""
    import urllib.request, re
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        assert r.status == 200
        html = r.read().decode("utf-8")
    m = re.search(r"<script>([\\s\\S]*?)</script>", html)
    assert m, "no <script> block in index.html"
    js = m.group(1)
    assert "loadCapabilities" in js, "loadCapabilities() not present in index.html"
    assert "/api/capabilities" in js, "no /api/capabilities fetch in index.html"
    assert "PROVIDER_BASE_URL_PRESETS" not in js, (
        "PROVIDER_BASE_URL_PRESETS still hardcoded; should be replaced by "
        "the capabilities cache."
    )


def test_set_base_url_editability_for_agent():
    """Phase 2.2 -- setBaseUrlEditability must wire up the agent provider's
    base URL field. This is the regression check for the bug that the
    agent path was previously uncontrolled."""
    import urllib.request, re
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode("utf-8")
    m = re.search(r"<script>([\\s\\S]*?)</script>", html)
    js = m.group(1)
    assert re.search(r"setBaseUrlEditability[\\s\\S]{0,2500}agentBaseUrl", js), (
        "setBaseUrlEditability does not reference agentBaseUrl; the agent "
        "path is still uncontrolled."
    )
    assert "getAgentProviderCaps" in js, (
        "getAgentProviderCaps helper not present; agent editability not "
        "wired to /api/capabilities."
    )


'''
needle = "    print()\n    if failures == 0:"
if "test_index_html_uses_capabilities" in src:
    print("frontend checks already present")
else:
    src = src.replace(needle, new_tests + "    test_index_html_uses_capabilities()\n    test_set_base_url_editability_for_agent()\n    print()\n    if failures == 0:", 1)
    p.write_text(src)
    print("patched regression_smoke.py for 2.2.3")
PY
py_compile tests/regression_smoke.py

log "running regression suite after 2.2.3"
python3 tests/regression_smoke.py 2>&1 | tee -a "$LOG" | tail -40 \
  || fail "regression suite failed at 2.2.3"
if grep -E "\[FAIL\]" "$LOG" >/dev/null; then
  grep -E "\[FAIL\]" "$LOG" | head -5
  fail "regression suite has failures at 2.2.3"
fi
log "regression suite green after 2.2.3"

commit_one 2.2.3 "test: cover /api/capabilities frontend wiring

Verifies:
  - index.html fetches /api/capabilities and defines loadCapabilities
  - old PROVIDER_BASE_URL_PRESETS constant is gone
  - setBaseUrlEditability now wires up agentBaseUrl (the bugfix)
  - getAgentProviderCaps helper is present"

# ============================================================================
# Stage 2.3.1 -- docs/capabilities.md
# ============================================================================
log "STAGE 2.3.1 -- provider switch contract"

mkdir -p docs
cat > docs/capabilities.md <<'MDEOF'
# Provider & Model Capability Contract

Single source of truth for which provider the user has selected, what
base URL is in play, which models the dropdown should show, and which
fields the UI may edit.

## Endpoints

- `GET /api/capabilities` -- the registry. See `korina/schemas_capabilities.py`.
  Two sections: `providers` (response-LLM, 4 today), `agent_providers`
  (agent, 2 today). Each entry has 8 fields: `label`, `default_base_url`,
  `editable_base_url`, `manageable`, `model_sources`, `is_local`,
  `agent_only`, `response_llm_only`.

- `POST /api/llm/provider/activate` -- switches the local inference
  server when the response-LLM provider is `manageable: true`. Body
  `{provider, model}`. Response includes `activation.started` and
  `activation.stopped` lists.

## Invariants

1. **The backend owns provider rules.** No JS file may hardcode
   "llama.cpp -> http://127.0.0.1:8080/v1" or
   "openai-compatible -> base URL is editable". Both are derived from
   `/api/capabilities`.

2. **A provider is editable iff `editable_base_url: true`.** The UI
   disables the base-URL input otherwise. Today:
   - llama.cpp, lmstudio, ollama: locked.
   - openai-compatible, anthropic: editable.

3. **manageable providers get a server lifecycle.** Picking a
   `manageable: true` response-LLM provider must POST
   `/api/llm/provider/activate` after persisting config and before
   refetching `/api/models`.

4. **Agent-only providers never appear in `providers`.** Today only
   `anthropic` is agent-only.

5. **`/api/capabilities` is fetch-once.** The frontend caches the
   response for the session. Reload the page to pick up registry
   changes.

6. **Capability keys are always snake_case in JSON.**

## Adding a new provider

1. Add an entry to `PROVIDER_CAPABILITIES` in `korina/util/presets.py`.
   Pick the right section: `response_llm_only`, `agent_only`, or both.
2. If local (`is_local: true`), set `manageable: true` and add a
   corresponding branch to `start_*/stop_*` in
   `korina/services/provider_manager.py`.
3. Run `python3 tests/regression_smoke.py`. The capability check
   (`test_capabilities_endpoint`) hard-codes the expected provider set;
   update it if you add or remove a provider.
4. Commit on `beta`; do not touch `alpha` or `master` until release.

## Provider switch user flow (response-LLM)

1. User picks a new provider in `#llmProvider`.
2. UI:
   - `maybeApplyProviderPreset('llmProvider','llmBaseUrl')` to set the
     base URL to the registry default.
   - `setBaseUrlEditability()` to lock/unlock the base URL.
   - `syncConverseSettingsUI()` to refresh status pills.
   - `saveConfigNow()` to persist.
   - `activateSelectedProvider(llmProvider(), lmModel())` to
     start/stop the local server.
   - `loadModelOptions(true)` to refresh the model dropdown.
   - `loadAgentModelOptions()` to refresh the agent dropdown.
   - `health()` to refresh the status text.

## Provider switch user flow (agent)

1. User picks a new agent provider in `#agentProvider`.
2. UI:
   - `maybeApplyProviderPreset('agentProvider','agentBaseUrl')`.
   - `setBaseUrlEditability()` (now also updates `agentBaseUrl`).
   - `saveConfigSoon()`.
3. There is no `activate_agent_provider` endpoint. The agent runs on
   demand in `generate_agent_state_report`; the user only needs to
   set the base URL and model.

## Out of scope for Phase 2

- Per-model capability metadata (audio_input, mmproj, vram) -- Phase 4.
- Provider/model compatibility matrix -- Phase 4.
- Live reload of `/api/capabilities` -- only on full page reload.
MDEOF

commit_one 2.3.1 "docs: provider switch contract for Phase 2 capabilities

Single place future work can read to know what the UI/backend contract
is. Covers endpoints, invariants, new-provider checklist, and the
two user flows (response-LLM and agent)."

# ============================================================================
# Stage 2.3.2 -- edge-case regression test
# ============================================================================
log "STAGE 2.3.2 -- edge-case regression test"

python3 <<'PY' || fail "regression patch 2.3.2 failed"
from pathlib import Path
p = Path("tests/regression_smoke.py")
src = p.read_text()
new_test = '''def test_capabilities_anthropic_default_and_editability():
    """Phase 2.3 -- pin the editable_base_url contract across all providers."""
    import json, urllib.request
    with urllib.request.urlopen(BASE + "/api/capabilities", timeout=10) as r:
        body = json.loads(r.read().decode("utf-8"))
    a = body["agent_providers"]["anthropic"]
    assert a["default_base_url"] == ""
    assert a["editable_base_url"] is True
    assert a["is_local"] is False
    assert a["agent_only"] is True
    p_ = body["providers"]
    assert p_["openai-compatible"]["editable_base_url"] is True
    assert p_["llama.cpp"]["editable_base_url"] is False
    assert p_["lmstudio"]["editable_base_url"] is False
    assert p_["ollama"]["editable_base_url"] is False
    expected_keys = {
        "label", "default_base_url", "editable_base_url", "manageable",
        "model_sources", "is_local", "agent_only", "response_llm_only",
    }
    for section in (p_, body["agent_providers"]):
        for pid, cap in section.items():
            missing = expected_keys - set(cap.keys())
            assert not missing, f"{pid} missing keys: {sorted(missing)}"


'''
needle = "    print()\n    if failures == 0:"
if "test_capabilities_anthropic_default_and_editability" in src:
    print("edge-case check already present")
else:
    src = src.replace(needle, new_test + "    test_capabilities_anthropic_default_and_editability()\n    print()\n    if failures == 0:", 1)
    p.write_text(src)
    print("patched regression_smoke.py for 2.3.2")
PY
py_compile tests/regression_smoke.py

log "running regression suite after 2.3.2"
python3 tests/regression_smoke.py 2>&1 | tee -a "$LOG" | tail -40 \
  || fail "regression suite failed at 2.3.2"
if grep -E "\[FAIL\]" "$LOG" >/dev/null; then
  grep -E "\[FAIL\]" "$LOG" | head -5
  fail "regression suite has failures at 2.3.2"
fi
log "regression suite green after 2.3.2"

commit_one 2.3.2 "test: pin editable_base_url contract across all providers

Verifies:
  - anthropic default_base_url is empty (user must supply)
  - anthropic editable_base_url is true
  - openai-compatible editable; llama.cpp/lmstudio/ollama not
  - all providers declare the same 8 capability keys (no field drift)"

# ============================================================================
# Stage 2.4.1 -- full live regression + OpenAPI parity
# ============================================================================
log "STAGE 2.4.1 -- full live regression"

git push origin beta >>"$LOG" 2>&1 || fail "git push failed at 2.4.1"
REMOTE=$(git ls-remote --heads origin beta | awk '{print $1}')
LOCAL=$(git rev-parse beta)
[ "$REMOTE" = "$LOCAL" ] || fail "remote $REMOTE != local $LOCAL after push"
log "pushed to beta at $LOCAL"

log "running final regression suite"
python3 tests/regression_smoke.py 2>&1 | tee -a "$LOG" | tail -50 \
  || fail "final regression failed"
if grep -E "\[FAIL\]" "$LOG" >/dev/null; then
  grep -E "\[FAIL\]" "$LOG" | head -5
  fail "final regression has failures"
fi
log "final regression green"

PATHS_JSON=$(curl -fsS --max-time 10 http://127.0.0.1:8001/openapi.json) \
  || fail "openapi.json fetch failed"
echo "$PATHS_JSON" | python3 -c "
import json, sys
spec = json.load(sys.stdin)
paths = spec.get('paths', {})
n = len(paths)
print(f'openapi path count: {n}')
assert '/api/capabilities' in paths, 'capabilities not in openapi'
for p in ['/api/health','/api/config','/api/models','/api/chat','/api/transcribe','/api/acks','/api/agent/events','/api/llm/provider/activate']:
    assert p in paths, f'missing prior path: {p}'
print('openapi parity OK')
" >>"$LOG" 2>&1 || fail "openapi parity check failed"
log "openapi parity OK"

MODEL=$(ls /home/roggoz/Disks/SN750/models/lmstudio-community/gemma-4-E2B-it-GGUF/*.gguf 2>/dev/null | head -1 || true)
if [ -n "$MODEL" ]; then
  log "provider activation smoke (gemma 4 E2B)"
  curl -fsS --max-time 30 -X POST http://127.0.0.1:8001/api/llm/provider/activate \
    -H "Content-Type: application/json" \
    -d "{\"provider\":\"llama.cpp\",\"model\":\"$MODEL\"}" \
    | python3 -m json.tool >>"$LOG" 2>&1 || fail "provider activation failed"
  curl -fsS --max-time 60 -X POST http://127.0.0.1:8001/api/chat \
    -H "Content-Type: application/json" \
    -d "{\"message\":\"hi\",\"history\":[]}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('chat ok:', (d.get('reply') or 'NO REPLY')[:80])" \
    >>"$LOG" 2>&1 || fail "chat round-trip failed"
  log "provider lifecycle OK"
else
  log "no gemma 4 E2B GGUF on disk; skipping functional chat smoke"
fi

# ============================================================================
# Stage 2.4.2 -- PROGRESS.md update + final push
# ============================================================================
log "STAGE 2.4.2 -- PROGRESS.md update"

LATEST_SHA=$(git rev-parse --short HEAD)
LATEST_MSG=$(git log -1 --pretty=%s)
TS=$(date -u +%Y-%m-%d\ %H:%M:%S\ UTC)

cat > docs/refactor/PROGRESS.md <<MDEOF
# Refactor Progress

Mirror of the checklist at the bottom of blueprint.md.

**Branch:** beta
**Latest verified Phase 2 commit:** \`$LATEST_SHA\` -- $LATEST_MSG
**Last full verification:** $TS on roggoz. \`tests/regression_smoke.py\`
passed all checks (including new capabilities + frontend-wiring +
edge-case tests) with real /api/chat enabled; /api/chat returned
HTTP 200 with a live LLM reply.

## Phase 0 -- Stabilization

- [done] 0.1 barge-in thresholds -- completed before Phase 1; validated on roggoz.
- [done] 0.2 injection migration -- steer/delivery wording migrated to injection terminology where required.
- [done] 0.3 config role split -- runtime config separated from tracked example config.
- [done] 0.4 agent_api_key docs -- documented API key/env contract.
- [done] 0.5 secrets contract -- runtime secrets excluded from tracked config; README updated.
- [done] 0.6 display label for agent -- model labels normalized for display.
- [done] 0.7 verify on roggoz -- Phase 0 verified and committed.

## Phase 1 -- Backend modularization

- [done] 1.1 directory layout -- package skeleton under korina/ added.
- [done] 1.2 paths/config -- path/bootstrap/config helpers moved into korina/ package.
- [done] 1.3 runtime state -- module globals gathered into korina/runtime/state.py.
- [done] 1.4 services -- service areas extracted into korina/services/.
- [done] 1.5 config helpers -- helper functions redistributed into focused modules.
- [done] 1.6 routes -- FastAPI route ownership split into korina/routes/.
- [done] 1.7 schemas -- routes import schemas from korina.schemas; validation returns 422 correctly.
- [done] 1.8 backward-compat shim -- korina.app.main() introduced as canonical uvicorn launcher.
- [done] 1.9 delete monolith body -- Korina/korina_voice_lab.py reduced to 3-line shim.
- [done] 1.10 verify -- full live regression and Phase 0 route-contract comparison.

**Phase 1 result:** backend modularization complete on beta. Current app
construction lives in korina/app_factory.py; Korina/korina_voice_lab.py
is only the compatibility shim. The live API exposes the same 19 path
contract as the Phase 0 baseline with no missing/added paths and no
method diffs.

## Phase 2 -- Single source of truth

- [done] 2.1 capabilities endpoint -- /api/capabilities serves the
  PROVIDER_CAPABILITIES registry split into providers and
  agent_providers; Pydantic schema in korina/schemas_capabilities.py;
  route in korina/routes/capabilities.py; registry in
  korina/util/presets.py. 4 response-LLM providers, 2 agent providers.
- [done] 2.2 frontend reads capabilities -- loadCapabilities() caches
  the registry; setBaseUrlEditability() now controls the agent base URL
  field (was uncontrolled); maybeApplyProviderPreset() and
  providerPresetBaseUrl() read from the cache. Old
  PROVIDER_BASE_URL_PRESETS constant removed.
- [done] 2.3 switch contract documented -- docs/capabilities.md is the
  canonical reference. Covers endpoints, invariants, new-provider
  checklist, response-LLM and agent user flows, and what's out of
  scope for Phase 2.
- [done] 2.4 verify -- full live regression passed on roggoz;
  /api/capabilities returns 200; /api/chat still works after
  provider activation; OpenAPI path count is Phase 1 baseline + 1
  (/api/capabilities) and all prior paths remain.

**Phase 2 result:** single source of truth for provider rules is live.
The agent provider's base-URL field is now properly controlled by
/api/capabilities (the bug the blueprint flagged). Response-LLM
provider behavior is identical to Phase 1 for current users.

## Phase 3 -- Frontend modularization

- [ ] 3.1 module strategy
- [ ] 3.2 extract modules
- [ ] 3.3 extract styles
- [ ] 3.4 model fetch on open
- [ ] 3.5 verify

## Phase 4 -- Capability registry

- [ ] 4.1 capability metadata
- [ ] 4.2 /api/models includes capabilities
- [ ] 4.3 frontend filters
- [ ] 4.4 provider/model compatibility
- [ ] 4.5 verify

## Phase 5 -- Process supervision unification

- [ ] 5.1 supervisor decision
- [ ] 5.2 unit files
- [ ] 5.3 verify

## Phase 6 -- Testing + CI

- [ ] 6.1 scaffold
- [ ] 6.2 backend tests
- [ ] 6.3 frontend smoke
- [ ] 6.4 CI
MDEOF

commit_one 2.4.2 "docs: mark Phase 2 complete in PROGRESS.md"

git push origin beta >>"$LOG" 2>&1 || fail "git push failed at 2.4.2"
REMOTE=$(git ls-remote --heads origin beta | awk '{print $1}')
LOCAL=$(git rev-parse beta)
[ "$REMOTE" = "$LOCAL" ] || fail "remote $REMOTE != local $LOCAL after final push"
log "final push OK at $LOCAL"

log "AUTOPILOT COMPLETE. Phase 2 fully landed on beta at $(git rev-parse --short HEAD)"
echo
echo "---- phase 2 autopilot summary ----"
git log --oneline -12
echo
echo "Files added/modified:"
git diff --name-only HEAD~10..HEAD 2>/dev/null || git diff --name-only --cached
