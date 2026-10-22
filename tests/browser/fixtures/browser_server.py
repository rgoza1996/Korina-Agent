"""Serve a disposable browser-test app; an occupied port fails instead of reusing it."""
from __future__ import annotations

import argparse
import json
import signal
import sys

from runtime import REPO, browser_runtime

sys.path.insert(0, str(REPO))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    port = parser.parse_args().port
    if not 1 <= port <= 65535:
        parser.error('port must be between 1 and 65535')
    with browser_runtime() as runtime:
        from korina.app import main as serve
        print(json.dumps({'browser_runtime': str(runtime)}), flush=True)
        # Uvicorn handles SIGTERM, then re-raises it to the previous handler.
        # Let that final signal return so this context removes its runtime tree.
        previous = signal.signal(signal.SIGTERM, lambda *_: None)
        try:
            serve(host='127.0.0.1', port=port)
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
