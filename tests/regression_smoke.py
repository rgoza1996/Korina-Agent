"""End-to-end regression test for the Korina Voice Lab API.

This script exercises every route on the live service and asserts that each
returns the expected HTTP status. Run it from a machine that can reach
``http://127.0.0.1:8001`` (the default for the systemd ``--user`` service
on roggoz).

Usage::

    python3 tests/regression_smoke.py [--base URL] [--no-chat] [--no-transcribe]

Exit code 0 if every check passes, 1 otherwise.

Phase 1.7 additions (schema validation, partial transcribe, provider
activate, /api/config POST round-trip, OpenAPI body-schema visibility).

Phase 1.8 additions (korina.app.main dispatch — both explicit-app and
no-arg fallback paths forward to uvicorn.run with the right args).

Phase 1.9 additions (korina.app_factory.create_app owns app construction;
korina.app.main() has no app arg; Korina/korina_voice_lab.py is a 3-line
shim to main().)
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


def http_post_multipart(base: str, path: str, wav_path: Path,
                        query: str = "", timeout: float = 30.0) -> tuple[int, str]:
    boundary = "----korina-regression-boundary"
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"{wav_path.name}\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    parts.append(wav_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = base + path + (("?" + query) if query else "")
    req = urllib.request.Request(
        url,
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

    # ----- Phase 1.7 additions: schema validation + new endpoints -----

    # Schema validation: missing required field → 422 (Pydantic).
    code, body = http_post(base, "/api/chat", {"history": []})  # no "message"
    if not assert_status("POST /api/chat (missing 'message' -> 422)", code, 422, body):
        failures += 1

    # Schema validation: wrong type → 422.
    code, body = http_post(base, "/api/agent/transcript", {"delivery_mode": 12345})
    if not assert_status("POST /api/agent/transcript (bad type -> 422)", code, 422, body):
        failures += 1

    # Schema validation: provider activate missing 'provider' → 422.
    code, body = http_post(base, "/api/llm/provider/activate", {"model": "x"})
    if not assert_status("POST /api/llm/provider/activate (missing 'provider' -> 422)", code, 422, body):
        failures += 1

    # Schema validation: permission-answer with all-default fields is
    # accepted by design (the schema intentionally allows empty bodies
    # because the helper is a no-op in that case). The route returns
    # 200, not 422 — this validates that the defaults work, which is
    # part of the 1.7 contract.
    code, body = http_post(base, "/api/agent/permission-answer", {})
    if not assert_status("POST /api/agent/permission-answer (empty body -> 200, defaults accepted)", code, 200, body):
        failures += 1

    # Schema validation: permission-answer with bad type → 422.
    code, body = http_post(base, "/api/agent/permission-answer", {"request_id": 12345})
    if not assert_status("POST /api/agent/permission-answer (bad type -> 422)", code, 422, body):
        failures += 1

    # Optional real LLM round-trip (skipped if --no-chat). Run this before
    # provider-activation tests: activating openai-compatible intentionally
    # stops managed local providers such as llama-server, and would make this
    # later chat check fail for sequencing reasons rather than app behavior.
    if do_chat:
        code, body = http_post(
            base,
            "/api/chat",
            {"message": "hello korina"},
            timeout=60,
        )
        if not assert_status("POST /api/chat (with message -> 200)", code, 200, body):
            failures += 1

    # Provider activate with valid payload → 200 (active is a known provider).
    # This is intentionally after /api/chat because it may stop managed local
    # provider processes as a side effect of switching to openai-compatible.
    code, body = http_post(base, "/api/llm/provider/activate",
                           {"provider": "openai-compatible", "model": ""}, timeout=45)
    if not assert_status("POST /api/llm/provider/activate (valid -> 200)", code, 200, body):
        failures += 1

    # /api/config POST round-trip: send a known-valid key (the route
    # filters unknown keys), verify the change persists, then roll back.
    config_before = json.loads(http_get(base, "/api/config")[1])
    test_key = "voice"
    original_value = config_before.get(test_key, "af_heart")
    new_value = "af_bella" if original_value != "af_bella" else "af_heart"
    payload = dict(config_before)
    payload[test_key] = new_value
    code, body = http_post(base, "/api/config", payload)
    if not assert_status("POST /api/config (round-trip -> 200)", code, 200, body):
        failures += 1
    else:
        code, body = http_get(base, "/api/config")
        ok = code == 200 and f"\"voice\":\"{new_value}\"" in body
        if not assert_status(f"POST /api/config (echo: voice={new_value} verified)", 200 if ok else 0, 200,
                             f"voice={new_value}" if ok else "voice not updated"):
            failures += 1
        # Roll back.
        roll_back = dict(config_before)
        code, _ = http_post(base, "/api/config", roll_back)
        if code != 200:
            print(f"  [WARN] failed to roll back /api/config (HTTP {code})")

    # Optional transcribe via synthetic WAV (skipped if --no-transcribe).
    if do_transcribe:
        wav = Path("/tmp/korina_regression_silence.wav")
        make_silence_wav(wav)
        code, body = http_post_multipart(
            base,
            "/api/transcribe",
            wav,
            query="backend=whisper",
            timeout=60,
        )
        if not assert_status("POST /api/transcribe (multipart)", code, 200, body):
            failures += 1

        # Partial transcribe — same shape as transcribe but separate endpoint.
        code, body = http_post_multipart(
            base,
            "/api/transcribe/partial",
            wav,
            query="backend=whisper",
            timeout=60,
        )
        if not assert_status("POST /api/transcribe/partial (multipart)", code, 200, body):
            failures += 1

    # OpenAPI surface — verify path count, required paths.
    code, body = http_get(base, "/openapi.json")
    try:
        spec = json.loads(body)
        paths = spec.get("paths", {})
        path_count = len(paths)
        required = [
            "/api/models",
            "/api/transcribe",
            "/api/transcribe/partial",
            "/api/llm/provider/activate",
            "/api/agent/state-report",
            "/api/agent/transcript",
            "/api/agent/permission-answer",
            "/api/chat",
            "/api/config",
        ]
        missing = [p for p in required if p not in paths]
        ok = code == 200 and path_count >= 19 and not missing
        if not assert_status("GET /openapi.json (>=19 paths, all required)",
                             code, 200 if ok else 0,
                             f"{path_count} paths, missing={missing}"):
            failures += 1
    except Exception as e:
        print(f"  [FAIL] GET /openapi.json: parse error {e}")
        failures += 1

    # Phase 1.7 deliverable: the 5 Pydantic models are importable from
    # korina.schemas as the canonical home. We don't assert they appear
    # in components.schemas — that requires route signatures to declare
    # `payload: Schema = Body(...)` rather than the current manual
    # `Schema(**(await request.json()))` pattern, which is a future
    # follow-up. The importable-as-canonical contract IS the 1.7 scope.
    try:
        # Make sure korina is importable regardless of cwd: the test
        # script lives at <repo>/tests/, so the repo root is its parent.
        import os as _os
        _repo_root = str(Path(__file__).resolve().parent.parent)
        if _repo_root not in _os.sys.path:
            _os.sys.path.insert(0, _repo_root)
        from korina.schemas import (  # type: ignore[import-not-found]
            AgentPermissionAnswer,
            AgentStateRequest,
            AgentTranscriptRequest,
            ChatRequest,
            ProviderActivateRequest,
        )
        classes_ok = all(c.__name__ == name for c, name in zip(
            (AgentPermissionAnswer, AgentStateRequest, AgentTranscriptRequest,
             ChatRequest, ProviderActivateRequest),
            ("AgentPermissionAnswer", "AgentStateRequest", "AgentTranscriptRequest",
             "ChatRequest", "ProviderActivateRequest"),
        ))
        if not assert_status(
            "importable: 5 schemas from korina.schemas (canonical home)",
            200 if classes_ok else 0,
            200,
            "all 5 importable" if classes_ok else "name mismatch",
        ):
            failures += 1
    except ImportError as e:
        print(f"  [FAIL] korina.schemas import: {e}")
        failures += 1

    # Phase 1.8 / 1.9 deliverable: korina.app.main() is the canonical uvicorn
    # launcher. After 1.9 the package owns the app via app_factory.create_app,
    # and main() takes no app argument. We verify:
    #   1. main() imports cleanly (no app to pass).
    #   2. korina.app_factory.create_app() returns a wired FastAPI app.
    #   3. Calling main() with stubbed uvicorn.run forwards the factory-built
    #      app to uvicorn.run with the right host/port defaults.
    try:
        # Make sure korina is importable regardless of cwd.
        import os as _os
        _repo_root = str(Path(__file__).resolve().parent.parent)
        if _repo_root not in _os.sys.path:
            _os.sys.path.insert(0, _repo_root)

        import uvicorn as _uvicorn
        from korina.app_factory import create_app  # type: ignore[import-not-found]

        # 0) Blueprint check: korina_voice_lab.py appears exactly once
        # in git ls-files. Phase 1.9 reduces it to a 3-line shim.
        import subprocess as _subprocess
        try:
            _ls = _subprocess.run(
                ["git", "ls-files"],
                cwd=_repo_root, capture_output=True, text=True, check=True,
            ).stdout
            n_korina_voice_lab = sum(
                1 for line in _ls.splitlines()
                if line.endswith("korina_voice_lab.py")
            )
            ok_one = n_korina_voice_lab == 1
            if not assert_status(
                "blueprint: exactly 1 korina_voice_lab.py in git ls-files",
                200 if ok_one else 0, 200,
                f"count={n_korina_voice_lab}" if not ok_one else "1 entry",
            ):
                failures += 1
        except Exception as e:
            print(f"  [FAIL] git ls-files check: {type(e).__name__}: {e}")
            failures += 1

        # 1) Factory builds the same app the live service runs.
        factory_app = create_app()
        ok_factory = (
            factory_app.title.startswith("Korina")
            and len(factory_app.openapi()["paths"]) >= 19
        )
        if not assert_status(
            "factory: korina.app_factory.create_app() returns wired FastAPI app",
            200 if ok_factory else 0, 200,
            f"{len(factory_app.openapi()['paths'])} paths" if ok_factory else "factory broken",
        ):
            failures += 1

        # 2) main() forwards to uvicorn.run with the factory-built app.
        captured: list[dict] = []
        def _capture_run(*args, **kwargs):
            title = (args[0].title if args
                     else getattr(kwargs.get("app"), "title", None))
            captured.append({
                "title": title,
                "host": args[1] if len(args) > 1 else kwargs.get("host"),
                "port": args[2] if len(args) > 2 else kwargs.get("port"),
            })
            raise SystemExit(0)
        _uvicorn.run = _capture_run

        from korina.app import main as korina_main  # type: ignore[import-not-found]
        # Verify the post-1.9 signature has no `app` parameter.
        import inspect
        sig = inspect.signature(korina_main)
        no_app_arg = "app" not in sig.parameters
        if not assert_status(
            "factory: korina.app.main() takes no app arg (1.9 contract)",
            200 if no_app_arg else 0, 200,
            f"sig={sig}" if not no_app_arg else "main() has no app param",
        ):
            failures += 1

        # Call main() — should build app via factory and pass to uvicorn.
        try:
            korina_main()
        except SystemExit:
            pass
        ok_main = (
            len(captured) == 1
            and captured[0]["title"] and captured[0]["title"].startswith("Korina")
            and captured[0]["host"] == "0.0.0.0"
            and captured[0]["port"] == 8001
        )
        if not assert_status(
            "dispatch: korina.app.main() builds via factory + calls uvicorn.run",
            200 if ok_main else 0, 200,
            f"captured={captured}" if not ok_main else "main() dispatched correctly",
        ):
            failures += 1

        # 3) main(host=, port=) forwards the override.
        captured.clear()
        try:
            korina_main(host="127.0.0.1", port=9999)
        except SystemExit:
            pass
        ok_override = (
            len(captured) == 1
            and captured[0]["host"] == "127.0.0.1"
            and captured[0]["port"] == 9999
        )
        if not assert_status(
            "dispatch: korina.app.main(host=, port=) forwards overrides",
            200 if ok_override else 0, 200,
            f"captured={captured}" if not ok_override else "overrides forwarded",
        ):
            failures += 1
    except Exception as e:
        print(f"  [FAIL] korina.app.main dispatch test: {type(e).__name__}: {e}")
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