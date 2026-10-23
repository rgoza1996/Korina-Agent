"""Display-side mmproj estimates share a bounded per-request directory scan."""
from __future__ import annotations

import os
from pathlib import Path

from korina.services.model_capability import get_model_capability
from korina.services.model_metadata import ModelMetadataScan


def test_same_large_folder_is_scanned_once_for_512_models(monkeypatch, tmp_path):
    model_ids = []
    for index in range(512):
        model = tmp_path / f'fixture-{index:04d}.gguf'
        model.touch()
        model_ids.append(str(model))
    for index in range(2000):
        (tmp_path / f'sidecar-{index:04d}-mmproj.gguf').touch()
    real_scandir = os.scandir
    real_glob = Path.glob
    calls = {'scandir': 0, 'glob': 0}

    def scandir(path):
        calls['scandir'] += 1
        return real_scandir(path)

    def glob(self, pattern, *args, **kwargs):
        calls['glob'] += 1
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(os, 'scandir', scandir)
    monkeypatch.setattr(Path, 'glob', glob)
    scan = ModelMetadataScan()
    capabilities = [get_model_capability(mid, mmproj_lookup=scan.has_mmproj) for mid in model_ids]
    assert all(cap['has_mmproj'] for cap in capabilities)
    assert calls == {'scandir': 1, 'glob': 0}
    assert scan.info['visited_entries'] <= 2512
    assert scan.info['directories_scanned'] == 1
    assert not scan.info['incomplete']


def test_budget_stops_actual_iteration_and_negative_cache_does_not_reopen_directory(monkeypatch, tmp_path):
    from types import SimpleNamespace
    model = tmp_path / 'fixture.gguf'
    model.touch()
    counts = {'next': 0, 'closed': 0}

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            counts['closed'] += 1

        def __next__(self):
            counts['next'] += 1
            return SimpleNamespace(name='ordinary-file', is_file=lambda: True)

    monkeypatch.setattr(os, 'scandir', lambda path: Entries())
    scan = ModelMetadataScan(max_entries=12)
    assert scan.has_mmproj(str(model)) is False
    assert scan.has_mmproj(str(model)) is False
    assert counts == {'next': 12, 'closed': 1}
    assert scan.info['visited_entries'] == 12
    assert scan.info['truncated'] and scan.info['incomplete']
    assert str(model) in scan.incomplete_models


def test_filesystem_error_is_an_incomplete_estimate_not_confirmed_absence(monkeypatch, tmp_path):
    model = tmp_path / 'fixture.gguf'
    model.touch()
    monkeypatch.setattr(os, 'scandir', lambda path: (_ for _ in ()).throw(PermissionError('denied')))
    scan = ModelMetadataScan()
    assert scan.has_mmproj(str(model)) is False
    assert scan.info['incomplete']
    assert scan.info['errors'] == ['Model metadata unavailable (PermissionError)']
    assert str(model) in scan.incomplete_models


def test_fresh_request_refreshes_sidecar_cache_and_activation_finder_is_independent(tmp_path):
    from korina.services.provider_manager import find_mmproj_for_model
    model = tmp_path / 'fixture.gguf'
    model.touch()
    first = ModelMetadataScan()
    assert first.has_mmproj(str(model)) is False
    sidecar = tmp_path / 'fixture-mmproj.gguf'
    sidecar.touch()
    assert ModelMetadataScan().has_mmproj(str(model)) is True
    exhausted = ModelMetadataScan(max_entries=0)
    assert exhausted.has_mmproj(str(model)) is False
    assert exhausted.info['incomplete']
    assert find_mmproj_for_model(str(model)) == str(sidecar)
