"""Frontend source-text assertions for the debug-strip runtime-truth fix.

Commit 2 of the debug-strip audit (issues #17, #18, #19, #20, #21, #22).
The frontend must consume the server-truth fields exposed by Commit 1's
/api/health additions:

  - tts: {ok, loaded, device, cuda_available, provider, base_url, error}
  - tts_provider
  - response_llm_provider, response_llm_base_url, response_llm_model

Behavior contracts:
  1. api.js:health() must NOT construct its own TTS URL from form settings
     (Bug A / #17). It must use j.tts.base_url from the server.
  2. api.js:health() must read j.tts_provider for the offline-label
     (Bug A label-hardcode / #20), not ttsProvider() form value.
  3. api.js:health() must set hostInfo from server fields
     (Bug C / #19). The two form-derived setters in settings-ui.js:206
     and app.js:323 must be deleted.
  4. api.js:health() must NOT flip pageDot red on a single missed poll
     (Bug B / #18) - require 2-3 consecutive failures.
  5. index.html must have a settingsLiveStatus element separate from
     settingsInfo (Bug D / #21).
  6. api.js:health() must populate settingsLiveStatus from j.llm_error
     and j.stt_llm_error / j.ok on every poll.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KORINA_DIR = REPO_ROOT / "Korina"
JS_DIR = KORINA_DIR / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- 1. Bug A / #17: ttsHealth must use server URL, not form ttsBaseUrl() ----

def test_api_js_health_uses_server_tts_block_url_not_form_settings():
    api = read(JS_DIR / "api.js")
    # Inside health(), the TTS fetch URL must come from j.tts.base_url or
    # j.tts_base_url, not from ttsBaseUrl() (which reads form state).
    # We assert the function references j.tts in a fetch URL context.
    assert "j.tts.base_url" in api or "j.tts_base_url" in api, (
        "api.js:health() must read the resolved TTS URL from /api/health "
        "(j.tts.base_url or j.tts_base_url), not from form-derived ttsBaseUrl()"
    )


def test_api_js_health_does_not_call_ttsBaseUrl():
    """The TTS fetch in health() must not invoke the form-derived ttsBaseUrl()."""
    api = read(JS_DIR / "api.js")
    # Look inside the health() function body specifically.
    # Quick heuristic: ttsBaseUrl() must not appear as a call expression
    # in the second try-block (TTS fetch). We do a weaker check: the
    # function must not call ttsBaseUrl() at all when building the URL.
    assert "fetch(`${ttsBaseUrl()}/health`)" not in api, (
        "api.js must not call fetch('${ttsBaseUrl()}/health') - the URL "
        "must come from /api/health so the frontend cannot disagree with "
        "the server about which TTS endpoint is live."
    )


def test_api_js_health_reads_tts_block_fields():
    """health() must consume j.tts.{loaded, error} for the pill label."""
    api = read(JS_DIR / "api.js")
    assert "j.tts" in api, "api.js must reference j.tts block"
    # Accept direct (j.tts.loaded) or aliased (tts.loaded after const tts=j.tts).
    assert (
        "j.tts.loaded" in api
        or "j.tts?.loaded" in api
        or "tts.loaded" in api
    ), "api.js must read j.tts.loaded (or aliased) to color the pill"
    assert (
        "j.tts.error" in api
        or "j.tts?.error" in api
        or "tts.error" in api
    ), "api.js must include j.tts.error in the offline message"


# ---- 2. Bug A label / #20: error label uses server tts_provider ----

def test_api_js_health_offline_label_uses_server_provider():
    api = read(JS_DIR / "api.js")
    # When tts.ok is false, the catch-block-style error path must include
    # the SERVER's tts_provider, not the form's ttsProvider().
    # Acceptable patterns: j.tts_provider, j.tts.provider
    assert "j.tts_provider" in api or "j.tts?.provider" in api, (
        "api.js:health() must reference j.tts_provider (or j.tts.provider) "
        "for the offline label, so an openai-compatible TTS failure does "
        "not display as 'Kokoro offline'."
    )


# ---- 3. Bug C / #19: hostInfo is set by health(), not by form changes ----

def test_api_js_health_sets_hostInfo_from_server_fields():
    api = read(JS_DIR / "api.js")
    assert "hostInfo" in api, "api.js must reference hostInfo"
    # The setter must use server-resolved URLs from j, not form fields.
    # Look for j.response_llm_base_url and j.tts.base_url in hostInfo
    # context.
    assert "j.response_llm_base_url" in api, (
        "api.js must set hostInfo from j.response_llm_base_url "
        "(server truth), not from llmBaseUrl() form field."
    )
    assert "j.tts" in api and ("j.tts.base_url" in api or "j.tts_base_url" in api), (
        "api.js must set hostInfo from j.tts.base_url (server truth), "
        "not from ttsBaseUrl() form field."
    )


def test_settings_ui_removes_form_derived_hostInfo_setter():
    """Bug C / #19: the duplicate hostInfo setter in applyConfig must go."""
    js = read(JS_DIR / "settings-ui.js")
    # Look ONLY inside the applyConfig function body. The exported getters
    # ttsBaseUrl()/llmBaseUrl() are still needed by other code.
    start = js.find("export function applyConfig")
    assert start >= 0, "settings-ui.js must still export applyConfig"
    rest = js[start:]
    end_idx = len(rest)
    for marker in ("\nexport function ", "\nexport async function "):
        idx = rest.find(marker, 10)
        if idx > 0 and idx < end_idx:
            end_idx = idx
    apply_body = rest[:end_idx]
    # The form-derived setter must be gone from applyConfig.
    has_form_setter = (
        "hostInfo" in apply_body
        and ("llmBaseUrl()" in apply_body or "ttsBaseUrl()" in apply_body)
    )
    assert not has_form_setter, (
        "settings-ui.js:applyConfig() must no longer set hostInfo from "
        "form fields (llmBaseUrl + ttsBaseUrl). The setter now lives in "
        "api.js:health() and runs every 5s from server-resolved URLs."
    )


def test_app_js_removes_form_derived_hostInfo_setter():
    """Bug C / #19: the duplicate hostInfo setter in app.js event handler must go."""
    js = read(JS_DIR / "app.js")
    # Look for the pattern: hostInfo setter inside an onchange handler
    # using llmBaseUrl() and ttsBaseUrl().
    assert not (
        "hostInfo" in js and "llmBaseUrl()" in js and "ttsBaseUrl()" in js
    ), (
        "app.js must no longer set hostInfo from form fields on settings "
        "change events. api.js:health() owns hostInfo now (every 5s)."
    )


# ---- 4. Bug B / #18: pageDot grace period for transient failures ----

def test_api_js_health_grace_period_for_page_dot():
    api = read(JS_DIR / "api.js")
    # Must track consecutive failures, not flip red on a single miss.
    # Acceptable signals: state.healthFailures, state.consecutiveHealthFails,
    # or any counter in `state` checked before flipping to 'bad'.
    assert any(
        marker in api
        for marker in (
            "state.healthFailures",
            "state.consecutiveHealthFails",
            "state.healthFailCount",
            "healthFailures",
            "consecutiveHealthFails",
        )
    ), (
        "api.js:health() must track consecutive /api/health failures and "
        "only flip pageDot to red after 2-3 misses (10-15s grace). "
        "A single transient blip must not turn the pill red."
    )


# ---- 5. Bug D / #21: settingsLiveStatus separate from settingsInfo ----

def test_index_html_has_settings_live_status_element():
    html = read(KORINA_DIR / "index.html")
    assert 'id="settingsLiveStatus"' in html, (
        "index.html must have a <span id='settingsLiveStatus'> element "
        "next to settingsInfo for live LLM/STT health, separate from the "
        "load-time 'Loaded config.json settings.' banner."
    )


def test_api_js_health_populates_settings_live_status():
    api = read(JS_DIR / "api.js")
    assert "settingsLiveStatus" in api, (
        "api.js:health() must populate #settingsLiveStatus every 5s "
        "from j.ok, j.llm_error, j.stt_llm_error."
    )


# ---- 6. ttsProviderLabel survives the import already exists ----

def test_api_js_still_imports_ttsProviderLabel():
    """Regression guard: the import line must not be removed by refactor."""
    api = read(JS_DIR / "api.js")
    assert "ttsProviderLabel" in api, (
        "api.js must still import ttsProviderLabel from labels.js "
        "(used in the offline-label fallback path)."
    )