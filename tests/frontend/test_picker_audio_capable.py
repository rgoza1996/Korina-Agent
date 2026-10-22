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

These checks retain the markup/source contracts. Executable filter behavior
is covered by tests/js/test_capability_filter.mjs.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JS_DIR = REPO / 'Korina/js'
INDEX_HTML = REPO / 'Korina/index.html'


def test_picker_explains_that_capability_hints_are_estimates():
    html = INDEX_HTML.read_text()
    assert 'estimated to accept audio' in html
    assert 'These hints do not prove audio support' in html
    assert 'warnings are shown in red' in html

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
