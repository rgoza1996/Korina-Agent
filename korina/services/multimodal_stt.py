"""Multimodal STT (audio chat completions) helpers."""

from __future__ import annotations

import base64
import math
import re
import time
from pathlib import Path
from typing import Optional

from korina.config import (
    config_llm_base_url,
    config_stt_llm_api_env,
    config_stt_llm_base_url,
    config_stt_llm_chat_url,
    config_stt_llm_model,
    config_stt_llm_reasoning,
    load_config,
    normalize_api_base_url,
)
from korina.runtime.http import auth_headers_from_env
from korina.runtime.json_http import HTTPStatusError, request_json
from korina.runtime.completion import openai_text_completion
from korina.services.model_catalog import llm_models_for


_TRANSIENT_WHISPER_FALLBACK_PATTERNS = [
    (re.compile(r"failed to load model", re.IGNORECASE), "model_load_failed"),
    (re.compile(r"model has crashed", re.IGNORECASE), "model_crashed"),
    (re.compile(r"empty_text", re.IGNORECASE), "empty_text"),
]


class MultimodalSttError(RuntimeError):
    """Keep provider status/detail available to the shared fallback policy."""

    def __init__(self, model: str, status_code: int, detail: str):
        self.model, self.status_code, self.detail = model, status_code, detail
        super().__init__(
            f'Multimodal STT model {model!r} rejected audio transcription request: HTTP {status_code}: {detail}'
        )


def transient_whisper_fallback_reason(detail: str) -> str | None:
    """Return a non-cacheable Whisper-fallback reason for transient multimodal failures.

    These are not "audio unsupported forever" signals, so they should not be
    written into config.audio_unsupported. They *should* still degrade live STT
    back to Whisper so the conversation keeps moving.
    """
    detail = str(detail or '').strip()
    if not detail:
        return None
    for pat, reason in _TRANSIENT_WHISPER_FALLBACK_PATTERNS:
        if pat.search(detail):
            return reason
    return None


def lmstudio_models() -> list[str]:
    config = load_config()
    return llm_models_for(config_llm_base_url(config), str(config.get('llm_api_key_env') or ''))


def resolve_stt_api_env(config: dict, base_url: str, api_env: str | None) -> str:
    """None inherits credentials only for the configured endpoint; empty disables.

    A candidate probe must never receive another endpoint's key merely because
    its caller omitted an auth setting. Explicit env names remain opt-in.
    """
    if api_env is not None:
        return str(api_env).strip()
    if normalize_api_base_url(base_url) == normalize_api_base_url(config_stt_llm_base_url(config)):
        return config_stt_llm_api_env(config)
    return ''


def lmstudio_transcribe_wav(wav: Path, *, model: Optional[str] = None, base_url: Optional[str] = None, api_env: Optional[str] = None, timeout: float = 120.0) -> dict:
    """Attempt audio transcription through a multimodal STT model endpoint.

    OpenAI-compatible multimodal endpoints definitely support text/images. Audio input
    depends on the loaded model + server support, so this endpoint returns a clear
    error if the selected model rejects input_audio. None auth inherits only
    for the configured STT endpoint; an empty env name disables auth. timeout
    applies to the HTTP call and is bounded to (0, 120] seconds.

    Phase 5 (2026-06-27): the returned dict now carries ``finish_reason`` from
    the OpenAI chat-completion response. Callers in the audio-probe / stt route
    use this to classify silent-empty failures (HTTP 200 + empty content +
    finish_reason != 'stop') as structurally audio-incompatible.
    """
    config = load_config()
    resolved_base_url = str(base_url if base_url is not None else config_stt_llm_chat_url(config)).strip()
    from urllib.parse import urlsplit
    parsed = urlsplit(resolved_base_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Multimodal STT requires an http(s) endpoint')
    timeout = float(timeout)
    if not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError('Multimodal STT timeout must be greater than zero and at most 120 seconds')
    resolved_api_env = resolve_stt_api_env(config, resolved_base_url, api_env)
    selected_model = (model or config_stt_llm_model(config)).strip()
    if not selected_model:
        available = llm_models_for(normalize_api_base_url(resolved_base_url), resolved_api_env)
        selected_model = available[0].strip() if available else ''
    if not selected_model:
        raise RuntimeError('No multimodal STT model available for the selected endpoint')
    data = wav.read_bytes()
    b64 = base64.b64encode(data).decode('ascii')
    started = time.time()
    payload = {
        'model': selected_model,
        'messages': [
            {
                'role': 'system',
                'content': 'You are a strict speech-to-text engine. Return only the exact transcript. No commentary.',
            },
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Transcribe this audio exactly. Return only the spoken words.'},
                    {'type': 'input_audio', 'input_audio': {'data': b64, 'format': 'wav'}},
                ],
            },
        ],
        'temperature': 0,
        'max_tokens': 512,
        'stream': False,
        'reasoning': config_stt_llm_reasoning(config),
    }
    headers = {'Content-Type': 'application/json'} | auth_headers_from_env(resolved_api_env)
    try:
        body = request_json(resolved_base_url, method='POST', headers=headers, json_body=payload, timeout=timeout)
    except HTTPStatusError as e:
        raise MultimodalSttError(selected_model, e.status_code, e.detail) from e
    completion = openai_text_completion(body)
    text, finish_reason = completion.text, completion.finish_reason
    return {
        'text': text,
        'finish_reason': finish_reason,
        'seconds': time.time() - started,
        'model': selected_model,
        'backend': 'multimodal-stt',
        'segments': [{'start': None, 'end': None, 'text': text}] if text else [],
    }
