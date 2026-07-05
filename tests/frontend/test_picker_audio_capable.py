import shutil
NODE_BIN = shutil.which("node") or "node"  # CI runners have node on PATH
"""Regression: the multimodal STT picker must surface audio-capability
before the user picks a model that can't accept audio input.

User's reported bug (2026-06-27): picking google/gemma-4-e4b (text-only)
as the multimodal STT model resulted in every utterance returning HTTP
200 with empty content and finish_reason='length', because llama.cpp
happily accepted the input_audio part but the model has no mmproj and
no audio projection. Every utterance cost ~4s of wasted multimodal
round-trip.

The audio-capability filter infrastructure exists (model_capability.py,
capability-filter.js) but was being silently bypassed because:

  1. state.capabilityFilterOverride defaulted to True (i.e. "show all
     models regardless of capability").
  2. The hidden checkbox in index.html made the override user-invisible.
  3. providers-ui.js never called filterModelsByCapability on
     j.stt_llm_models before rendering the dropdown.
  4. applyModelOptionStatus marked non-capable models red but did not
     annotate the label with '(no audio)' — so the user had no
     at-a-glance signal.

These tests cover all four gaps.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import shutil


REPO = Path(__file__).resolve().parents[2]
JS_DIR = REPO / 'Korina/js'
INDEX_HTML = REPO / 'Korina/index.html'


# ---------- state.js default ----------


def test_state_explicitly_defaults_capability_filter_override_to_false():
    """By default, multimodal STT picker should only show audio-capable
    models. The override must be explicitly set to false in state.js —
    relying on `undefined` is fragile because any caller that sets it
    once sticks for the session."""
    src = (REPO / 'Korina/js/state.js').read_text()
    # Find the block declaration of state.capabilityFilterOverride
    m = re.search(
        r"capabilityFilterOverride\s*:\s*([^,\n]+)",
        src,
    )
    assert m, (
        'state.capabilityFilterOverride is not explicitly set in state.js. '
        'Defaulting to undefined is fragile — if any code path sets it once '
        '(e.g. user opens Settings), the override stays on for the session.'
    )
    value = m.group(1).strip()
    assert value == 'false', (
        f"state.capabilityFilterOverride must default to false (got {value!r}). "
        "Defaulting to true bypasses the audio-capability filter for every user."
    )


# ---------- index.html: All models override must be visible ----------


def test_stt_capability_override_checkbox_is_not_hidden():
    """The 'All models' override must be visible (not hidden) so users
    can toggle it. Hidden + checked by default meant users couldn't see
    the filter existed."""
    html = INDEX_HTML.read_text()
    m = re.search(
        r'<input[^>]*id="sttCapabilityFilterOverride"[^>]*>',
        html,
    )
    assert m, 'sttCapabilityFilterOverride input not found in index.html'
    tag = m.group(0)
    assert 'hidden' not in tag, (
        'sttCapabilityFilterOverride is hidden — users must be able to see/toggle it'
    )


def test_stt_capability_override_has_visible_label():
    """A bare checkbox without a label is invisible. There must be a
    visible <label> associating with the checkbox — either explicitly
    via for="..." OR implicitly by wrapping the input inside a <label>."""
    html = INDEX_HTML.read_text()
    explicit = re.search(
        r'<label[^>]*for="sttCapabilityFilterOverride"',
        html,
    )
    implicit = re.search(
        r'<label[^>]*>\s*<input[^>]*id="sttCapabilityFilterOverride"',
        html,
    )
    assert explicit or implicit, (
        'sttCapabilityFilterOverride must have a visible <label> '
        '(either <label for="..."> or wrapping the input)'
    )


def test_stt_capability_override_default_is_unchecked():
    """Default state should be unchecked = filter ON. Currently the
    HTML has 'checked' which sets the override on, defeating the filter."""
    html = INDEX_HTML.read_text()
    m = re.search(
        r'<input[^>]*id="sttCapabilityFilterOverride"[^>]*>',
        html,
    )
    tag = m.group(0)
    # 'checked' can appear as either attribute form
    assert not re.search(r'\bchecked\b', tag.split('id')[0]), (
        "sttCapabilityFilterOverride must default to unchecked; 'checked' "
        'attribute sets the override on, defeating the filter.'
    )


# ---------- providers-ui.js: apply filter to dropdown ----------


def test_loadModelOptions_filters_sttLlmModel_by_capability():
    """loadModelOptions must call filterModelsByCapability with
    sttLlmModelRequirement before populating #sttLlmModel."""
    src = (JS_DIR / 'providers-ui.js').read_text()
    # Find the function body
    m = re.search(
        r"export\s+async\s+function\s+loadModelOptions\([^)]*\)\s*\{",
        src,
    )
    assert m, 'loadModelOptions not found'
    body_start = m.end()
    # Find the matching closing brace by scanning
    depth = 1
    i = body_start
    while i < len(src) and depth > 0:
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
        i += 1
    body = src[body_start:i]
    # The filter must be applied to the stt_llm_models list
    assert 'filterModelsByCapability' in body, (
        'loadModelOptions does not call filterModelsByCapability — the '
        'multimodal STT dropdown will render every model regardless of '
        'audio capability.'
    )
    assert 'stt_llm_models_capabilities' in body, (
        'loadModelOptions does not read stt_llm_models_capabilities from '
        '/api/models — the filter would have no input to work with.'
    )
    assert 'sttLlmModelRequirement' in body, (
        'loadModelOptions does not call sttLlmModelRequirement — the '
        'filter would have no requirement spec to honor the override.'
    )


def test_applyModelOptionStatus_appends_no_audio_suffix():
    """When a model is rendered with supports_audio_input=false, the
    label should include ' (no audio)' so users see it at a glance,
    not just a red color."""
    src = (JS_DIR / 'providers-ui.js').read_text()
    m = re.search(r"function\s+applyModelOptionStatus\([^)]*\)\s*\{", src)
    assert m, 'applyModelOptionStatus not found'
    body_start = m.end()
    depth = 1
    i = body_start
    while i < len(src) and depth > 0:
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
        i += 1
    body = src[body_start:i]
    assert 'no audio' in body.lower(), (
        'applyModelOptionStatus must annotate non-capable models with '
        "'(no audio)' so the user sees audio-capability at a glance."
    )


# ---------- capability-filter.js behaviour ----------


# ---------- Node integration test: full chain ----------


def _run_node_module(js_path, fetch_stub_js, extra_modules=()):
    """Run a JS file under node with a DOM shim and a fetch stub.
    Returns CompletedProcess."""
    src = js_path.read_text()
    cjs_src = re.sub(r'^import .*?$', '// import stripped', src, flags=re.M)
    cjs_src = re.sub(
        r'^export async function (\w+)',
        r'exports.\1 = async function \1',
        cjs_src, flags=re.M,
    )
    cjs_src = re.sub(
        r'^export function (\w+)',
        r'exports.\1 = function \1',
        cjs_src, flags=re.M,
    )
    tmp_module = Path('/tmp/_capability_test_cjs.js')
    tmp_module.write_text(cjs_src)
    driver = Path('/tmp/_capability_test_driver.js')
    driver.write_text(
        f"""
const mod = require({json.dumps(str(tmp_module))});
{fetch_stub_js}
(async () => {{
  try {{
    await mod.runTest();
    console.log('TEST_OK');
    process.exit(0);
  }} catch (e) {{
    console.error('THREW ' + (e && e.message || e));
    process.exit(1);
  }}
}})();
"""
    )
    return subprocess.run(
        [NODE_BIN, str(driver)],
        capture_output=True, text=True, timeout=15,
    )


def test_capability_filter_drops_gemma_4_e4b_when_override_off():
    """End-to-end: with capabilityFilterOverride=false, the multimodal
    STT picker must NOT include google/gemma-4-e4b in the rendered list."""
    js = (JS_DIR / 'capability-filter.js').read_text()
    # Strip ESM import lines and convert exports to CJS
    cjs = re.sub(r"^import .*?$", "", js, flags=re.M)
    cjs = re.sub(
        r"^export\s+(async\s+)?function\s+(\w+)",
        r"exports.\2 = \1function \2",
        cjs, flags=re.M,
    )
    cjs_path = Path('/tmp/_capability_filter_cjs.js')
    cjs_path.write_text(cjs)
    driver = (
        "const f = require(" + json.dumps(str(cjs_path)) + ");\n"
        "const models = [\n"
        "  'google/gemma-4-e4b',\n"
        "  'google/gemma-4-e2b-it-mmproj',\n"
        "  'ultravox-v0_5-llama-3_1-8b',\n"
        "  'qwen2.5-1.5b-instruct',\n"
        "];\n"
        "const caps = {\n"
        "  'google/gemma-4-e4b': { supports_audio_input: false },\n"
        "  'google/gemma-4-e2b-it-mmproj': { supports_audio_input: true },\n"
        "  'ultravox-v0_5-llama-3_1-8b': { supports_audio_input: true },\n"
        "  'qwen2.5-1.5b-instruct': { supports_audio_input: false },\n"
        "};\n"
        "const out = f.filterModelsByCapability(models, caps, { kind: 'audio_input' });\n"
        "if (out.includes('google/gemma-4-e4b')) {\n"
        "  console.error('FAIL: gemma-4-e4b should be filtered out, got ' + JSON.stringify(out));\n"
        "  process.exit(1);\n"
        "}\n"
        "if (out.includes('qwen2.5-1.5b-instruct')) {\n"
        "  console.error('FAIL: qwen2.5 should be filtered out');\n"
        "  process.exit(1);\n"
        "}\n"
        "if (!out.includes('google/gemma-4-e2b-it-mmproj')) {\n"
        "  console.error('FAIL: gemma-4-e2b-it-mmproj should be kept');\n"
        "  process.exit(1);\n"
        "}\n"
        "if (!out.includes('ultravox-v0_5-llama-3_1-8b')) {\n"
        "  console.error('FAIL: ultravox should be kept');\n"
        "  process.exit(1);\n"
        "}\n"
        "console.log('FILTER_OUT ' + JSON.stringify(out));\n"
    )
    driver_path = Path('/tmp/_capability_filter_driver.js')
    driver_path.write_text(driver)
    result = subprocess.run(
        [NODE_BIN, str(driver_path)],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        pytest.fail(
            f"capability-filter smoke test failed: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    out_line = result.stdout.split('FILTER_OUT ')[-1].strip() if 'FILTER_OUT ' in result.stdout else ''
    assert 'gemma-4-e2b-it-mmproj' in out_line
    assert 'ultravox' in out_line
    assert 'gemma-4-e4b' not in out_line
    assert 'qwen2.5-1.5b-instruct' not in out_line


def test_capability_filter_keeps_everything_when_override_on():
    """When the override is on (requirement kind 'any'), every model passes."""
    js = (JS_DIR / 'capability-filter.js').read_text()
    cjs = re.sub(r"^import .*?$", "", js, flags=re.M)
    cjs = re.sub(
        r"^export\s+(async\s+)?function\s+(\w+)",
        r"exports.\2 = \1function \2",
        cjs, flags=re.M,
    )
    cjs_path = Path('/tmp/_capability_filter_cjs_any.js')
    cjs_path.write_text(cjs)
    driver = (
        "const f = require(" + json.dumps(str(cjs_path)) + ");\n"
        "const models = ['google/gemma-4-e4b', 'qwen2.5-1.5b-instruct'];\n"
        "const caps = {\n"
        "  'google/gemma-4-e4b': { supports_audio_input: false },\n"
        "  'qwen2.5-1.5b-instruct': { supports_audio_input: false },\n"
        "};\n"
        "const out = f.filterModelsByCapability(models, caps, { kind: 'any' });\n"
        "if (out.length !== 2) {\n"
        "  console.error('FAIL: kind=any should keep all, got ' + JSON.stringify(out));\n"
        "  process.exit(1);\n"
        "}\n"
        "console.log('FILTER_KEEP_ALL');\n"
    )
    driver_path = Path('/tmp/_capability_filter_driver_any.js')
    driver_path.write_text(driver)
    result = subprocess.run(
        [NODE_BIN, str(driver_path)],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        pytest.fail(
            f"capability-filter kind=any test failed: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    assert 'FILTER_KEEP_ALL' in result.stdout
