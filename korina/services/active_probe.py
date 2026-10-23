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
import json
import re
import time
import wave
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from collections.abc import Callable
from typing import Optional

from korina.runtime.json_http import RequestCancelledError
from korina.runtime.request_owner import check_current


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
    api_env: str | None = None,
    transcribe_fn=None,
    is_current: Callable[[], bool] | None = None,
) -> ProbeResult:
    """Probe a single (provider, base_url, model) triple.

    transcribe_fn defaults to
    ``korina.services.multimodal_stt.lmstudio_transcribe_wav``. Tests inject
    a stub; production callers should rely on the default. The timeout is
    forwarded to the production HTTP request; legacy injected callbacks retain
    their four-argument calling convention. api_env=None inherits auth only for
    the configured STT endpoint; an empty string explicitly disables auth.
    Retired owners cancel connected HTTP work and skip cache publication.

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
    from korina.services.audio_probe import classify_failure
    from korina.config import load_config, normalize_api_base_url, set_audio_unsupported, clear_audio_unsupported
    from korina.services.multimodal_stt import resolve_stt_api_env

    check_current(is_current)
    if transcribe_fn is None:
        from korina.services.multimodal_stt import lmstudio_transcribe_wav
        transcribe_fn = partial(lmstudio_transcribe_wav, timeout=timeout, is_current=is_current)

    provider_id = str(provider or "").strip().lower()
    base_url_clean = normalize_api_base_url(str(base_url or "").strip())
    # Normalize to the chat-completions endpoint the same way the runtime
    # streaming STT path does. lmstudio returns a 200-with-error-body when
    # you POST to the bare /v1 base URL, which would silently mask every
    # probe as a transient empty-text failure.
    if base_url_clean and not base_url_clean.rstrip("/").endswith("/chat/completions"):
        base_url_clean = base_url_clean.rstrip("/") + "/chat/completions"
    model_id = str(model or "").strip()
    resolved_api_env = resolve_stt_api_env(load_config(), base_url_clean, api_env)

    tmp_path: Optional[Path] = None
    started = time.time()
    try:
        import tempfile
        check_current(is_current)
        with tempfile.NamedTemporaryFile(prefix="korina-probe-", suffix=".wav", delete=False) as f:
            tmp_path = Path(f.name)
            f.write(_build_synthetic_wav())
        check_current(is_current)
        result = transcribe_fn(tmp_path, model=model_id, base_url=base_url_clean, api_env=resolved_api_env)
        check_current(is_current)
        text = str((result or {}).get("text") or "").strip()
        latency_ms = int((time.time() - started) * 1000)
        if text:
            check_current(is_current)
            clear_audio_unsupported(provider_id, base_url_clean, model_id, is_current=is_current)
            return ProbeResult(
                model=model_id,
                supported=True,
                reason="",
                latency_ms=latency_ms,
                status=200,
                body_excerpt=text[:200],
                error="",
            )
        # Match the live STT policy. Empty output can exhaust a reasoning
        # budget without proving audio incompatibility.
        reason_excerpt = str((result or {}).get("error") or "empty_text")
        finish_reason = str((result or {}).get('finish_reason') or '')
        body = json.dumps({'choices': [{'message': {'content': text}, 'finish_reason': finish_reason}]})
        unsupported, reason = classify_failure(200, body, finish_reason=finish_reason)
        if unsupported:
            check_current(is_current)
            set_audio_unsupported(provider_id, base_url_clean, model_id, reason, body, is_current=is_current)
        return ProbeResult(
            model=model_id,
            supported=False,  # empty 200 is a UI-visible red mark either way
            reason=reason if unsupported else "transient_empty_text",
            latency_ms=latency_ms,
            status=200,
            body_excerpt=reason_excerpt[:200],
            error=reason_excerpt if not unsupported else "",
        )
    except RequestCancelledError:
        raise
    except Exception as e:
        check_current(is_current)
        latency_ms = int((time.time() - started) * 1000)
        status, body = _classify_runtime_error(e)
        unsupported, reason = classify_failure(status, body)
        body_excerpt = body[:200]
        error = str(e)[:200]
        if unsupported:
            check_current(is_current)
            set_audio_unsupported(provider_id, base_url_clean, model_id, reason, body, is_current=is_current)
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
