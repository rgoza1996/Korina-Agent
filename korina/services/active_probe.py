"""Active audio-capability probe.

Fires a tiny synthetic audio request at a candidate (provider, base_url, model)
through the same multimodal STT path used by live conversation, then classifies
the response with the existing ``korina.services.audio_probe.classify_failure``
patterns. Successful probes clear any cached audio-unsupported entry for the
triple; failed probes either persist (cacheable audio-unsupported) or report
without persisting (transient).

Design rationale: see Phase 5.0 plan §5.1. Active probing is opt-in only --
triggered by the "Refresh local models" button in the settings modal. It does
not run on every /api/models load and it does not poll in the background.

Contract for ``supported``:
  True  -> the model accepted our audio request and returned non-empty text.
  False -> the model either explicitly rejected audio (audio_unsupported,
           cacheable) OR the request failed transiently (transport error,
           empty 200). The dropdown must mark these red; the *cache* is
           updated only when the failure is a confirmed "audio unsupported".
"""

from __future__ import annotations

import io
import re
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ProbeResult:
    """Result of a single active audio-capability probe."""

    model: str
    supported: bool
    reason: str  # empty on success; "transient_*" or "body_match:*" / "http_415_*" otherwise
    latency_ms: int
    status: int  # HTTP status from the multimodal call (0 on transport error)
    body_excerpt: str  # first 200 chars of the raw response body for debugging
    error: str  # empty on success; short error string on transport failure


def _build_synthetic_wav() -> bytes:
    """1 second of silence at 16 kHz mono, 16-bit PCM.

    Kept deliberately small so the probe stays cheap; we are not testing
    transcription quality, only whether the model rejects audio input.
    """
    sample_rate = 16000
    duration_seconds = 1
    sample_count = sample_rate * duration_seconds
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * sample_count)
    return buf.getvalue()


def _classify_runtime_error(exc: Exception) -> tuple[int, str]:
    """Map a raised exception from lmstudio_transcribe_wav into a synthetic
    (status_code, body) pair suitable for audio_probe.classify_failure.

    The runtime wraps HTTPError as RuntimeError with the body embedded. We
    peel off the HTTP code prefix when present so 400/422 body patterns can
    match.
    """
    msg = str(exc or "")
    match = re.search(r"HTTP (\d+):\s*(.*)", msg, re.DOTALL)
    if match:
        return int(match.group(1)), match.group(2)
    return 0, msg


def probe_audio_support(
    provider: str,
    base_url: str,
    model: str,
    *,
    timeout: float = 15.0,
    transcribe_fn=None,
) -> ProbeResult:
    """Probe a single (provider, base_url, model) triple.

    transcribe_fn defaults to
    ``korina.services.multimodal_stt.lmstudio_transcribe_wav``. Tests inject
    a stub; production callers should rely on the default.

    Side effects:
      - On supported=True, the persistent audio_unsupported cache entry for
        the triple is cleared (UX: the red badge disappears on next refresh).
      - On supported=False with a *cacheable* failure (http_415,
        body_match:audio.*, no mmproj, multimodal.*not support, etc.),
        the triple is persisted via ``set_audio_unsupported`` using the
        same shape the runtime STT path uses, so /api/models red-marks it
        immediately.
      - On supported=False with a transient classification, the cache is
        *not* updated -- transient failures should not stick.
    """
    from korina.services.audio_probe import classify_failure, triple_key
    from korina.config import set_audio_unsupported, clear_audio_unsupported

    if transcribe_fn is None:
        from korina.services.multimodal_stt import lmstudio_transcribe_wav as transcribe_fn

    provider_id = str(provider or "").strip().lower()
    base_url_clean = str(base_url or "").strip()
    # Normalize to the chat-completions endpoint the same way the runtime
    # streaming STT path does. lmstudio returns a 200-with-error-body when
    # you POST to the bare /v1 base URL, which would silently mask every
    # probe as a transient empty-text failure.
    if base_url_clean and not base_url_clean.rstrip("/").endswith("/chat/completions"):
        base_url_clean = base_url_clean.rstrip("/") + "/chat/completions"
    model_id = str(model or "").strip()
    triple_key_value = triple_key(provider_id, base_url_clean, model_id)

    tmp_path: Optional[Path] = None
    started = time.time()
    try:
        import tempfile
        with tempfile.NamedTemporaryFile(prefix="korina-probe-", suffix=".wav", delete=False) as f:
            f.write(_build_synthetic_wav())
            tmp_path = Path(f.name)
        result = transcribe_fn(tmp_path, model=model_id, base_url=base_url_clean, api_env="")
        text = str((result or {}).get("text") or "").strip()
        latency_ms = int((time.time() - started) * 1000)
        if text:
            clear_audio_unsupported(provider_id, base_url_clean, model_id)
            return ProbeResult(
                model=model_id,
                supported=True,
                reason="",
                latency_ms=latency_ms,
                status=200,
                body_excerpt=text[:200],
                error="",
            )
        # 200 with empty text -- silent failure. Treat as transient unless
        # we have a specific reason string in result.error.
        reason_excerpt = str((result or {}).get("error") or "empty_text")
        unsupported, reason = classify_failure(200, reason_excerpt)
        if unsupported:
            set_audio_unsupported(provider_id, base_url_clean, model_id, reason, reason_excerpt)
        return ProbeResult(
            model=model_id,
            supported=False,  # empty 200 is a UI-visible red mark either way
            reason=reason if unsupported else "transient_empty_text",
            latency_ms=latency_ms,
            status=200,
            body_excerpt=reason_excerpt[:200],
            error=reason_excerpt if not unsupported else "",
        )
    except Exception as e:
        latency_ms = int((time.time() - started) * 1000)
        status, body = _classify_runtime_error(e)
        unsupported, reason = classify_failure(status, body)
        body_excerpt = body[:200]
        error = str(e)[:200]
        if unsupported:
            set_audio_unsupported(provider_id, base_url_clean, model_id, reason, body)
        return ProbeResult(
            model=model_id,
            supported=False,
            reason=reason if unsupported else "transient_runtime_error",
            latency_ms=latency_ms,
            status=status,
            body_excerpt=body_excerpt,
            error=error,
        )
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def probe_many(
    provider: str,
    base_url: str,
    models: list[str],
    *,
    timeout: float = 15.0,
    transcribe_fn=None,
) -> list[ProbeResult]:
    """Sequentially probe a list of models. Sequential (not parallel) so we
    do not flood the active STT endpoint with N simultaneous multimodal
    requests; most user-facing hosts have a single loaded model at a time
    anyway.
    """
    results: list[ProbeResult] = []
    for model in models:
        mid = str(model or "").strip()
        if not mid:
            continue
        results.append(
            probe_audio_support(
                provider,
                base_url,
                mid,
                timeout=timeout,
                transcribe_fn=transcribe_fn,
            )
        )
    return results
