"""End-to-end regression test for the Korina Voice Lab API.

This script exercises every route on the live service and asserts that each
returns the expected HTTP status. Run it from a machine that can reach
``http://127.0.0.1:8001`` (the default for the systemd ``--user`` service
on roggoz).

Usage::

    python3 tests/regression_smoke.py [--base URL] [--no-chat] [--no-transcribe]

Exit code 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path


def http_get(base: str, path: str, timeout: float = 20.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"client_error: {type(e).__name__}: {e}"


def http_post(base: str, path: str, payload: dict, timeout: float = 30.0) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"client_error: {type(e).__name__}: {e}"


def http_post_multipart(base: str, path: str, wav_path: Path, timeout: float = 30.0) -> tuple[int, str]:
    boundary = "----korina-regression-boundary"
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{wav_path.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    parts.append(wav_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"client_error: {type(e).__name__}: {e}"


def make_silence_wav(path: Path, duration_seconds: float = 0.1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * duration_seconds))


# Each check is (name, expected_status, status, body).
def assert_status(name: str, got: int, want: int, body: str) -> bool:
    ok = got == want
    short = body.replace(chr(10), " ")[:140]
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {name}: HTTP {got} (expected {want})  {short}")
    return ok


def run(base: str, do_chat: bool, do_transcribe: bool) -> int:
    failures = 0

    # Health / config / models / metadata endpoints.
    for name, path, want in [
        ("GET /api/health", "/api/health", 200),
        ("GET /api/config", "/api/config", 200),
        ("GET /api/models", "/api/models", 200),
        ("GET /api/acks", "/api/acks", 200),
        ("GET /api/acks/status", "/api/acks/status", 200),
        ("GET /api/agent/status", "/api/agent/status", 200),
        ("GET /api/agent/events", "/api/agent/events", 200),
        ("GET /api/agent/models", "/api/agent/models", 200),
        ("GET /", "/", 200),
    ]:
        code, body = http_get(base, path)
        if not assert_status(name, code, want, body):
            failures += 1

    # Agent POST endpoints.
    for name, path, payload, want in [
        ("POST /api/agent/reset", "/api/agent/reset", {}, 200),
        (
            "POST /api/agent/state-report",
            "/api/agent/state-report",
            {"transcript": [], "previous_report": "", "model": "", "max_tokens": 16},
            200,
        ),
        (
            "POST /api/agent/transcript",
            "/api/agent/transcript",
            {"transcript": [], "delivery_mode": "injection", "reason": "regression", "turn_count": 0},
            200,
        ),
        (
            "POST /api/agent/permission-answer",
            "/api/agent/permission-answer",
            {"request_id": "regress-test", "answer": "yes"},
            200,
        ),
        ("POST /api/acks/rebuild", "/api/acks/rebuild", {}, 200),
        ("POST /api/chat (empty -> 400)", "/api/chat", {"message": ""}, 400),
    ]:
        code, body = http_post(base, path, payload)
        if not assert_status(name, code, want, body):
            failures += 1

    # Optional real LLM round-trip (skipped if --no-chat).
    if do_chat:
        code, body = http_post(
            base,
            "/api/chat",
            {"message": "hello korina"},
            timeout=60,
        )
        if not assert_status("POST /api/chat (with message -> 200)", code, 200, body):
            failures += 1

    # Optional transcribe via synthetic WAV (skipped if --no-transcribe).
    if do_transcribe:
        wav = Path("/tmp/korina_regression_silence.wav")
        make_silence_wav(wav)
        code, body = http_post_multipart(
            base,
            "/api/transcribe?backend=whisper",
            wav,
            timeout=60,
        )
        if not assert_status("POST /api/transcribe (multipart)", code, 200, body):
            failures += 1

    # OpenAPI surface.
    code, body = http_get(base, "/openapi.json")
    try:
        spec = json.loads(body)
        path_count = len(spec.get("paths", {}))
        required = ["/api/models", "/api/transcribe", "/api/llm/provider/activate", "/api/agent/state-report"]
        missing = [p for p in required if p not in spec.get("paths", {})]
        ok = code == 200 and path_count >= 18 and not missing
        if not assert_status("GET /openapi.json (>=18 paths, all required)", code, 200 if ok else 0,
                             f"{path_count} paths, missing={missing}"):
            failures += 1
    except Exception as e:
        print(f"  [FAIL] GET /openapi.json: parse error {e}")
        failures += 1

    print()
    if failures == 0:
        print(f"All checks passed against {base}.")
        return 0
    print(f"{failures} check(s) failed against {base}.")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:8001", help="Base URL")
    p.add_argument("--no-chat", action="store_true", help="Skip real LLM chat round-trip")
    p.add_argument("--no-transcribe", action="store_true", help="Skip synthetic-WAV transcribe")
    args = p.parse_args()
    return run(args.base, do_chat=not args.no_chat, do_transcribe=not args.no_transcribe)


if __name__ == "__main__":
    sys.exit(main())
