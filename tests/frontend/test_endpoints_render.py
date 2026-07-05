"""Regression: populateEndpoints() must render the list within a
reasonable time when /api/health returns a realistic response.

The first version of this modal had a render-time TypeError
(`h[`response llm_load`]` — `h` was a dict, not an array-indexable
object). The fetch resolved but the render path threw after
`list.textContent = 'Loading…'` was set, so the modal sat on
"Loading…" forever. These tests guard the data shape and loadKey
contract and exercise the real JS module via node + a DOM shim."""

import json
import re
import subprocess
from pathlib import Path

import pytest

import shutil  # noqa: E402  -- inserted by /tmp/fix_tests.py

NODE_BIN = shutil.which("node") or "node"  # CI runners have node on PATH

_HAS_NODE = shutil.which("node") is not None


def _node_bin():
    """Return the node executable, or skip the test if node is unavailable."""
    if not _HAS_NODE:
        import pytest
        pytest.skip("node not in PATH (test shells out to node to render JS)")
    return NODE_BIN


REPO = Path(__file__).resolve().parents[2]

HEALTH = {
    'response_llm_base_url': 'http://127.0.0.1:8080/v1',
    'response_llm_chat_url': 'http://127.0.0.1:8080/v1/chat/completions',
    'multimodal_stt_base_url': 'http://127.0.0.1:8080/v1',
    'multimodal_stt_chat_url': 'http://127.0.0.1:8080/v1/chat/completions',
    'agent_base_url': 'http://127.0.0.1:8080/v1',
    'agent_chat_url': 'http://127.0.0.1:8080/v1/chat/completions',
    'tts_base_url': 'http://127.0.0.1:8880',
    'response_llm_load': {'ok': True, 'loaded': True, 'error': None},
    'multimodal_stt_load': {'ok': True, 'loaded': True, 'error': None},
    'agent_load': {'ok': False, 'error': 'URLError: Connection refused'},
    'tts': {'ok': True, 'base_url': 'http://127.0.0.1:8880', 'error': None},
}


def test_endpoints_js_has_loadKey_for_every_external_group():
    """Bug guard: the original bug was that the render loop tried to
    look up the load block via `h[...]` (literal array index). The
    fix introduced `loadKey` per group. If any external group lacks a
    `loadKey`, that's a smell — it'll default to 'always online' which
    is wrong for agent/stt/llm/tts."""
    src = (REPO / 'Korina/js/endpoints.js').read_text()
    for grp, key in [
        ('Response LLM', 'response_llm_load'),
        ('Multimodal STT', 'multimodal_stt_load'),
        ('Agent', 'agent_load'),
        ('TTS', 'tts'),
    ]:
        m = src.split(f"group: '{grp}'", 1)
        assert len(m) == 2, f'group {grp!r} missing from EXTERNAL_GROUPS'
        block, _ = m[1].split('keys:', 1)
        assert f"loadKey: '{key}'" in block, (
            f'group {grp!r} missing loadKey: {key!r} '
            '— will render as always online'
        )


def _run_node_module(js_path, fetch_stub_js, health=None):
    """Run endpoints.js under node with a DOM shim and the provided
    fetch stub. Returns CompletedProcess."""
    src = js_path.read_text()
    cjs_src = re.sub(r'^import .*?$', '// import stripped', src, flags=re.M)
    cjs_src = re.sub(
        r'^export async function (\w+)',
        r'exports.\1 = async function \1',
        cjs_src,
        flags=re.M,
    )
    cjs_src = re.sub(
        r'^export function (\w+)',
        r'exports.\1 = function \1',
        cjs_src,
        flags=re.M,
    )
    tmp_module = Path('/tmp/_endpoints_populate_cjs.js')
    tmp_module.write_text(cjs_src)
    driver = Path('/tmp/_endpoints_populate_driver.js')
    health_block = ''
    if health is not None:
        # Write JSON to its own file to avoid inlining JS object literals
        # (which collide with the driver f-string's quote escaping).
        health_file = Path('/tmp/_endpoints_populate_health.json')
        health_file.write_text(json.dumps(health))
        health_block = f"const __HEALTH = require({json.dumps(str(health_file))});\n"
    driver.write_text(
        f"""
const mod = require({json.dumps(str(tmp_module))});
{health_block}{fetch_stub_js}
(async () => {{
  try {{
    await mod.populateEndpoints();
    const html = elementsList.innerHTML;
    console.log('HTML_LEN ' + html.length);
    console.log('HTML_BEGIN');
    console.log(html);
    console.log('HTML_END');
    process.exit(0);
  }} catch (e) {{
    console.error('THREW ' + (e && e.message || e));
    process.exit(1);
  }}
}})();
"""
    )
    return subprocess.run(
        [_node_bin(), str(driver)],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _extract_html(stdout):
    """Pull the rendered HTML from between the HTML_BEGIN/HTML_END
    sentinels the driver emits."""
    lines = stdout.splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if line == 'HTML_BEGIN':
            start = i + 1
        elif line == 'HTML_END' and start is not None:
            end = i
            break
    if start is None or end is None:
        return ''
    return '\n'.join(lines[start:end])


def test_endpoints_js_renders_without_throwing_under_node():
    """Integration: real /api/health → all four external groups render
    without throwing (the bug was a post-fetch TypeError that left the
    modal stuck on Loading…)."""
    fetch_stub = (
        'function Element() { this.textContent = ""; this.innerHTML = ""; }\n'
        'const elementsList = new Element();\n'
        'global.$ = (id) => id === "endpointsList" ? elementsList : null;\n'
        'global.fetch = async () => ({ ok: true, json: async () => __HEALTH });\n'
        'global.location = { hostname: "100.71.89.62", origin: "http://100.71.89.62:8001" };\n'
    )
    result = _run_node_module(REPO / 'Korina/js/endpoints.js', fetch_stub, health=HEALTH)
    if result.returncode != 0:
        pytest.fail(
            f"populateEndpoints threw: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    html = _extract_html(result.stdout)
    assert html, f'no HTML_HEAD in node output: {result.stdout}'
    assert html != 'Loading…', f'stuck on Loading…: {html}'
    for grp in ['Page server', 'Response LLM', 'Multimodal STT', 'Agent', 'TTS']:
        assert grp in html, f'missing group {grp!r} in rendered HTML:\n{html[:400]}'


def test_endpoints_js_handles_failed_health_without_sticking():
    """If /api/health fails entirely, populateEndpoints must NOT leave
    'Loading…' on screen — it must show a real error message."""
    fetch_stub = (
        'function Element() { this.textContent = ""; this.innerHTML = ""; }\n'
        'const elementsList = new Element();\n'
        'global.$ = (id) => id === "endpointsList" ? elementsList : null;\n'
        'global.fetch = async () => { throw new Error("boom"); };\n'
        'global.location = { hostname: "127.0.0.1", origin: "http://127.0.0.1:8001" };\n'
    )
    result = _run_node_module(REPO / 'Korina/js/endpoints.js', fetch_stub)
    if result.returncode != 0:
        pytest.fail(
            f"failed-health path broke: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    html = _extract_html(result.stdout)
    assert html, f'no HTML_HEAD in node output: {result.stdout}'
    assert html != 'Loading…', f'stuck on Loading… after health fetch failure: {html}'
    assert 'failed to fetch' in html.lower(), f'no error message rendered: {html[:300]}'