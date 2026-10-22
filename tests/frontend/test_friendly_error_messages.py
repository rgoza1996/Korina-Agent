"""Regression tests for user-facing error messages.

These tests pin the *contracts* the dogfood pass surfaced as bugs #8 and #10:

- Bug #8 (recorder.js:setupMic): when navigator.mediaDevices is missing,
  the user must see a friendly explanation, not the raw
  "Cannot read properties of undefined (reading 'getUserMedia')" TypeError.
Add-root error presentation is exercised in tests/js/test_api_errors.mjs.
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

