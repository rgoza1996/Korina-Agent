"""Bounded validation of the optional local acknowledgement catalog."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from korina.util.labels import safe_slug

MAX_MANIFEST_BYTES = 64 * 1024
MAX_PHRASES = 256
MAX_TEXT_CHARS = 512
MAX_ID_CHARS = 64
MAX_TAGS = 8
MAX_TAG_CHARS = 32


class AckPhrase(TypedDict):
    id: str
    text: str
    tags: list[str]


class AckManifestError(ValueError):
    """A local catalog needs repair before ACK generation can run."""


def _invalid(reason: str) -> AckManifestError:
    return AckManifestError(f'ACK manifest unavailable: {reason}. Repair Ack/ack_phrases.json and retry.')


def _text(value: object, limit: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _invalid(f'{field} must be a nonempty string of at most {limit} characters')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise _invalid(f'{field} must contain valid Unicode') from None
    return value.strip()


def _reject_constant(value: str):
    raise ValueError('JSON numbers must be finite')


def read_ack_manifest(path: Path) -> list[AckPhrase]:
    try:
        with path.open('rb') as source:
            raw = source.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        raise _invalid('the file is missing or unreadable') from None
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _invalid(f'the file exceeds {MAX_MANIFEST_BYTES} bytes')
    try:
        data = json.loads(raw.decode('utf-8'), parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise _invalid('the file must contain valid UTF-8 JSON') from None
    legacy = isinstance(data, list)
    phrases = data if legacy else data.get('phrases') if isinstance(data, dict) else None
    if not isinstance(phrases, list) or len(phrases) > MAX_PHRASES:
        raise _invalid(f'phrases must be a list of at most {MAX_PHRASES} entries')
    result: list[AckPhrase] = []
    seen: set[str] = set()
    for index, item in enumerate(phrases, 1):
        field = f'phrase {index}'
        if isinstance(item, str):
            text = _text(item, MAX_TEXT_CHARS, f'{field} text')
            phrase_id, tags = f'ack_{index:02d}', ['global']
        elif isinstance(item, dict) and not legacy:
            text = _text(item.get('text'), MAX_TEXT_CHARS, f'{field} text')
            raw_id = item.get('id')
            phrase_id = safe_slug(_text(raw_id, MAX_ID_CHARS, f'{field} id')) if raw_id is not None else safe_slug(text)[:MAX_ID_CHARS]
            raw_tags = item.get('tags', ['global'])
            if isinstance(raw_tags, str):
                raw_tags = [raw_tags]
            if not isinstance(raw_tags, list) or not 1 <= len(raw_tags) <= MAX_TAGS:
                raise _invalid(f'{field} tags must contain one to {MAX_TAGS} strings')
            tags = list(dict.fromkeys(safe_slug(_text(tag, MAX_TAG_CHARS, f'{field} tag')) for tag in raw_tags))
        else:
            raise _invalid(f'{field} must be a string or a tagged phrase object')
        if phrase_id in seen:
            raise _invalid(f'{field} has a duplicate normalized id')
        seen.add(phrase_id)
        result.append({'id': phrase_id, 'text': text, 'tags': tags})
    return result
