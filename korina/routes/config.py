"""Read and persist settings; provider activation has its own explicit route."""
from __future__ import annotations

from fastapi import APIRouter

from korina.config import load_config, update_config_values

router = APIRouter()


@router.get('/api/config')
def get_config():
    return load_config()


@router.post('/api/config')
def update_config(payload: dict):
    return update_config_values(payload)
