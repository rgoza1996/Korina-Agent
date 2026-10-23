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


MAX_USAGE_TOKENS = 2**53 - 1  # Preserve exact integer values in the browser.


@dataclass(frozen=True)
class CompletionUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


def openai_completion_usage(body: object) -> CompletionUsage | None:
    """Read optional observations without making malformed usage a reply error.

    Missing counts remain unavailable. Unknown provider fields are ignored;
    malformed known fields or contradictory breakdowns discard the observation.
    These counts are not cumulative usage or a billing estimate.
    """
    if not isinstance(body, dict) or body.get('usage') is None:
        return None
    usage = body['usage']
    if not isinstance(usage, dict):
        return None

    def count(value: object) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 0 <= value <= MAX_USAGE_TOKENS:
            raise ValueError('Invalid token count')
        return value

    def detail(parent: str, key: str) -> int | None:
        value = usage.get(parent)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError('Invalid token details')
        return count(value.get(key))

    try:
        parsed = CompletionUsage(
            prompt_tokens=count(usage.get('prompt_tokens')),
            completion_tokens=count(usage.get('completion_tokens')),
            reasoning_tokens=detail('completion_tokens_details', 'reasoning_tokens'),
            cached_tokens=detail('prompt_tokens_details', 'cached_tokens'),
        )
    except ValueError:
        return None
    if (parsed.prompt_tokens is not None and parsed.cached_tokens is not None
            and parsed.cached_tokens > parsed.prompt_tokens):
        return None
    if (parsed.completion_tokens is not None and parsed.reasoning_tokens is not None
            and parsed.reasoning_tokens > parsed.completion_tokens):
        return None
    if all(value is None for value in (
        parsed.prompt_tokens, parsed.completion_tokens, parsed.reasoning_tokens, parsed.cached_tokens,
    )):
        return None
    return parsed


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
