"""Validation responses that can encode malformed/non-finite JSON inputs."""
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


def _json_safe(value):
    if isinstance(value, str):
        return value.encode('utf-8', errors='backslashreplace').decode('utf-8')
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {_json_safe(key): _json_safe(item) for key, item in value.items()}
    return value


async def sanitized_validation_error(request: Request, exc: Exception):
    assert isinstance(exc, RequestValidationError)
    # Raw input can contain keys, non-finite numbers or unpaired Unicode.
    # Retain only the useful diagnostic and make its property names safe too.
    detail = [_json_safe({key: error[key] for key in ('type', 'loc', 'msg')})
              for error in exc.errors()]
    return JSONResponse({'detail': detail}, status_code=422)


class SanitizedValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return await sanitized_validation_error(request, exc)

        return validated
