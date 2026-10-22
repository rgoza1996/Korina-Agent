"""Read and persist settings; provider activation has its own explicit route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from korina import config as config_store
from korina.config import load_config, update_config_values
from korina.services.provider_manager import provider_activation_lock
from korina.settings_schema import SettingsUpdate
from korina.routes.validation import SanitizedValidationRoute


router = APIRouter(route_class=SanitizedValidationRoute)


@router.get('/api/config')
def get_config():
    return load_config()


@router.post('/api/config')
def update_config(payload: SettingsUpdate):
    # Acquire lifecycle ownership before entering the config transaction. When
    # busy, the transaction still permits unrelated or identical form values.
    acquired = provider_activation_lock.acquire(blocking=False)
    try:
        return update_config_values(payload.model_dump(exclude_unset=True), reject_role_changes=not acquired)
    except config_store.ProviderSettingsBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if acquired:
            provider_activation_lock.release()
