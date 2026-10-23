"""Retired legacy smoke command; no checks or runtime operations are performed.

Use scripts/check_stack.py for read-only HTTP readiness checks and
follow docs/hackathon.md for an actual model and hardware rehearsal.
"""
from __future__ import annotations

import argparse
import shlex
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8001', help='Stack URL for the suggested replacement command')
    parser.add_argument('--no-chat', action='store_true', help='Accepted legacy flag; no checks are run')
    parser.add_argument('--no-transcribe', action='store_true', help='Accepted legacy flag; no checks are run')
    args = parser.parse_args()
    print(
        'tests/regression_smoke.py is retired. No requests, configuration changes, '
        'provider operations, or model inference were performed.\n'
        'Read-only readiness check:\n'
        f'  python scripts/check_stack.py --base {shlex.quote(args.base)}\n'
        'Deterministic regression checks:\n'
        '  python -m pytest -q\n'
        '  npm test\n'
        '  npm run test:browser\n'
        'For actual model and hardware validation, follow docs/hackathon.md and '
        'record the boundaries described in docs/verification.md. '
        'Readiness and fixture tests do not validate model quality, physical '
        'microphone behavior, GPU compatibility, or voice latency.',
        file=sys.stderr,
    )
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
