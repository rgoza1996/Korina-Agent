"""Provider startup polling must inherit the bounded transport deadline."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from korina.services import provider_manager


def test_ready_poll_cannot_be_kept_alive_by_a_trickling_response():
    entered = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            entered.set()
            body = b'{"data": []}' * 20
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(.02)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match='did not become ready'):
            provider_manager._wait_for_models(f'http://127.0.0.1:{server.server_port}/v1/models', 'Fixture', .2)
        assert time.monotonic() - started < 1
        assert entered.is_set()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
