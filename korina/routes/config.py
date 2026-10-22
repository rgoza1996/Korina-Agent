"""Read and persist settings; provider activation has its own explicit route."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from korina.config import load_config, update_config_values
from korina.settings_schema import SettingsUpdate


class _ConfigValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Raw JSON can contain NaN/Infinity. Echoing such an input in
                # FastAPI's default validation response fails JSON encoding.
                # Field location/type/message suffice and avoid echoing keys.
                detail = [{key: error[key] for key in ('type', 'loc', 'msg')}
                          for error in exc.errors()]
                return JSONResponse({'detail': detail}, status_code=422)

        return validated


router = APIRouter(route_class=_ConfigValidationRoute)


@router.get('/api/config')
def get_config():
    return load_config()


@router.post('/api/config')
def update_config(payload: SettingsUpdate):
    return update_config_values(payload.model_dump(exclude_unset=True))
