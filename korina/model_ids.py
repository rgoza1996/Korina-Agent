"""Small, bounded model catalogs shared by discovery and its HTTP surfaces."""
from __future__ import annotations

import json
from collections.abc import Iterable

MAX_MODEL_ID_CHARS = 512
MAX_MODEL_IDS = 512
MAX_MODEL_ID_BYTES = 256 * 1024
MAX_CATALOG_IDS = 2 * MAX_MODEL_IDS
MAX_CATALOG_ID_BYTES = 2 * MAX_MODEL_ID_BYTES


def normalize_model_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_MODEL_ID_CHARS:
        return None
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        return None
    return value


class ModelIds(list[str]):
    """Retain the historical list interface, with discovery diagnostics."""

    def __init__(self, values: Iterable[str] = (), *, info: dict | None = None):
        super().__init__(values)
        self.info = info or {}


def model_list_info(values: list[str]) -> dict:
    return dict(getattr(values, 'info', {}))


def limit_model_ids(values: Iterable[object], *, pinned: Iterable[object] = (),
                    max_ids: int = MAX_MODEL_IDS, max_bytes: int = MAX_MODEL_ID_BYTES,
                    sources: Iterable[list[str]] = ()) -> ModelIds:
    """Bound UTF-8 and JSON-escaped identifiers, retaining listed selected IDs.

    Pins are retained only when discovered; an unknown saved selection is not
    presented as an available provider model. Its picker keeps it separately.
    """
    pins = {mid for value in pinned if (mid := normalize_model_id(value)) is not None}
    seen: set[str] = set()
    chosen: list[str] = []
    sizes: dict[str, int] = {}
    invalid = duplicate = limited = 0
    byte_count = 0
    for value in values:
        mid = normalize_model_id(value)
        if mid is None:
            invalid += 1
            continue
        if mid in seen:
            duplicate += 1
            continue
        seen.add(mid)
        size = len(json.dumps(mid, ensure_ascii=False).encode('utf-8'))
        if len(chosen) >= max_ids or byte_count + size > max_bytes:
            if mid not in pins:
                limited += 1
                continue
            # At most the configured role selections are pinned. Never evict
            # another pin to make a catalog look more complete than it is.
            while chosen and (len(chosen) >= max_ids or byte_count + size > max_bytes):
                removable = next((index for index in range(len(chosen) - 1, -1, -1)
                                  if chosen[index] not in pins), None)
                if removable is None:
                    break
                removed = chosen.pop(removable)
                byte_count -= sizes.pop(removed)
                limited += 1
            if len(chosen) >= max_ids or byte_count + size > max_bytes:
                limited += 1
                continue
        chosen.append(mid)
        sizes[mid] = size
        byte_count += size
    info: dict = {'max_models': max_ids, 'max_identifier_bytes': max_bytes,
            'returned': len(chosen), 'identifier_bytes': byte_count,
            'skipped_invalid': invalid, 'skipped_duplicates': duplicate,
            'skipped_limit': limited, 'truncated': limited > 0}
    upstream = [model_list_info(source) for source in sources]
    for source in upstream:
        info['truncated'] = bool(info['truncated'] or source.get('truncated'))
        for key in ('skipped_invalid', 'skipped_duplicates', 'skipped_limit'):
            info[key] += int(source.get(key, 0))
    errors = [str(error)[:256] for source in upstream for error in source.get('errors', [])][:8]
    if errors:
        info['errors'] = errors
    if upstream:
        info['visited_entries'] = sum(int(source.get('visited_entries', 0)) for source in upstream)
    return ModelIds(chosen, info=info)
