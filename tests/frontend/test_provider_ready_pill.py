"""Contracts for the provider/model readiness pill."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "Korina" / "index.html"
API_JS = REPO / "Korina" / "js" / "api.js"


def _function_body(src, name):
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
                return src[brace_open + 1:i]
        i += 1
    raise AssertionError(f"unterminated function {name}")


def test_provider_ready_pill_is_in_top_strip():
    html = INDEX.read_text()
    assert 'id="providerReady"' in html
    assert 'id="providerReadyDot"' in html


def test_health_renders_provider_ready_pill():
    body = _function_body(API_JS.read_text(), "health")
    assert "providerReady" in body
    assert "setDot('providerReadyDot'" in body
    assert "response_llm_load" in body


def test_activate_selected_provider_signals_a_pending_provider_change():
    src = API_JS.read_text()
    assert "state.providerPending" in src
