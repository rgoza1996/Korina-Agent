from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from korina.config import (
    config_llm_chat_url,
    config_llm_api_env,
    config_llm_max_tokens,
    config_llm_reasoning,
    config_llm_reasoning_effort,
    load_config,
)

from korina.runtime.http import auth_headers_from_env
from korina.runtime.json_http import HTTPStatusError, RequestCancelledError, request_json
from korina.runtime.completion import CompletionUsage, completion_text, openai_completion_usage, openai_text_completion

from korina.schemas import ChatRequest

from korina.util.paths import LMSTUDIO_MODEL


@dataclass(frozen=True)
class ResponseRequestSettings:
    provider: str
    model: str
    max_tokens: int
    reasoning_effort: str | None
    reasoning: str | None


@dataclass(frozen=True)
class ResponseChatResult:
    reply: str
    usage: CompletionUsage | None
    request_settings: ResponseRequestSettings


def format_voice_reply(text: str) -> str:
    """Make replies more TTS/chunk-friendly: one short sentence per line."""
    text = (text or '').strip()
    if not text:
        return text
    # Preserve deliberate newlines, but split any multi-sentence line.
    lines: list[str] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = re.split(r'(?<=[.!?…])\s+', raw)
        lines.extend(p.strip() for p in parts if p.strip())
    return '\n'.join(lines)

def response_llm_chat(req: ChatRequest, *, is_current: Callable[[], bool] | None = None) -> str:
    """Return only the spoken text for existing direct service callers."""
    return response_llm_chat_result(req, is_current=is_current).reply


def response_llm_chat_result(req: ChatRequest, *, is_current: Callable[[], bool] | None = None) -> ResponseChatResult:
    """Keep optional usage and labels tied to the same outbound request."""
    system = req.system or (
        'You are Korina, a concise real-time voice conversation assistant.\n'
        'Speak in short, natural sentences that are suitable for being heard aloud.\n'
        'Put each sentence on its own new line. This is important because the TTS chunker uses new lines as clean break points.\n'
        'Prefer 1 to 3 short sentences unless the user explicitly asks for detail.\n'
        'Avoid markdown tables, bullets, long paragraphs, emojis, emoticons, kaomoji, symbols used as decoration, and stage directions.\n'
        'Never output emoji characters. Use plain words only, because replies are spoken aloud by TTS.\n'
        'If the user interrupts you mid-speech, treat that interruption as intentional.\n'
        'Use the provided interruption context to know roughly what you had already said and where the user cut in.\n'
        'After an interruption, respond to the latest user message rather than continuing your previous answer.'
    )
    messages = [{'role': 'system', 'content': system}]
    for m in req.history[-12:]:
        role = m.get('role')
        content = m.get('content')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            messages.append({'role': role, 'content': content.strip()})
    messages.append({'role': 'user', 'content': req.message.strip()})

    config = load_config()
    provider = str(config.get('llm_provider') or 'openai-compatible').strip().lower()
    is_meta = provider == 'meta'
    settings = ResponseRequestSettings(
        provider=provider,
        model=req.model or str(config.get('lm_model') or LMSTUDIO_MODEL),
        max_tokens=req.max_tokens if 'max_tokens' in req.model_fields_set else config_llm_max_tokens(config),
        reasoning_effort=(req.reasoning_effort if 'reasoning_effort' in req.model_fields_set
                          else config_llm_reasoning_effort(config)) if is_meta else None,
        reasoning=None if is_meta else (
            str(req.reasoning).strip().lower() if req.reasoning else config_llm_reasoning(config)
        ),
    )
    payload = {
        'model': settings.model,
        'messages': messages,
        'temperature': req.temperature,
        'max_tokens': settings.max_tokens,
        'stream': False,
    }
    # Meta's Chat Completions API reasons by default. Its documented control
    # is reasoning_effort; the local/generic on/off extension is not sent.
    if is_meta:
        if settings.reasoning_effort:
            payload['reasoning_effort'] = settings.reasoning_effort
    else:
        payload['reasoning'] = settings.reasoning
    headers = {'Content-Type': 'application/json'} | auth_headers_from_env(config_llm_api_env(config))
    try:
        body = request_json(config_llm_chat_url(config), method='POST', headers=headers, json_body=payload, timeout=90, is_current=is_current)
    except RequestCancelledError:
        raise
    except HTTPStatusError as e:
        raise RuntimeError(f'Response LLM HTTP {e.status_code}: {e.detail}') from e
    except Exception as e:
        raise RuntimeError(f'Response LLM request failed: {e}')

    completion = openai_text_completion(body)
    if not completion.text and completion.finish_reason == 'length':
        raise RuntimeError(
            'Response model exhausted its output-token budget before answering. '
            'Increase Response token budget in Settings or choose another response model.'
        )
    reply = completion.text
    # Sentence splitting can add newlines; validate the final history value.
    return ResponseChatResult(
        reply=completion_text(format_voice_reply(reply), label='Response LLM'),
        usage=openai_completion_usage(body),
        request_settings=settings,
    )
