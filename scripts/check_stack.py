"""Read-only HTTP smoke gate for a running Korina demo stack.

Usage: python scripts/check_stack.py [--base URL] [--allow-degraded]
Unlike the legacy regression suite, this never resets sessions, edits config,
switches providers, or sends prompts/audio to a model.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def check_stack(base: str, *, allow_degraded: bool = False) -> dict:
    checks = []
    for path in ('/', '/styles.css', '/js/app.js', '/api/capabilities', '/api/health', '/api/ready'):
        try:
            with urllib.request.urlopen(base.rstrip('/') + path, timeout=15) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (OSError, ValueError) as exc:
            checks.append({'path': path, 'ok': False, 'error': str(exc)})
            continue
        ok = status == 200
        details = None
        if path.startswith('/api/'):
            try:
                details = json.loads(raw)
                if path == '/api/health':
                    ok = ok and details.get('ok') is True
                elif path == '/api/ready':
                    ok = ok and details.get('ready') is True
                    if allow_degraded and status == 503 and details.get('ready') is False:
                        ok = True
            except (ValueError, AttributeError):
                ok = False
        checks.append({'path': path, 'ok': ok, 'http_status': status, **({'readiness': details} if path == '/api/ready' else {})})
    return {'ok': all(check['ok'] for check in checks), 'base': base, 'allow_degraded': allow_degraded, 'checks': checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8001')
    parser.add_argument('--allow-degraded', action='store_true', help='Accept a reachable stack with unavailable models; infrastructure smoke only')
    args = parser.parse_args()
    result = check_stack(args.base, allow_degraded=args.allow_degraded)
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
