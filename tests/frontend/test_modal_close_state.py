"""Frontend tests pinning the contract for close-on-any-path persistence + activation."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS_DIR = REPO / "Korina" / "js"


def read(p: Path) -> str:
    return p.read_text()


def function_body(src: str, name: str) -> str:
    """Brace-counted function body extraction so nested try/catch doesn't fool us.
    Skips the parameter list (handles `(c = {})`) before starting brace count.
    """
    patterns = [
        f"export async function {name}(",
        f"export function {name}(",
        f"async function {name}(",
        f"function {name}(",
    ]
    idx = -1
    for p in patterns:
        i = src.find(p)
        if i != -1:
            idx = i
            break
    assert idx != -1, f"function {name} not found"
    # Skip past the opening paren, balancing nested parens (for `(c = {})`
    # default-object arguments).
    paren_open = src.find("(", idx)
    assert paren_open != -1
    depth = 1
    i = paren_open + 1
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    # Skip whitespace until first {
    while i < len(src) and src[i] in " \t\n\r":
        i += 1
    assert i < len(src) and src[i] == "{", (
        f"function {name}: expected body brace at offset {i}, got {src[i]!r}"
    )
    brace_open = i
    depth = 0
    i = brace_open
    while i < len(src):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace_open + 1 : i]
        i += 1
    raise AssertionError(f"unterminated function {name}")


def test_settings_ui_apply_config_snapshots_loaded_state():
    settings_ui = read(JS_DIR / "settings-ui.js")
    body = function_body(settings_ui, "applyConfig")
    assert "state.appConfigSnapshot" in body, (
        "applyConfig() must snapshot the loaded config so closeSettings() can "
        "diff current form values against it."
    )


def test_app_js_close_settings_wrapper_always_hides_modal():
    app = read(JS_DIR / "app.js")
    body = function_body(app, "closeSettings")
    assert "$('settingsModal')?.classList.remove('open')" in body
    hide_idx = body.find("classList.remove('open')")
    try_idx = body.find("try {")
    catch_idx = body.find("} catch (e) {", try_idx if try_idx != -1 else 0)
    assert try_idx != -1 and catch_idx != -1
    assert hide_idx > catch_idx, "hide line must run AFTER try/catch"


def test_app_js_inner_close_settings_persists_and_activates():
    app = read(JS_DIR / "app.js")
    body = function_body(app, "_closeSettingsImpl")
    assert "saveConfigNow()" in body
    assert "state.appConfigSnapshot" in body
    assert "activateSelectedProvider" in body
    assert "state.appConfigSnapshot =" in body


def test_app_js_inner_close_settings_handles_activate_failure():
    app = read(JS_DIR / "app.js")
    body = function_body(app, "_closeSettingsImpl")
    assert "try {" in body and "} catch (e) {" in body
    assert "Provider activation failed" in body


def test_app_js_still_has_modal_click_outside_handler():
    app = read(JS_DIR / "app.js")
    assert "modal.addEventListener('click'" in app or 'modal.addEventListener("click"' in app


def test_app_js_still_has_escape_key_handler():
    app = read(JS_DIR / "app.js")
    assert "Escape" in app


def test_app_js_save_settings_routes_through_close_settings():
    app = read(JS_DIR / "app.js")
    body = function_body(app, "saveSettings")
    assert "closeSettings()" in body
