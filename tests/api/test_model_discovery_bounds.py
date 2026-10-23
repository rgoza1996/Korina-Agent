"""Provider input caps must also bound expanded model discovery responses."""
from __future__ import annotations

import json
import httpx
import pytest

from korina import config
from korina.model_ids import MAX_MODEL_ID_BYTES
from korina.routes import models


def test_real_transport_40000_models_is_bounded_before_metadata(client, monkeypatch, mock_provider_http):
    ids = [f'fixture-model-{index:05d}' for index in range(40000)]
    config.update_config_values({'lm_model': ids[-1], 'stt_llm_model': ids[-2]})
    mock_provider_http(lambda request: httpx.Response(200, content=json.dumps({'data': [{'id': mid} for mid in ids] + [{'id': 'x' * 513}, {'id': '\ud800'}]}).encode()))
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    response = client.get('/api/models')
    assert response.status_code == 200
    body = response.json()
    assert len(body['llm_models']) == len(body['stt_llm_models']) == 512
    assert ids[-1] in body['llm_models'] and ids[-2] in body['stt_llm_models']
    known = set(body['llm_models'] + body['stt_llm_models'])
    for field in ['labels', 'llm_models_capabilities', 'stt_llm_models_capabilities', 'llm_models_status', 'stt_llm_models_status']:
        assert set(body[field]) == known
    assert body['catalog_info']['response']['truncated']
    assert body['catalog_info']['response']['skipped_invalid'] == 2
    assert len(response.content) < 1024 * 1024


@pytest.mark.parametrize('character', ['x', '🙂', '\\'])
def test_worst_valid_identifier_payload_stays_below_browser_envelope(client, monkeypatch, character):
    # Distinct role lists plus repeated fallback pools maximize key/label and
    # unusable-reason duplication while retaining valid 512-character names.
    ids = [character * 505 + f'{index:07d}' for index in range(1200)]
    monkeypatch.setattr(models, 'llm_models_for', lambda base, env, **kwargs: ids if base.endswith('/one') else list(reversed(ids)))
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: ids)
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: list(reversed(ids)))
    response = client.get('/api/models', params={'llm_base_url': 'http://fixture/one', 'stt_llm_base_url': 'http://fixture/two',
                                                'llm_provider': 'llama.cpp', 'stt_llm_provider': 'llama.cpp'})
    assert response.status_code == 200
    body = response.json()
    assert len(response.content) < 8 * 1024 * 1024
    for key in ['response', 'stt', 'local_gguf', 'lmstudio']:
        assert body['catalog_info'][key]['returned'] <= 512
        assert body['catalog_info'][key]['identifier_bytes'] <= MAX_MODEL_ID_BYTES
    assert body['catalog_info']['aggregate']['identifier_bytes'] <= 2 * MAX_MODEL_ID_BYTES


def test_agent_catalog_filters_invalid_ids_and_reports_limits_without_saving(client, config_module, mock_provider_http):
    before = config_module.CONFIG_PATH.read_bytes() if config_module.CONFIG_PATH.exists() else None
    mock_provider_http(lambda request: httpx.Response(200, content=json.dumps({'data': [{'id': 'x' * 513}, {'id': '\ud800'}, *[{'id': f'model-{index}'} for index in range(2000)]]}).encode()))
    response = client.get('/api/agent/models')
    assert response.status_code == 200
    body = response.json()
    assert len(body['models']) == 512
    assert body['catalog_info']['truncated']
    assert body['catalog_info']['skipped_invalid'] == 2
    assert (config_module.CONFIG_PATH.read_bytes() if config_module.CONFIG_PATH.exists() else None) == before


def test_exhausted_metadata_scan_reports_incomplete_estimate_without_claiming_missing_mmproj(client, monkeypatch, tmp_path):
    from korina.services.model_metadata import ModelMetadataScan
    model = tmp_path / 'fixture.gguf'
    model.touch()
    (tmp_path / 'fixture-mmproj.gguf').touch()
    mid = str(model)
    monkeypatch.setattr(models, 'ModelMetadataScan', lambda: ModelMetadataScan(max_entries=0))
    monkeypatch.setattr(models, 'llm_models_for', lambda *args, **kwargs: [mid])
    monkeypatch.setattr(models, 'discover_local_gguf_models', lambda **kwargs: [])
    monkeypatch.setattr(models, 'discover_lmstudio_catalog_models', lambda **kwargs: [])
    body = client.get('/api/models').json()
    assert body['catalog_info']['metadata']['incomplete']
    assert body['catalog_info']['metadata']['visited_entries'] == 0
    assert body['stt_llm_models_capabilities'][mid]['mmproj_estimate_incomplete']
    reasons = ' '.join(body['stt_llm_models_status'][mid]['reasons'])
    assert 'estimate incomplete' in reasons
    assert 'missing mmproj' not in reasons
