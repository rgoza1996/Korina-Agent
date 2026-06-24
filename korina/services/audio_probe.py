"""Runtime audio-capability probe.

Determines whether a failed multimodal STT request indicates the model
actually doesn't support audio input (cacheable failure) vs a transient
or content-related failure (don't cache). Also provides the cache
read/write helpers that wrap korina.config.

Design rationale: see Phase 4 plan §4.6. The probe is the first real
audio STT request; on audio-not-supported failure we cache and fall
back to whisper forever (per triple) until the user explicitly clears.
"""

from __future__ import annotations

import re
from typing import Tuple


# Patterns that indicate "this model/inference engine cannot accept
# audio input" in the response body. Specific enough to avoid matching
# generic STT content failures (no speech, bad audio, timeout).
_AUDIO_UNSUPPORTED_PATTERNS = [
    re.compile(r"audio.*not\s+support", re.IGNORECASE),
    re.compile(r"audio.*unsupport", re.IGNORECASE),
    re.compile(r"audio.*not\s+enabled", re.IGNORECASE),
    re.compile(r"audio.*invalid", re.IGNORECASE),
    re.compile(r"no\s+mmproj", re.IGNORECASE),
    re.compile(r"multimodal.*not\s+support", re.IGNORECASE),
    re.compile(r"input_audio.*not\s+support", re.IGNORECASE),
]


def classify_failure(status_code: int, body: str) -> Tuple[bool, str]:
    """Return (is_audio_unsupported, reason).

    is_audio_unsupported is True iff the failure indicates the model or
    inference engine cannot accept audio input. Such failures are
    cacheable; subsequent requests should fall back to whisper.

    status_code: HTTP status from the multimodal STT endpoint.
    body: response body as a string.
    """
    if not isinstance(body, str):
        body = str(body or "")

    # 415 Unsupported Media Type is the canonical "audio not supported" code.
    if status_code == 415:
        return (True, "http_415_unsupported_media_type")

    # 400/422 with explicit "audio ... not supported" / "no mmproj" in body.
    if status_code in (400, 422):
        for pat in _AUDIO_UNSUPPORTED_PATTERNS:
            if pat.search(body):
                return (True, f"body_match:{pat.pattern}")

    # 200 with empty/garbage audio content (server accepted the request
    # but didn't actually process audio) -- this is the silent-failure
    # case. We can't easily detect this without examining the response
    # payload, so we don't classify it here; the caller decides whether
    # to log a warning.

    return (False, "transient_or_content_failure")


def triple_key(provider: str, base_url: str, model: str) -> str:
    """Canonical cache key for the (provider, base_url, model) triple."""
    from korina.config import audio_unsupported_key
    return audio_unsupported_key(provider, base_url, model)


def maybe_fallback_to_whisper(
    provider: str, base_url: str, model: str,
    stt_status_code: int, stt_response_body: str,
    whisper_fallback_fn,
) -> tuple[bool, dict]:
    """Decide whether to fall back to whisper for this STT request.

    Returns (used_fallback, response_payload). If used_fallback is True,
    response_payload is the result of whisper_fallback_fn() (typically
    a dict with 'text', 'engine', and 'fallback_reason' keys).

    Behavior:
      1. Check cache: if (provider, base_url, model) is in audio_unsupported,
         skip the failing call entirely and fall back.
      2. Otherwise, the caller already made the failing call. Check
         classify_failure: if True, persist to cache + fall back.
      3. If False, return (False, {'error': ...}) for the caller to handle.
    """
    from korina.config import (
        config_audio_unsupported,
        set_audio_unsupported,
    )

    triple = triple_key(provider, base_url, model)
    cache = config_audio_unsupported()
    cached = cache.get(triple)
    if cached:
        # Cache hit -- skip retry entirely.
        return (True, {
            **whisper_fallback_fn(),
            "fallback_reason": cached.get("reason", "cached_audio_unsupported"),
            "audio_unsupported_since": cached.get("since", ""),
            "triple": triple,
        })

    unsupported, reason = classify_failure(stt_status_code, stt_response_body)
    if unsupported:
        set_audio_unsupported(provider, base_url, model, reason, stt_response_body)
        return (True, {
            **whisper_fallback_fn(),
            "fallback_reason": reason,
            "triple": triple,
        })

    return (False, {
        "error": "stt_failed",
        "status_code": stt_status_code,
        "body": stt_response_body[:500],
    })
