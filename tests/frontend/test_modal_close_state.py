"""Frontend tests pinning modal close + apply behavior."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS_DIR = REPO / "Korina" / "js"


def read(p: Path) -> str:
    return p.read_text()


def function_body(src: str, name: str) -> str:
    patterns = [f"export async function {name}(", f"export function {name}(", f"async function {name}(", f"function {name}("]
    idx = next((src.find(p) for p in patterns if src.find(p) != -1), -1)
    assert idx != -1, f"function {name} not found"
    paren_open = src.find("(", idx)
    depth = 1
    i = paren_open + 1
    while i < len(src) and depth > 0:
        if src[i] == "(": depth += 1
        elif src[i] == ")": depth -= 1
        i += 1
    while i < len(src) and src[i] in " \t\n\r": i += 1
    assert src[i] == "{"
    brace_open = i
    depth = 0
    i = brace_open
    while i < len(src):
        if src[i] == "{": depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace_open + 1:i]
        i += 1
    raise AssertionError(f"unterminated function {name}")


def test_settings_ui_apply_config_snapshots_loaded_state():
    body = function_body(read(JS_DIR / "settings-ui.js"), "applyConfig")
    assert "state.appConfigSnapshot" in body


def test_app_js_close_settings_hides_before_applying():
    body = function_body(read(JS_DIR / "app.js"), "closeSettings")
    assert "modal?.classList.remove('open')" in body
    assert "void _closeSettingsImpl().catch" in body
    assert body.find("classList.remove('open')") < body.find("_closeSettingsImpl()")


def test_app_js_inner_close_settings_persists_and_activates():
    body = function_body(read(JS_DIR / "app.js"), "_closeSettingsImpl")
    assert "saveConfigNow()" in body
    assert "state.appConfigSnapshot" in body
    assert "activateSelectedProvider" in body
    assert "state.appConfigSnapshot =" in body


def test_app_js_inner_close_settings_handles_activate_failure():
    body = function_body(read(JS_DIR / "app.js"), "_closeSettingsImpl")
    assert "try {" in body and "} catch (e) {" in body
    assert "Provider activation failed" in body


def test_app_js_still_has_modal_click_outside_handler():
    app = read(JS_DIR / "app.js")
    assert "modal.addEventListener('click'" in app or 'modal.addEventListener("click"' in app


def test_app_js_still_has_escape_key_handler():
    assert "Escape" in read(JS_DIR / "app.js")


def test_app_js_save_settings_routes_through_close_settings():
    body = function_body(read(JS_DIR / "app.js"), "saveSettings")
    assert "closeSettings()" in body
