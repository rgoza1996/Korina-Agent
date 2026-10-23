"""Static load-state mapping contract for the Endpoints modal.

Executable success/fetch-error rendering cases live in tests/js/test_endpoints.mjs.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


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
