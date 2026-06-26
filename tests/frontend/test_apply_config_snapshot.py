"""Regression: applyConfig must not reference undefined variables.

The previous version of applyConfig assigned
  state.appConfigSnapshot = { llm_provider: current.llm_provider, ... }
which threw `ReferenceError: current is not defined` every time the
modal was closed (because loadConfig/activateSelectedProvider both call
applyConfig). closeSettings hid the modal but the user got
"Provider activation failed: current is not defined" with no settings
persisted or activated.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS_UI_JS = REPO / "Korina" / "js" / "settings-ui.js"


def function_body(src: str, name: str) -> str:
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
    paren_open = src.find("(", idx)
    depth = 1
    i = paren_open + 1
    while i < len(src) and depth > 0:
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    while i < len(src) and src[i] in " \t\n\r":
        i += 1
    assert i < len(src) and src[i] == "{"
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


def test_apply_config_snapshot_uses_parameter_not_undefined_var():
    """applyConfig must build state.appConfigSnapshot from its `c` parameter,
    not from a free variable like `current`."""
    src = SETTINGS_UI_JS.read_text()
    body = function_body(src, "applyConfig")
    assert "state.appConfigSnapshot" in body

    # Extract the snapshot object body. Start at the first `{` after
    # `state.appConfigSnapshot =` and walk braces to matching `}`.
    snap_start = body.find("state.appConfigSnapshot")
    eq_idx = body.find("=", snap_start)
    brace_open = body.find("{", eq_idx)
    assert brace_open != -1
    depth = 0
    i = brace_open
    while i < len(body):
        c = body[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    snap_body = body[brace_open + 1 : i]

    # For each line of the form `key: expr,` or `key: expr`, the LHS
    # identifier is the key (before the first `:`); everything after
    # the first `:` on that line is the RHS expression.
    safe_globals = {"state", "this", "Math", "Number", "String",
                    "parseInt", "parseFloat", "Boolean", "JSON", "Array",
                    "Object", "undefined", "null", "true", "false"}

    bad_refs = []
    for line in snap_body.splitlines():
        # split into key | rhs by first `:`
        if ":" not in line:
            continue
        key, _, rhs = line.partition(":")
        key = key.strip().rstrip(",").strip()
        # Skip if the line is itself a nested object opening (no key).
        if not key.replace("_", "").isidentifier():
            continue
        # Extract identifiers from the RHS only.
        for ident in re.findall(r"\b([a-zA-Z_$][\w$]*)\b", rhs):
            if ident in safe_globals:
                continue
            if ident == "c":
                continue  # the parameter (e.g. `c.llm_provider`)
            # Allow property access chains where the property name is the
            # same as the key (e.g. `key: c.key`); not technically needed
            # for our snapshot but harmless.
            if ident == key:
                continue
            bad_refs.append((ident, line.strip()))

    assert not bad_refs, (
        "applyConfig() snapshot references free identifiers (not `c` "
        "or builtins):\n"
        + "\n".join(f"  {ident!r} in: {line}" for ident, line in bad_refs)
    )
