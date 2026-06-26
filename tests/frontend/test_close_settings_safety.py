"""Regression: modal close must hide the modal even when internal helpers throw.

A previous version of closeSettings() called three functions that weren't
imported (effectiveSttLlmProvider, ttsPort, ttsModel), which threw a
TypeError on every close attempt and trapped the user inside the modal.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP_JS = REPO / "Korina" / "js" / "app.js"
SETTINGS_UI_JS = REPO / "Korina" / "js" / "settings-ui.js"


def _read(p: Path) -> str:
    return p.read_text()


def _function_body(src: str, name: str) -> str:
    """Extract the body of `async function NAME(...) { ... }` using
    brace-counting, so nested braces inside try/catch blocks don't
    fool a non-greedy regex.
    """
    idx = src.find(f"async function {name}(")
    if idx == -1:
        idx = src.find(f"function {name}(")
    assert idx != -1, f"function {name} not found"
    # find first { after the param list
    brace_open = src.find("{", idx)
    assert brace_open != -1
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


def _imports_from_settings_ui() -> set[str]:
    src = _read(APP_JS)
    m = re.search(
        r"import\s*\{([^}]+)\}\s*from\s*[\"\']\./settings-ui\.js[\"\']",
        src,
    )
    if not m:
        return set()
    names = []
    for token in m.group(1).split(","):
        token = token.strip()
        if not token:
            continue
        if " as " in token:
            token = token.split(" as ", 1)[0].strip()
        names.append(token)
    return set(names)


def _settings_ui_exports() -> set[str]:
    src = _read(SETTINGS_UI_JS)
    return set(re.findall(r"^export\s+function\s+(\w+)", src, re.MULTILINE))


def test_every_function_called_by_close_settings_is_imported():
    """If closeSettings() calls a settings-ui function, it must be imported."""
    src = _read(APP_JS)
    body = _function_body(src, "closeSettings") + _function_body(src, "_closeSettingsImpl")
    called = set(re.findall(r"\b([a-zA-Z_$][\w$]*)\s*\(", body))
    called -= {"if", "for", "while", "switch", "catch", "function", "return",
               "await", "typeof", "new", "async"}
    settings_ui_exports = _settings_ui_exports()
    suspects = {n for n in called if n in settings_ui_exports}
    missing = suspects - _imports_from_settings_ui()
    assert not missing, (
        f"closeSettings() calls settings-ui functions that are not imported "
        f"in app.js: {sorted(missing)}. Add them to the import block."
    )


def test_closeSettings_wrapper_always_hides_modal():
    """closeSettings() must unconditionally hide the modal AFTER its try/catch."""
    src = _read(APP_JS)
    body = _function_body(src, "closeSettings")
    assert "$('settingsModal')?.classList.remove('open')" in body, (
        "closeSettings() must unconditionally hide the settings modal. "
        "Found body:\n" + body
    )
    # The hide line must appear AFTER the inner try block. Find indices.
    hide_idx = body.find("classList.remove('open')")
    try_idx = body.find("try {")
    catch_idx = body.find("} catch (e) {", try_idx if try_idx != -1 else 0)
    assert try_idx != -1 and catch_idx != -1, (
        "closeSettings() must wrap _closeSettingsImpl() in try/catch"
    )
    assert hide_idx > catch_idx, (
        "modal-hide line must run AFTER the inner try/catch closes"
    )


def test_inner_closeSettings_isolated_from_closeSettings_throws():
    """Outer closeSettings must call _closeSettingsImpl inside try/catch."""
    src = _read(APP_JS)
    body = _function_body(src, "closeSettings")
    assert "try {" in body
    assert "await _closeSettingsImpl()" in body
    assert "} catch (e) {" in body


def test_saveSettings_routes_through_closeSettings():
    """The Done button must close via the same path as X / click-outside / Escape."""
    src = _read(APP_JS)
    body = _function_body(src, "saveSettings")
    assert "closeSettings()" in body, (
        "saveSettings() (Done button) must call closeSettings() so persistence "
        "+ activation behavior is identical across all close paths."
    )
