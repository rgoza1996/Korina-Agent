"""Regression tests for user-facing error messages.

These tests pin the *contracts* the dogfood pass surfaced as bugs #8 and #10:

- Bug #8 (recorder.js:setupMic): when navigator.mediaDevices is missing,
  the user must see a friendly explanation, not the raw
  "Cannot read properties of undefined (reading 'getUserMedia')" TypeError.
- Bug #10 (local-models.js:addRoot): when POST /api/models/roots returns a
  4xx with a JSON body, the user must see the parsed `detail` field,
  not the raw JSON envelope.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KORINA_DIR = REPO_ROOT / "Korina"
JS_DIR = KORINA_DIR / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_setup_mic_throws_friendly_error_when_mediaDevices_missing():
    """setupMic must guard navigator.mediaDevices before calling getUserMedia.

    Without this guard, the catch in toggleLive surfaces a raw TypeError to
    the user. We require a clear English explanation instead.
    """
    recorder = read(JS_DIR / "recorder.js")

    # Must check that navigator.mediaDevices exists *before* calling
    # getUserMedia, so we can throw a meaningful Error instead of a TypeError.
    has_guard = bool(
        re.search(
            r"!\s*navigator\.mediaDevices\s*\|\|\s*!\s*navigator\.mediaDevices\.getUserMedia",
            recorder,
        )
    )
    assert has_guard, (
        "recorder.js:setupMic must guard navigator.mediaDevices before "
        "calling getUserMedia; otherwise a missing API surfaces as a raw "
        "TypeError to the user."
    )

    # The thrown error must contain a helpful hint, not a JSON blob or
    # the bare JS exception message.
    throw_block = re.search(
        r"throw new Error\(([^)]+)\)", recorder
    )
    assert throw_block, "setupMic must throw an Error with a user-readable message"
    msg = throw_block.group(1)
    # The throw message should mention "microphone" or "media" so the
    # user understands what failed.
    assert re.search(r"microphone|media|getUserMedia", msg, re.I), (
        f"setupMic throw message should mention microphone/media; got: {msg!r}"
    )


def test_add_root_parses_detail_field_from_json_error_response():
    """addRoot must parse the JSON `detail` field from error responses.

    FastAPI errors return `{"detail": "..."}`. The dogfood pass showed the
    raw JSON envelope being shown to the user instead of the underlying
    message.
    """
    local_models = read(JS_DIR / "local-models.js")

    # Find the body of addRoot by slicing from `async function addRoot`
    # to its matching closing brace at the function level.
    start = local_models.index("async function addRoot")
    depth = 0
    end = start
    for i in range(start, len(local_models)):
        ch = local_models[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    body = local_models[start:end]

    # Within addRoot, the error path must parse JSON to extract `detail`.
    # We require BOTH a JSON parse (try/JSON.parse or response.json()) AND
    # a reference to `.detail` within the same function body.
    has_json_parse = bool(
        re.search(r"\.json\(\)|JSON\.parse\(", body)
    )
    assert has_json_parse, (
        "local-models.js:addRoot must parse the JSON error body to extract "
        "the FastAPI `detail` field before showing it to the user."
    )

    has_detail_use = bool(re.search(r"\bdetail\b", body))
    assert has_detail_use, (
        "local-models.js:addRoot must reference the parsed `detail` field "
        "when building the user-facing failure message."
    )

    # The error must NOT surface the raw `await r.text()` body without
    # JSON parsing — that is exactly the bug we are pinning.
    raw_text_in_error = bool(
        re.search(
            r"throw new Error\(\s*(?:await\s+)?r\.text\(\)\s*\)",
            body,
        )
    )
    assert not raw_text_in_error, (
        "local-models.js:addRoot must not throw `await r.text()` directly; "
        "it must parse the JSON `detail` first."
    )

