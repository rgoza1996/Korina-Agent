"""Validate provider text before it enters conversation history or agent state."""
from __future__ import annotations

from dataclasses import dataclass

from korina.schemas import MAX_TEXT_LENGTH


class InvalidCompletionError(RuntimeError):
    """A successful upstream HTTP response did not contain usable text data."""


def completion_text(value: object, *, label: str = 'Completion') -> str:
    if not isinstance(value, str):
        raise InvalidCompletionError(f'{label} text must be a string')
    value = value.strip()
    if len(value) > MAX_TEXT_LENGTH:
        raise InvalidCompletionError(f'{label} text exceeds {MAX_TEXT_LENGTH} characters')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise InvalidCompletionError(f'{label} text contains invalid Unicode') from exc
    return value


@dataclass(frozen=True)
class TextCompletion:
    text: str
    finish_reason: str = ''


def openai_text_completion(body: object) -> TextCompletion:
    """Read the first text choice without embedding provider data in errors.

    Null content remains an empty completion, as used by silent audio and
    providers that return no visible text. Other falsy values are malformed.
    """
    if not isinstance(body, dict):
        raise InvalidCompletionError('Completion response must be an object')
    choices = body.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise InvalidCompletionError('Completion response must contain a first choice')
    choice = choices[0]
    message = choice.get('message')
    if not isinstance(message, dict) or 'content' not in message:
        raise InvalidCompletionError('Completion choice must contain message.content')
    content = message['content']
    text = completion_text('' if content is None else content)
    finish_reason = choice.get('finish_reason')
    if finish_reason is None:
        finish_reason = ''
    if not isinstance(finish_reason, str) or len(finish_reason) > 64:
        raise InvalidCompletionError('Completion finish_reason must be a short string or null')
    finish_reason = completion_text(finish_reason, label='Completion finish_reason')
    return TextCompletion(text, finish_reason)


def anthropic_text_completion(body: object) -> str:
    """Join visible text blocks; thinking/tool blocks are not spoken reports."""
    if not isinstance(body, dict) or not isinstance(body.get('content'), list):
        raise InvalidCompletionError('Agent response must contain content blocks')
    parts: list[str] = []
    length = 0
    for block in body['content']:
        if not isinstance(block, dict) or not isinstance(block.get('type'), str):
            raise InvalidCompletionError('Agent content blocks must have a type')
        if block['type'] != 'text':
            continue
        text = block.get('text')
        if not isinstance(text, str):
            raise InvalidCompletionError('Agent text block must contain text')
        length += len(text)
        if length > MAX_TEXT_LENGTH:
            raise InvalidCompletionError(f'Agent text exceeds {MAX_TEXT_LENGTH} characters')
        parts.append(text)
    return completion_text(''.join(parts), label='Agent')
