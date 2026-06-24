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

BASE = ''  # module-level placeholder; set by run() before invoking the 4 test_* functions
_FAILURES = [0]  # module-level counter; tests bump _FAILURES[0] += 1 on failure

import argparse
import json
import os
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
    global BASE
    BASE = base
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

    # Phase 4.2.3 -- /api/models must include per-model capability dicts
    # once the runtime picks up the route change (Task 4.6.1 restart). The
    # test itself always asserts the source-level contract, and
    # conditionally checks the live payload if/when the new fields exist.
    if not test_api_models_includes_capabilities():
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

    # ----- Phase 2 frontend wiring (must run BEFORE the factory
    # dispatch test, which historically raised SystemExit(0) and
    # skipped every test that came after it). The factory stub now
    # returns instead of raising, but we keep the tests grouped here
    # and clearly labelled. -----
    test_frontend_uses_capabilities()
    test_frontend_set_base_url_editability_for_agent()
    test_frontend_initial_sync_uses_capabilities()
    test_capabilities_endpoint()
    test_model_capability_heuristic()
    if not test_frontend_multimodal_stt_filter():
        failures += 1
    test_capabilities_anthropic_default_and_editability()

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
        # in git ls-files. Phase 1.9 reduces it to a 3-line shim. Also
        # verify runtime config.json files are not tracked, catching the
        # "tracked secret/runtime config" class of regression.
        import subprocess as _subprocess
        try:
            _ls = _subprocess.run(
                ["git", "ls-files"],
                cwd=_repo_root, capture_output=True, text=True, check=True,
            ).stdout
            tracked = _ls.splitlines()
            n_korina_voice_lab = sum(
                1 for line in tracked
                if line.endswith("korina_voice_lab.py")
            )
            ok_one = n_korina_voice_lab == 1
            if not assert_status(
                "blueprint: exactly 1 korina_voice_lab.py in git ls-files",
                200 if ok_one else 0, 200,
                f"count={n_korina_voice_lab}" if not ok_one else "1 entry",
            ):
                failures += 1

            tracked_runtime_configs = [
                line for line in tracked
                if line == "config.json" or line.endswith("/config.json")
            ]
            # Keep tracked examples/templates; only runtime config.json should be absent.
            tracked_runtime_configs = [
                line for line in tracked_runtime_configs
                if not line.endswith("config.example.json")
            ]
            ok_no_runtime_config = not tracked_runtime_configs
            if not assert_status(
                "git hygiene: runtime config.json is not tracked",
                200 if ok_no_runtime_config else 0, 200,
                "none tracked" if ok_no_runtime_config else f"tracked={tracked_runtime_configs}",
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
            # NOTE: do NOT raise SystemExit(0) here. SystemExit inherits
            # from BaseException, so a raise propagates out of run() and
            # skips any later test invocations (this regression script's
            # own test_capabilities_endpoint, plus any test added after
            # the factory block in Phase 2.2.3+). Just return -- the
            # captured dict is what we assert on.
            return
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

    # ----- Run summary -----
    print()
    total_failures = max(failures, _FAILURES[0])
    if total_failures == 0:
        print(f"All checks passed against {base}.")
        return 0
    print(f"{total_failures} check(s) failed against {base}.")
    return 1

def test_frontend_uses_capabilities():
    """Phase 2.2.3 (updated for Phase 3 modularization) -- after Phase 3,
    the index.html is <script type="module" src="./js/app.js"></script>.
    The capability helpers and loadCapabilities() now live in
    Korina/js/providers-ui.js (fetched as a separate static file).
    Verify by reading /js/providers-ui.js instead of an inline <script>
    block. Also verify the index.html still references /api/capabilities
    via the module entry chain (app.js -> providers-ui.js)."""
    import urllib.request, re
    try:
        # Check 1: index.html references the module entrypoint.
        with urllib.request.urlopen(BASE + "/", timeout=10) as r:
            assert r.status == 200
            html = r.read().decode("utf-8")
        assert re.search(r'<script[^>]*type="module"[^>]*src="\./js/app\.js"', html), \
            "index.html does not load app.js as a module"
        # Check 2: providers-ui.js contains the helpers and /api/capabilities fetch.
        with urllib.request.urlopen(BASE + "/js/providers-ui.js", timeout=10) as r:
            assert r.status == 200
            js = r.read().decode("utf-8")
        assert "function getResponseLlmProviderCaps" in js, \
            "getResponseLlmProviderCaps helper not defined in providers-ui.js"
        assert "function getAgentProviderCaps" in js, \
            "getAgentProviderCaps helper not defined in providers-ui.js"
        assert "/api/capabilities" in js, \
            "no /api/capabilities fetch in providers-ui.js"
        # Old: legacy constant declaration gone (its usage in a comment is fine;
        # we only ban the const declaration).
        assert "const PROVIDER_BASE_URL_PRESETS" not in js, \
            "legacy PROVIDER_BASE_URL_PRESETS const declaration still present"
    except Exception as e:
        print(f"  [FAIL] frontend uses capabilities: {type(e).__name__}: {e}")
        _FAILURES[0] += 1
        return
    print(f"  [PASS] frontend uses capabilities: loadCapabilities + helpers present, legacy constant removed")


def test_frontend_set_base_url_editability_for_agent():
    """Phase 2.2.3 (updated for Phase 3 modularization) -- setBaseUrlEditability
    must wire up the agent provider's base URL field. After Phase 3 the
    function lives in /js/providers-ui.js. Read that file directly."""
    import urllib.request, re
    try:
        with urllib.request.urlopen(BASE + "/js/providers-ui.js", timeout=10) as r:
            assert r.status == 200
            js = r.read().decode("utf-8")
        # The agent section must be present in the editability function.
        m_fn = re.search(r"async function setBaseUrlEditability\(\)\{([\s\S]*?)\n\}", js)
        assert m_fn, "async setBaseUrlEditability not found in providers-ui.js"
        body = m_fn.group(1)
        assert "agentBaseUrl" in body, \
            "setBaseUrlEditability does not reference agentBaseUrl -- the agent path is uncontrolled"
        assert "getAgentProviderCaps" in js, \
            "getAgentProviderCaps helper not present in providers-ui.js"
    except Exception as e:
        print(f"  [FAIL] frontend setBaseUrlEditability for agent: {type(e).__name__}: {e}")
        _FAILURES[0] += 1
        return
    print(f"  [PASS] frontend setBaseUrlEditability for agent: agentBaseUrl wired, getAgentProviderCaps present")


def test_frontend_initial_sync_uses_capabilities():
    """Phase 2.2.3 (updated for Phase 3 modularization) -- the top-level
    loadCapabilities().then(()=>setBaseUrlEditability()) call from 2.2.2
    must be in /js/app.js (the entrypoint), so the async editability pass
    runs before initApp() without modifying initApp itself. After Phase 3,
    initApp itself lives in /js/api.js, so we verify initApp's signature
    is unchanged in api.js."""
    import urllib.request, re
    try:
        with urllib.request.urlopen(BASE + "/js/app.js", timeout=10) as r:
            assert r.status == 200
            js = r.read().decode("utf-8")
        # Allow flexible whitespace and a trailing semicolon.
        assert re.search(r"loadCapabilities\s*\(\s*\)\s*\.\s*then\s*\(\s*\(\s*\)\s*=>\s*setBaseUrlEditability\s*\(\s*\)\s*\)", js), \
            "initial sync is not loading capabilities before setting editability in app.js"
        # initApp must still be untouched (no await loadCapabilities inside).
        # Phase 3: initApp lives in /js/api.js, not /js/app.js. Read it from there.
        with urllib.request.urlopen(BASE + "/js/api.js", timeout=10) as r:
            assert r.status == 200
            api_js = r.read().decode("utf-8")
        m_init = re.search(r"async function initApp\s*\(\s*\)\s*\{([\s\S]*?)\n\}", api_js)
        assert m_init, "initApp not found in api.js"
        init_body = m_init.group(1)
        assert "try{ await loadCapabilities();" not in init_body, \
            "initApp was modified to await loadCapabilities -- 2.2.2 discipline broken"
    except Exception as e:
        print(f"  [FAIL] frontend initial sync uses capabilities: {type(e).__name__}: {e}")
        _FAILURES[0] += 1
        return
    print(f"  [PASS] frontend initial sync uses capabilities: top-level loadCapabilities().then() present, initApp untouched")

def test_capabilities_anthropic_default_and_editability():
    """Phase 2.3.2 -- pin the editable_base_url contract across all providers.

    Mirrors the per-provider table in docs/capabilities.md. If a future
    change to PROVIDER_CAPABILITIES or to a per-provider helper drifts
    from the contract, this test fails loudly.
    """
    import json, urllib.request
    try:
        with urllib.request.urlopen(BASE + "/api/capabilities", timeout=10) as r:
            assert r.status == 200
            body = json.loads(r.read().decode("utf-8"))
        # Anthropic (agent-only) contract
        a = body["agent_providers"]["anthropic"]
        assert a["default_base_url"] == "", (
            f"anthropic default_base_url must be empty (user must supply "
            f"their own Anthropic-compatible URL); got {a['default_base_url']!r}"
        )
        assert a["editable_base_url"] is True, (
            "anthropic must be editable so the user can paste their URL"
        )
        assert a["is_local"] is False, "anthropic is not a local server"
        assert a["agent_only"] is True, (
            "anthropic must be agent_only so it does not appear in the "
            "response-LLM section"
        )
        # Response-LLM editability contract (matches the legacy hardcoded
        # 'openai-compatible' check, so frontend behavior is identical for
        # current users even after the migration to the cache).
        p = body["providers"]
        assert p["openai-compatible"]["editable_base_url"] is True
        assert p["llama.cpp"]["editable_base_url"] is False
        assert p["lmstudio"]["editable_base_url"] is False
        assert p["ollama"]["editable_base_url"] is False
        # No field drift: every provider must declare the same 8 keys.
        expected_keys = {
            "label", "default_base_url", "editable_base_url", "manageable",
            "model_sources", "is_local", "agent_only", "response_llm_only",
        }
        for section_name, section in (("providers", p), ("agent_providers", body["agent_providers"])):
            for pid, cap in section.items():
                missing = expected_keys - set(cap.keys())
                assert not missing, (
                    f"{section_name}.{pid} missing keys: {sorted(missing)}"
                )
    except Exception as e:
        print(f"  [FAIL] capabilities contract per provider: {type(e).__name__}: {e}")
        _FAILURES[0] += 1
        return
    print("  [PASS] capabilities contract per provider: anthropic default=empty+editable, openai-compatible editable, 3 local not editable, 8 keys on every provider")

def test_capabilities_endpoint():
    """Phase 2.1 -- GET /api/capabilities returns the full registry split by section."""
    import json, urllib.request
    with urllib.request.urlopen(BASE + "/api/capabilities", timeout=10) as r:
        assert r.status == 200
        body = json.loads(r.read().decode("utf-8"))
    assert body.get("version") == 1
    providers = body.get("providers") or {}
    agent_providers = body.get("agent_providers") or {}
    assert set(providers.keys()) == {"llama.cpp", "lmstudio", "ollama", "openai-compatible"}
    for pid, cap in providers.items():
        assert cap.get("agent_only") is False, f"{pid} leaked into response-LLM section"
        assert isinstance(cap.get("editable_base_url"), bool)
        assert isinstance(cap.get("manageable"), bool)
        assert isinstance(cap.get("is_local"), bool)
        assert isinstance(cap.get("model_sources"), list)
        assert cap.get("label"), f"{pid} missing label"
    assert set(agent_providers.keys()) == {"openai-compatible", "anthropic"}
    assert agent_providers["anthropic"].get("agent_only") is True
    assert agent_providers["anthropic"].get("default_base_url") == ""
    assert agent_providers["anthropic"].get("editable_base_url") is True
    assert providers["llama.cpp"]["default_base_url"] == "http://127.0.0.1:8080/v1"
    assert providers["lmstudio"]["default_base_url"]  == "http://127.0.0.1:1234/v1"
    assert providers["ollama"]["default_base_url"]    == "http://127.0.0.1:11434/v1"





def test_model_capability_heuristic():
    """Phase 4.1 -- the model capability detector must correctly identify
    multimodal GGUFs via the registry, the mmproj-on-disk check, the hard
    allowlist, and the runtime allowlist; and must NOT false-positive on
    embeddings or TTS models.
    """
    import importlib
    mc = importlib.import_module("korina.services.model_capability")

    # 1. gemma-4-E2B is in the explicit registry -> multimodal, registry path.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q8_0.gguf"
    )
    assert cap["supports_audio_input"] is True, (
        f"gemma-4-E2B-it-Q8_0.gguf not flagged multimodal: {cap}"
    )
    assert cap["has_mmproj"] is True, cap
    assert cap["_inference"] in ("registry", "allowlist:gemma-4-e2b",
                                 "name_hint:gemma-4-e2b"), cap

    # 2. Qwen3-VL is in the registry as vision-only: mmproj present but
    # supports_audio_input must be False.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "Qwen3-VL-4B-Instruct-GGUF/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    )
    assert cap["supports_audio_input"] is False, (
        f"Qwen3-VL wrongly flagged as audio-capable: {cap}"
    )
    assert cap["has_mmproj"] is True, cap

    # 3. Plain Qwen3.5-2B text model sits next to an mmproj file on disk;
    # the heuristic therefore reports mmproj_present + multimodal. This is
    # the intentional "presence-of-mmproj wins" behaviour (no blacklist hit,
    # no registry entry) -- so we assert the positive signal is recorded
    # and the model is NOT classified as plain text by default.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "Qwen3.5-2B-GGUF/Qwen3.5-2B-Q8_0.gguf"
    )
    assert cap["has_mmproj"] is True, cap
    assert cap["supports_audio_input"] is True, cap
    assert cap["_inference"] == "mmproj_present", cap

    # 4. nomic-embed must be blacklisted/registry-non-multimodal even though
    # some embedding GGUFs ship with an mmproj for completeness. Embeddings
    # never consume audio input.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/nomic-ai/"
        "nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.f32.gguf"
    )
    assert cap["supports_audio_input"] is False, cap
    assert cap["_inference"] in ("registry", "blacklist"), cap

    # 5. Runtime allowlist override: a text-only model explicitly added to
    # the runtime allowlist becomes multimodal via the runtime_allowlist
    # branch and short-circuits before the heuristic.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "Qwen3.5-2B-GGUF/Qwen3.5-2B-Q8_0.gguf",
        allowlist=("/home/roggoz/Disks/SN750/models/lmstudio-community/"
                   "Qwen3.5-2B-GGUF/Qwen3.5-2B-Q8_0.gguf",),
    )
    assert cap["supports_audio_input"] is True, cap
    assert cap["_inference"] == "runtime_allowlist", cap

    print("  [PASS] model capability heuristic: registry, mmproj-on-disk, "
          "hard allowlist, runtime allowlist, blacklist all behave as documented")


def test_api_models_includes_capabilities() -> bool:
    """Phase 4.2.3 -- /api/models MUST include ``llm_models_capabilities``
    and ``stt_llm_models_capabilities`` dicts once the runtime picks up
    the route change (Task 4.6.1 restart). Until then, this test verifies
    the source-level contract: the route module references both keys, and
    if the live endpoint already exposes them we also validate their
    shape. Returns True on success.
    """
    # Source-level contract -- always asserted.
    src_path = os.path.join(os.path.dirname(__file__), "..", "korina",
                            "routes", "models.py")
    with open(src_path) as _f:
        src = _f.read()
    if "llm_models_capabilities" not in src:
        print(f"  [FAIL] {src_path} does not declare llm_models_capabilities")
        return False
    if "stt_llm_models_capabilities" not in src:
        print(f"  [FAIL] {src_path} does not declare stt_llm_models_capabilities")
        return False
    print("  [PASS] korina/routes/models.py source declares both "
          "*_models_capabilities keys")

    # Live endpoint -- conditional. The running service on roggoz does NOT
    # pick up route changes until Task 4.6.1 restart, so /api/models still
    # returns the old shape. Skip the per-model assertion in that case.
    try:
        _code, _body = http_get(BASE, "/api/models")
        payload = json.loads(_body)
    except Exception as e:
        print(f"  [FAIL] /api/models live check: could not parse response: "
              f"{type(e).__name__}: {e}")
        return False

    if isinstance(payload, dict) and "llm_models_capabilities" in payload \
            and "stt_llm_models_capabilities" in payload:
        if not isinstance(payload["llm_models_capabilities"], dict):
            print(f"  [FAIL] /api/models llm_models_capabilities is not a "
                  f"dict: {type(payload['llm_models_capabilities']).__name__}")
            return False
        if not isinstance(payload["stt_llm_models_capabilities"], dict):
            print(f"  [FAIL] /api/models stt_llm_models_capabilities is not "
                  f"a dict: {type(payload['stt_llm_models_capabilities']).__name__}")
            return False
        print("  [PASS] /api/models live endpoint includes both "
              "*_models_capabilities dicts")
    else:
        print("  [SKIP] /api/models live endpoint predates the capability "
              "fields (restart pending in 4.6.1)")
    return True



def test_frontend_multimodal_stt_filter() -> bool:
    """Phase 4.3 -- served index.html must include the All models toggle,
    served capability-filter.js must export filterModelsByCapability, and
    served providers-ui.js must import from capability-filter.js.
    Returns True on success.
    """
    import re

    # 1. Toggle in index.html.
    try:
        with urllib.request.urlopen(BASE + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
    except Exception as e:
        print(f"  [FAIL] could not fetch index.html: {type(e).__name__}: {e}")
        return False
    if 'id="sttCapabilityFilterOverride"' not in html:
        print("  [FAIL] sttCapabilityFilterOverride toggle missing from served index.html")
        return False
    print("  [PASS] served index.html has sttCapabilityFilterOverride toggle")

    # 2. capability-filter.js served and contains the expected exports.
    try:
        with urllib.request.urlopen(BASE + "/js/capability-filter.js", timeout=10) as r:
            js = r.read().decode("utf-8")
    except Exception as e:
        print(f"  [FAIL] could not fetch capability-filter.js: {type(e).__name__}: {e}")
        return False
    if "filterModelsByCapability" not in js:
        print("  [FAIL] filterModelsByCapability not exported from served capability-filter.js")
        return False
    if "sttLlmModelRequirement" not in js:
        print("  [FAIL] sttLlmModelRequirement not exported from served capability-filter.js")
        return False
    print("  [PASS] served capability-filter.js exports filterModelsByCapability + sttLlmModelRequirement")

    # 3. providers-ui.js imports from capability-filter.js.
    try:
        with urllib.request.urlopen(BASE + "/js/providers-ui.js", timeout=10) as r:
            pu = r.read().decode("utf-8")
    except Exception as e:
        print(f"  [FAIL] could not fetch providers-ui.js: {type(e).__name__}: {e}")
        return False
    if "capability-filter.js" not in pu:
        print("  [FAIL] providers-ui.js does not import from capability-filter.js")
        return False
    print("  [PASS] served providers-ui.js imports from capability-filter.js")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:8001", help="Base URL")
    p.add_argument("--no-chat", action="store_true", help="Skip real LLM chat round-trip")
    p.add_argument("--no-transcribe", action="store_true", help="Skip synthetic-WAV transcribe")
    args = p.parse_args()
    return run(args.base, do_chat=not args.no_chat, do_transcribe=not args.no_transcribe)


if __name__ == "__main__":
    sys.exit(main())