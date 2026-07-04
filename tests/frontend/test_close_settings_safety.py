"""Regression contracts for settings modal close safety."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP_JS = REPO / "Korina" / "js" / "app.js"
SETTINGS_UI_JS = REPO / "Korina" / "js" / "settings-ui.js"


def _read(p: Path) -> str:
    return p.read_text()


def _function_body(src: str, name: str) -> str:
    idx = src.find(f"async function {name}(")
    if idx == -1:
        idx = src.find(f"function {name}(")
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
    assert src[i] == "{"
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


def _imports_from_settings_ui() -> set[str]:
    src = _read(APP_JS)
    m = re.search("import\\s*\\{([^}]+)\\}\\s*from\\s*['\\\"]\\./settings-ui\\.js['\\\"]", src)
    if not m:
        return set()
    names = []
    for token in m.group(1).split(","):
        token = token.strip()
        if token:
            names.append(token.split(" as ", 1)[0].strip())
    return set(names)


def _settings_ui_exports() -> set[str]:
    return set(re.findall(r"^export\s+function\s+(\w+)", _read(SETTINGS_UI_JS), re.MULTILINE))


def test_every_function_called_by_close_settings_is_imported():
    src = _read(APP_JS)
    body = _function_body(src, "closeSettings") + _function_body(src, "_closeSettingsImpl")
    called = set(re.findall(r"\b([a-zA-Z_$][\w$]*)\s*\(", body))
    called -= {"if", "for", "while", "switch", "catch", "function", "return", "await", "typeof", "new", "async"}
    suspects = {n for n in called if n in _settings_ui_exports()}
    missing = suspects - _imports_from_settings_ui()
    assert not missing, f"settings-ui functions called but not imported: {sorted(missing)}"


def test_closeSettings_hides_modal_before_background_apply():
    body = _function_body(_read(APP_JS), "closeSettings")
    assert "modal?.classList.remove('open')" in body
    assert "void _closeSettingsImpl().catch" in body
    hide_idx = body.find("classList.remove('open')")
    apply_idx = body.find("_closeSettingsImpl()")
    assert hide_idx != -1 and apply_idx != -1
    assert hide_idx < apply_idx, "modal must hide before save/activate begins"


def test_closeSettings_surfaces_background_apply_failure_without_blocking_close():
    body = _function_body(_read(APP_JS), "closeSettings")
    assert "Settings apply failed" in body
    assert "catch(e =>" in body or "catch(e=>" in body


def test_saveSettings_routes_through_closeSettings():
    body = _function_body(_read(APP_JS), "saveSettings")
    assert "closeSettings()" in body
