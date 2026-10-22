"""Catalog validation is bounded, deterministic, and never rewrites local data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from korina.services.ack_manifest import AckManifestError, MAX_MANIFEST_BYTES, read_ack_manifest


@pytest.mark.parametrize('payload', [
    None, {}, {'phrases': 'wrong'}, {'phrases': [7]}, [7], [{'text': 'Not legacy strings'}],
    {'phrases': [{'text': 'Okay.', 'tags': 7}]},
    {'phrases': [{'text': 'Okay.', 'tags': [7]}]},
    {'phrases': [{'text': 'Okay.', 'tags': []}]},
    {'phrases': [{'text': 'Okay.', 'tags': ['a'] * 9}]},
    {'phrases': [{'text': 'Okay.', 'tags': ['a' * 33]}]},
    {'phrases': [{'text': 'Okay.', 'id': 7}]},
    {'phrases': [{'text': 'Okay.', 'id': 'i' * 65}]},
    {'phrases': [{'text': 'x' * 513}]}, {'phrases': ['']}, {'phrases': ['\ud800']},
    {'phrases': ['Okay.'] * 257},
    {'phrases': [{'id': 'Same!', 'text': 'One.'}, {'id': 'same?', 'text': 'Two.'}]},
])
def test_invalid_catalog_rejects_complete_manifest_without_echoing_values(tmp_path, payload):
    path = tmp_path / 'ack_phrases.json'
    raw = json.dumps(payload)
    path.write_text(raw)
    with pytest.raises(AckManifestError, match=r'Repair Ack/ack_phrases.json and retry') as caught:
        read_ack_manifest(path)
    assert len(str(caught.value)) < 256
    assert path.read_text() == raw


@pytest.mark.parametrize('raw', [b'{', b'\xff', b'{"version":NaN,"phrases":[]}', b' ' * (MAX_MANIFEST_BYTES + 1)])
def test_parse_and_byte_limits_are_actionable(tmp_path, raw):
    path = tmp_path / 'ack_phrases.json'
    path.write_bytes(raw)
    with pytest.raises(AckManifestError):
        read_ack_manifest(path)


def test_missing_or_unreadable_manifest_is_explicit(tmp_path):
    for path in (tmp_path / 'missing.json', tmp_path):
        with pytest.raises(AckManifestError, match='missing or unreadable'):
            read_ack_manifest(path)


def test_legacy_and_tagged_catalogs_keep_compatible_names(tmp_path):
    path = tmp_path / 'ack_phrases.json'
    path.write_text(json.dumps(['Okay.', 'I hear you.']))
    assert read_ack_manifest(path) == [
        {'id': 'ack_01', 'text': 'Okay.', 'tags': ['global']},
        {'id': 'ack_02', 'text': 'I hear you.', 'tags': ['global']},
    ]
    path.write_text(json.dumps({'phrases': [{'id': 'Got It!', 'text': 'Okay.', 'tags': 'Thinking'}]}))
    assert read_ack_manifest(path) == [{'id': 'got_it', 'text': 'Okay.', 'tags': ['thinking']}]
    path.write_text('{"phrases": []}')
    assert read_ack_manifest(path) == []  # an explicit empty catalog is valid


def test_tracked_catalog_is_valid():
    path = Path(__file__).resolve().parents[2] / 'Korina/Ack/ack_phrases.json'
    assert len(read_ack_manifest(path)) == 28
