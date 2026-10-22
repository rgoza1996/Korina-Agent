"""Read-only HTTP smoke gate for a running Korina demo stack.

Usage: python scripts/check_stack.py [--base URL] [--allow-degraded]
Unlike the legacy regression suite, this never resets sessions, edits config,
switches providers, or sends prompts/audio to a model. This script needs only
Python's standard library. Its socket timeout measures inactivity; the live
workflow supplies the hard overall time limit.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import urllib.error
import urllib.request

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
REQUIRED_CHECKS = {'frontend', 'response_llm', 'stt', 'tts', 'agent'}
MODEL_CHECKS = {'response_llm', 'stt', 'tts', 'agent'}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib otherwise reads whole redirect bodies before following them.
        # A monitor should verify the selected canonical stack URL directly.
        return None


_open_url = urllib.request.build_opener(_NoRedirect()).open


def _read_bounded(response) -> bytes:
    length = response.headers.get('Content-Length')
    declared = None
    if length is not None:
        try:
            declared = int(length)
        except ValueError:
            raise ValueError('invalid response Content-Length') from None
        if declared < 0 or declared > MAX_RESPONSE_BYTES:
            raise ValueError(f'response exceeds {MAX_RESPONSE_BYTES} bytes or has invalid length')
    body = bytearray()
    while True:
        chunk = response.read(min(READ_CHUNK_BYTES, MAX_RESPONSE_BYTES - len(body) + 1))
        if not chunk:
            if declared is not None and len(body) != declared:
                raise ValueError('response ended before its declared Content-Length')
            return bytes(body)
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            raise ValueError(f'response exceeds {MAX_RESPONSE_BYTES} bytes')
        body.extend(chunk)


def _invalid_constant(value: str):
    raise ValueError(f'invalid JSON numeric constant: {value}')


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('JSON number exceeds the supported finite range')
    return result


def _json_object(raw: bytes) -> dict:
    try:
        body = json.loads(raw, parse_constant=_invalid_constant, parse_float=_finite_float)
    except RecursionError:
        raise ValueError('JSON exceeds the supported nesting depth') from None
    if not isinstance(body, dict):
        raise ValueError('expected a JSON object')
    return body


def _validate_capabilities(body: dict) -> None:
    for section in ('providers', 'agent_providers'):
        providers = body.get(section)
        if not isinstance(providers, dict) or not providers:
            raise ValueError(f'capabilities requires a nonempty {section} map')
        for name, provider in providers.items():
            if not name.strip() or not isinstance(provider, dict):
                raise ValueError(f'invalid provider in {section}')
            if not isinstance(provider.get('label'), str) or not provider['label'].strip():
                raise ValueError(f'provider {name} requires a label')
            for field in ('editable_base_url', 'manageable', 'is_local'):
                if type(provider.get(field)) is not bool:
                    raise ValueError(f'provider {name} requires boolean {field}')


def _readiness_failures(body: dict) -> set[str]:
    ready, checks = body.get('ready'), body.get('checks')
    if type(ready) is not bool or not isinstance(checks, dict) or not REQUIRED_CHECKS <= checks.keys():
        raise ValueError('readiness requires ready and a complete check map')
    if any(not name.strip() or type(value) is not bool for name, value in checks.items()):
        raise ValueError('readiness checks must be named booleans')
    if ready != all(checks.values()):
        raise ValueError('ready flag disagrees with its check map')
    return {name for name, value in checks.items() if not value}


def _validate_health(body: dict) -> None:
    if body.get('ok') is not True:
        raise ValueError('backend liveness is not healthy')
    error = body.get('audio_limits_error')
    if error is not None and error != '':
        raise ValueError('backend reports invalid audio limits')
    if 'audio_limits' in body:
        limits = body['audio_limits']
        if not isinstance(limits, dict):
            raise ValueError('invalid audio_limits object')
        duration, upload = limits.get('max_duration_seconds'), limits.get('max_upload_bytes')
        if (type(duration) not in (int, float) or not 1 <= duration <= 1800 or not math.isfinite(duration)
                or type(upload) is not int or upload <= 0):
            raise ValueError('invalid audio_limits budgets')
    # Older servers may omit these optional diagnostics. If present, they must
    # not disguise a broken frontend or invalid runtime limits as model warm-up.
    if 'readiness' in body:
        readiness = body['readiness']
        if not isinstance(readiness, dict):
            raise ValueError('invalid health readiness object')
        failures = _readiness_failures(readiness)
        if failures - MODEL_CHECKS:
            raise ValueError('health reports an unavailable non-model dependency')


def check_stack(base: str, *, allow_degraded: bool = False) -> dict:
    checks = []
    for path in ('/', '/styles.css', '/js/app.js', '/api/capabilities', '/api/health', '/api/ready'):
        status = None
        details = None
        try:
            try:
                response = _open_url(base.rstrip('/') + path, timeout=15)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.status
                raw = _read_bounded(response)
            ok = status == 200
            if path.startswith('/api/'):
                details = _json_object(raw)
                if path == '/api/capabilities':
                    _validate_capabilities(details)
                elif path == '/api/health':
                    _validate_health(details)
                else:
                    failures = _readiness_failures(details)
                    if (status == 200) != details['ready'] or status not in (200, 503):
                        raise ValueError('readiness HTTP status disagrees with ready flag')
                    ok = details['ready'] or (allow_degraded and not (failures - MODEL_CHECKS))
            elif not raw:
                raise ValueError('frontend asset is empty')
            check = {'path': path, 'ok': ok, 'http_status': status}
        except (OSError, ValueError, http.client.HTTPException) as exc:
            check = {'path': path, 'ok': False, 'error': str(exc)}
            if status is not None:
                check['http_status'] = status
        if path == '/api/ready' and details is not None:
            check['readiness'] = details
        checks.append(check)
    return {'ok': all(check['ok'] for check in checks), 'base': base, 'allow_degraded': allow_degraded, 'checks': checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8001')
    parser.add_argument('--allow-degraded', action='store_true', help='Accept unavailable model dependencies; frontend and audio limits must remain healthy')
    args = parser.parse_args()
    result = check_stack(args.base, allow_degraded=args.allow_degraded)
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
