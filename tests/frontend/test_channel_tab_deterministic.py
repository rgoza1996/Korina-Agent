"""Regression contracts for Channel tab deterministic population (Phase 5 Commit 6 follow-up).

Pin the lifecycle/error-handling changes to populateChannelTab() and the
Channel tab click handler so the prior "DOM race" misdiagnosis does not
regress and so transient fetch failures surface a retryable state.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX_HTML = REPO / "Korina" / "index.html"
JS_DIR = REPO / "Korina" / "js"
SETTINGS_UI_JS = JS_DIR / "settings-ui.js"
APP_JS = JS_DIR / "app.js"


def _read(p: Path) -> str:
    return p.read_text()


def _function_body(src: str, name: str) -> str:
    patterns = [
        f"export async function {name}(",
        f"export function {name}(",
        f"async function {name}(",
        f"function {name}(",
    ]
    idx = next((src.find(p) for p in patterns if src.find(p) != -1), -1)
    assert idx != -1, f"function {name} not found"
    paren_open = src.find("(", idx)
    depth = 1
    i = paren_open + 1
    while i < len(src) and depth > 0:
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
        i += 1
    while i < len(src) and src[i] in " \t\n\r":
        i += 1
    assert src[i] == "{", f"function {name} has no body"
    brace_open = i
    depth = 0
    i = brace_open
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace_open + 1 : i]
        i += 1
    raise AssertionError(f"unterminated function {name}")


def test_index_html_channel_panel_contains_channel_select():
    """The Channel <select> must live inside the Channel settings panel."""
    html = _read(INDEX_HTML)
    panel_idx = html.find('id="settingsChannel"')
    select_idx = html.find('id="channelSelect"')
    hint_idx = html.find('id="channelSelectHint"')
    assert panel_idx != -1, "settingsChannel panel missing from index.html"
    assert select_idx != -1, "channelSelect missing from index.html"
    assert hint_idx != -1, "channelSelectHint missing from index.html"
    assert panel_idx < select_idx < hint_idx, (
        "channelSelect must be inside settingsChannel and precede its hint"
    )


def test_populate_channel_tab_checks_http_status():
    """fetchChannelList() must reject non-2xx responses explicitly."""
    body = _function_body(_read(SETTINGS_UI_JS), "fetchChannelList")
    assert "!r.ok" in body
    assert "throw new Error" in body
    # Both endpoints must be status-checked, not just the first.
    assert body.count("!r.ok") >= 2, "both endpoints must check response.ok"


def test_populate_channel_tab_sets_loading_state():
    """The select must be disabled and the hint must say Loading while fetching."""
    body = _function_body(_read(SETTINGS_UI_JS), "populateChannelTab")
    assert "select.disabled = true" in body
    assert "Loading registered channels" in body


def test_populate_channel_tab_reenables_select_on_success():
    """On success the select must be re-enabled (otherwise dropdown is dead)."""
    body = _function_body(_read(SETTINGS_UI_JS), "populateChannelTab")
    assert "select.disabled = false" in body


def test_populate_channel_tab_no_wrong_race_diagnostic():
    """The previous CSS/DOM-race diagnostic text must be gone."""
    body = _function_body(_read(SETTINGS_UI_JS), "populateChannelTab")
    assert "Channel panel not ready" not in body
    assert "Click the Channel tab again" not in body
    assert "CSS animation lag" not in body
    assert "DOM race" not in body


def test_populate_channel_tab_surfaces_fetch_failure_with_retry_hint():
    """A fetch failure must surface a hint that tells the user how to retry."""
    body = _function_body(_read(SETTINGS_UI_JS), "populateChannelTab")
    assert "Failed to load channels" in body
    assert "Click the Channel tab to retry" in body


def test_populate_channel_tab_empty_state_handled():
    """Zero registered channels must render the disabled '-' option, not leave select empty."""
    body = _function_body(_read(SETTINGS_UI_JS), "populateChannelTab")
    assert "No channels registered" in body


def test_app_js_tab_handler_catches_populate_rejection():
    """The Channel-tab click handler must attach .catch() to populateChannelTab()."""
    src = _read(APP_JS)
    assert "populateChannelTab().catch" in src, (
        "Channel-tab click must handle populateChannelTab() rejection"
    )
    # Must NOT be a bare fire-and-forget call.
    bare_call_idx = src.find("populateChannelTab();")
    assert bare_call_idx == -1, (
        "bare populateChannelTab(); call without .catch() reintroduces swallowed rejections"
    )


def test_app_js_change_listener_wired_outside_click_handler():
    """#channelSelect's change listener must be wired at init, not per-click."""
    src = _read(APP_JS)
    click_idx = src.find("btn.dataset.settingsTab === 'channel'")
    assert click_idx != -1, "Channel tab click handler missing"
    # Find where the .forEach click handler closes. The click-handler
    # forEach is the next })); after the channel click index; the wiring
    # must appear AFTER that close.
    forEach_close = src.find("}));", click_idx)
    assert forEach_close != -1, "settingsTab click forEach close not found"
    # Match the exact guard written in app.js (not a substring of any
    # unrelated __wired attribute).
    wired_idx = src.find("_channelSelect.__wired")
    assert wired_idx != -1, "change-listener __wired guard missing"
    assert wired_idx > forEach_close, (
        "change listener must be wired AFTER the settings-tab click forEach block, not inside it"
    )


def test_app_js_change_listener_wired_exactly_once():
    """The #channelSelect change listener must be wired exactly once."""
    src = _read(APP_JS)
    wiring = "_channelSelect.addEventListener('change', () => saveChannelSelection())"
    count = src.count(wiring)
    assert count == 1, f"change listener wired {count} times, expected exactly 1"


def test_close_settings_unchanged():
    """closeSettings() must still hide the modal before applying state."""
    body = _function_body(_read(APP_JS), "closeSettings")
    assert "modal?.classList.remove('open')" in body
    assert "void _closeSettingsImpl().catch" in body


def test_close_settings_impl_unchanged():
    """_closeSettingsImpl() must still snapshot+save+activate."""
    body = _function_body(_read(APP_JS), "_closeSettingsImpl")
    assert "saveConfigNow()" in body
    assert "state.appConfigSnapshot" in body
    assert "activateSelectedProvider" in body