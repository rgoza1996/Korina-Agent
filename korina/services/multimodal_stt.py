"""Multimodal STT (audio chat completions) helpers."""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from korina.config import (
    config_stt_llm_api_env,
    config_stt_llm_base_url,
    config_stt_llm_chat_url,
    config_stt_llm_model,
    config_stt_llm_reasoning,
    load_config,
)
from korina.runtime.http import auth_headers_from_env
from korina.services.model_catalog import llm_models_for


_TRANSIENT_WHISPER_FALLBACK_PATTERNS = [
    (re.compile(r"failed to load model", re.IGNORECASE), "model_load_failed"),
    (re.compile(r"model has crashed", re.IGNORECASE), "model_crashed"),
    (re.compile(r"empty_text", re.IGNORECASE), "empty_text"),
]


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


def lmstudio_transcribe_wav(wav: Path, *, model: Optional[str] = None, base_url: Optional[str] = None, api_env: Optional[str] = None) -> dict:
    """Attempt audio transcription through a multimodal STT model endpoint.

    OpenAI-compatible multimodal endpoints definitely support text/images. Audio input
    depends on the loaded model + server support, so this endpoint returns a clear
    error if the selected model rejects input_audio.

    Phase 5 (2026-06-27): the returned dict now carries ``finish_reason`` from
    the OpenAI chat-completion response. Callers in the audio-probe / stt route
    use this to classify silent-empty failures (HTTP 200 + empty content +
    finish_reason != 'stop') as structurally audio-incompatible.
    """
    config = load_config()
    resolved_base_url = str(base_url or config_stt_llm_chat_url(config)).strip()
    resolved_api_env = str(api_env or config_stt_llm_api_env(config) or '').strip()
    selected_model = (model or config_stt_llm_model(config)).strip()
    if not selected_model:
        available = llm_models_for(config_stt_llm_base_url(config), resolved_api_env)
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
    http_req = urllib.request.Request(
        resolved_base_url,
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(http_req, timeout=120) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(
            f'Multimodal STT model {selected_model!r} rejected audio transcription request: HTTP {e.code}: {detail}'
        )
    choice = (body.get('choices') or [{}])[0] or {}
    message = choice.get('message') or {}
    text = (message.get('content') or '').strip()
    finish_reason = str(choice.get('finish_reason') or '')
    return {
        'text': text,
        'finish_reason': finish_reason,
        'seconds': time.time() - started,
        'model': selected_model,
        'backend': 'multimodal-stt',
        'segments': [{'start': None, 'end': None, 'text': text}] if text else [],
    }