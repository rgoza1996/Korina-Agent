"""Runtime audio-capability probe.

Determines whether a failed multimodal STT request indicates the model
actually doesn't support audio input (cacheable failure) vs a transient
or content-related failure (don't cache). Also provides the cache
read/write helpers that wrap korina.config.

Design rationale: see Phase 4 plan §4.6. The probe is the first real
audio STT request; on audio-not-supported failure we cache and fall
back to whisper forever (per triple) until the user explicitly clears.

Phase 5 silent-empty handling (2026-06-27):
  A model that returns HTTP 200 with empty content and a non-'stop'
  finish_reason (typically 'length' after the model spent its budget
  on reasoning without producing audio-derived text) is structurally
  unable to process audio. We classify this as a cacheable failure
  ('silent_200_empty_content') so subsequent requests skip the
  multimodal round-trip entirely and go straight to Whisper.

  finish_reason='' (default) preserves pre-fix behaviour, so existing
  callers and tests that don't track finish_reason still treat silent
  empty as transient.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


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


def classify_failure(
    status_code: int,
    body: str,
    finish_reason: str = "",
) -> Tuple[bool, str]:
    """Return (is_audio_unsupported, reason).

    is_audio_unsupported is True iff the failure indicates the model or
    inference engine cannot accept audio input. Such failures are
    cacheable; subsequent requests should fall back to whisper.

    status_code: HTTP status from the multimodal STT endpoint.
    body: response body as a string.
    finish_reason: OpenAI chat-completion finish_reason ('' when unknown).
        When the server returns HTTP 200 with empty content and a
        finish_reason that is neither '' nor 'stop' (typically 'length'
        after spending the budget on reasoning without producing audio
        text), the request is structurally broken for audio and we cache
        it as 'silent_200_empty_content'. An empty finish_reason (legacy
        callers, active probe pre-fix) preserves old behaviour.
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

    # 200 + empty content + non-stop finish_reason → silent structural
    # failure. See module docstring for rationale. Only triggered when
    # the caller actually has finish_reason data; an empty string means
    # "I don't know" (legacy / active probe) and we fall through to the
    # old transient classification.
    #
    # We parse the body as the OpenAI chat-completion shape (the caller
    # in stt.py synthesises one) and check `choices[0].message.content`
    # is empty. Falling back to a raw-body emptiness check keeps the
    # pure-classify API usable without JSON.
    if status_code == 200 and finish_reason and finish_reason != "stop":
        content_empty = False
        if not body.strip():
            content_empty = True
        else:
            try:
                import json as _json
                parsed = _json.loads(body)
                if isinstance(parsed, dict):
                    choices = parsed.get('choices') or []
                    if choices and isinstance(choices[0], dict):
                        msg = choices[0].get('message') or {}
                        content = msg.get('content')
                        if content is None or (isinstance(content, str) and not content.strip()):
                            content_empty = True
            except (ValueError, TypeError):
                # Body wasn't JSON; if it's a literal empty sentinel, count it
                if body.strip() in ('""', "''", "null", "empty_text"):
                    content_empty = True
        if content_empty:
            return (True, "silent_200_empty_content")

    return (False, "transient_or_content_failure")


def triple_key(provider: str, base_url: str, model: str) -> str:
    """Canonical cache key for the (provider, base_url, model) triple."""
    from korina.config import audio_unsupported_key
    return audio_unsupported_key(provider, base_url, model)


def maybe_fallback_to_whisper(
    provider: str, base_url: str, model: str,
    stt_status_code: int, stt_response_body: str,
    whisper_fallback_fn,
    finish_reason: str = "",
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

    finish_reason: see classify_failure. Forwarded so the same silent-empty
    detection works at this layer.
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

    unsupported, reason = classify_failure(
        stt_status_code, stt_response_body, finish_reason=finish_reason,
    )
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