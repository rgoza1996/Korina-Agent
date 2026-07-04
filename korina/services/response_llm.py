from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from korina.config import (
    config_llm_chat_url,
    config_llm_reasoning,
    load_config,
)

from korina.runtime.http import auth_headers_from_env

from korina.schemas import ChatRequest

from korina.util.paths import LMSTUDIO_MODEL


def format_voice_reply(text: str) -> str:
    """Make replies more TTS/chunk-friendly: one short sentence per line."""
    text = (text or '').strip()
    if not text:
        return text
    # Preserve deliberate newlines, but split any multi-sentence line.
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = re.split(r'(?<=[.!?…])\s+', raw)
        lines.extend(p.strip() for p in parts if p.strip())
    return '\n'.join(lines)

def response_llm_chat(req: ChatRequest) -> str:
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
    payload = {
        'model': (req.model or str(config.get('lm_model') or LMSTUDIO_MODEL)),
        'messages': messages,
        'temperature': req.temperature,
        'max_tokens': req.max_tokens,
        'stream': False,
        'reasoning': (str(req.reasoning).strip().lower() if req.reasoning else config_llm_reasoning(config)),
    }
    data = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json'} | auth_headers_from_env(str(config.get('llm_api_key_env') or ''))
    http_req = urllib.request.Request(
        config_llm_chat_url(config),
        data=data,
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(http_req, timeout=90) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Response LLM HTTP {e.code}: {detail}')
    except Exception as e:
        raise RuntimeError(f'Response LLM request failed: {e}')

    try:
        return format_voice_reply(body['choices'][0]['message']['content'] or '')
    except Exception:
        raise RuntimeError(f'Unexpected response LLM payload: {body!r}')
