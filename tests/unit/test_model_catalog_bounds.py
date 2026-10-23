"""Catalog limits apply before metadata construction and native option work."""
from __future__ import annotations

import sys

from korina.model_ids import MAX_MODEL_ID_BYTES, limit_model_ids
from korina.services.agent_service import parse_model_ids


def test_model_ids_trim_deduplicate_reject_invalid_and_pin_listed_selection():
    ids = [f'model-{index}' for index in range(40000)]
    result = limit_model_ids([' x ', 'x', '', None, '\ud800', 'z' * 513, *ids], pinned=['model-39999', 'unknown'])
    assert len(result) == 512
    assert 'model-39999' in result
    assert 'unknown' not in result
    assert result[0] == 'x'
    assert result.info['truncated'] is True
    assert result.info['skipped_invalid'] == 4
    assert result.info['skipped_duplicates'] == 1


def test_identifier_budget_counts_utf8_and_json_escaping():
    for character in ['🙂', '\\', 'x']:
        result = limit_model_ids([character * 500 + str(index) for index in range(1000)], pinned=[character * 500 + '999'])
        assert result.info['identifier_bytes'] <= MAX_MODEL_ID_BYTES
        assert result.info['truncated']
        assert character * 500 + '999' in result


def test_agent_parser_has_same_limits_and_handles_malformed_provider_shapes():
    result = parse_model_ids({'data': [{'id': f'model-{index}'} for index in range(40000)]}, pinned=['model-39999'])
    assert len(result) == 512
    assert 'model-39999' in result
    assert result.info['truncated']
    assert parse_model_ids({'data': None}) == []
    assert parse_model_ids({'data': ['x' * 513, '\ud800', ' ok ', {'id': 'ok'}]}) == ['ok']


def test_local_walk_stops_at_result_budget_and_retains_explicit_selected_file(monkeypatch, model_root):
    from korina.services import model_catalog
    for index in range(600):
        (model_root / f'model-{index:04d}.gguf').touch()
    selected = model_root / 'model-0599.gguf'
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [model_root])
    found = model_catalog.discover_local_gguf_models(pinned=[str(selected)])
    assert len(found) == 512
    assert str(selected) in found
    assert found.info['truncated']
    assert found.info['visited_entries'] <= 513


def test_local_walk_bounds_non_model_entries_and_does_not_follow_directory_symlinks(monkeypatch, model_root, tmp_path):
    from korina.services import model_catalog
    for index in range(100):
        (model_root / f'other-{index}').touch()
    outside = tmp_path / 'outside'
    outside.mkdir()
    selected = outside / 'selected.gguf'
    selected.touch()
    (model_root / 'linked').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(model_catalog, 'local_model_roots', lambda: [model_root])
    monkeypatch.setattr(model_catalog, 'MAX_CATALOG_ENTRIES', 12)
    found = model_catalog.discover_local_gguf_models(pinned=[str(selected)])
    assert found == [str(selected)]
    assert found.info['visited_entries'] == 12
    assert found.info['truncated']


def test_lms_cli_output_is_bounded_before_json_parse(monkeypatch, tmp_path):
    from korina.services import model_catalog
    command = tmp_path / 'lms'
    command.write_text(f'#!{sys.executable}\nprint("x" * (3 * 1024 * 1024))\n')
    command.chmod(0o755)
    monkeypatch.setattr(model_catalog, 'LMS_CLI_BIN', command)
    found = model_catalog.discover_lmstudio_local_models()
    assert found == []
    assert found.info['errors'] == ['LM Studio catalog unavailable (ValueError)']


def test_selected_exact_hub_manifest_is_checked_independently_of_walk_budget(monkeypatch, lmstudio_root):
    from korina.services import model_catalog
    manifest = lmstudio_root / 'owner' / 'selected' / 'manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"owner":"owner","name":"selected"}')
    monkeypatch.setattr(model_catalog, 'MAX_CATALOG_ENTRIES', 1)
    found = model_catalog.discover_lmstudio_hub_manifests(pinned=['owner/selected', '../outside'])
    assert found == ['owner/selected']
    assert found.info['truncated']
    manifest.write_text('{"owner":"other","name":"model"}')
    assert 'owner/selected' not in model_catalog.discover_lmstudio_hub_manifests(pinned=['owner/selected'])
