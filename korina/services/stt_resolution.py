"""One multimodal capability-cache and Whisper-fallback policy for STT."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from korina.config import (
    config_stt_llm_api_env, config_stt_llm_base_url, config_stt_llm_chat_url,
    config_stt_llm_model, load_config,
)
from korina.services.audio_probe import maybe_fallback_to_whisper
from korina.services.multimodal_stt import MultimodalSttError, transient_whisper_fallback_reason
from korina.runtime.json_http import RequestCancelledError
from korina.runtime.request_owner import check_current


def resolve_multimodal_transcription(
    wav: Path, *, model: str | None, fallback: Callable[[], dict],
    transcribe: Callable, config: dict | None = None,
    is_current: Callable[[], bool] | None = None,
) -> dict:
    check_current(is_current)
    config = config if config is not None else load_config()
    provider = config.get('stt_llm_provider') or config.get('llm_provider') or 'llama.cpp'
    base = config_stt_llm_base_url(config)
    selected = str(model or config_stt_llm_model(config) or '').strip()

    def probe(status=0, body='', finish_reason=''):
        check_current(is_current)
        return maybe_fallback_to_whisper(
            provider=provider, base_url=base, model=selected,
            stt_status_code=status, stt_response_body=body,
            whisper_fallback_fn=owned_fallback, finish_reason=finish_reason,
        )

    def owned_fallback():
        check_current(is_current)
        result = fallback()
        check_current(is_current)
        return result

    # Without a selected ID, the provider discovers its model during the call.
    # An old empty-key cache entry must not cover every model on that endpoint.
    if selected:
        used, payload = probe()
        if used:
            return payload
    try:
        result = transcribe(
            wav, model=selected or None, base_url=config_stt_llm_chat_url(config),
            api_env=config_stt_llm_api_env(config),
            is_current=is_current,
        )
        check_current(is_current)
    except RequestCancelledError:
        raise
    except MultimodalSttError as exc:
        selected = selected or exc.model
        used, payload = probe(exc.status_code, exc.detail)
        if used:
            return payload
        return {**owned_fallback(), 'warning': str(exc), 'fallback_reason': 'multimodal_call_failed'}
    except Exception as exc:
        return {**owned_fallback(), 'warning': str(exc), 'fallback_reason': 'multimodal_call_failed'}
    selected = str(result.get('model') or selected)
    text = str(result.get('text') or '').strip()
    if not text:
        reason = str(result.get('finish_reason') or '')
        body = json.dumps({'choices': [{'finish_reason': reason, 'message': {'content': text}}]})
        used, payload = probe(200, body, reason)
        if used:
            return payload
        detail = result.get('error') or 'empty_text'
        fallback_reason = transient_whisper_fallback_reason(detail)
        if fallback_reason:
            return {**owned_fallback(), 'warning': detail, 'fallback_reason': fallback_reason}
    return {**result, 'text': text}
