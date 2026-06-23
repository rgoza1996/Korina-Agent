# Phase 1.10 Verification

Verified on roggoz at `2026-06-22 21:00 PDT` on branch `beta`.

## Commit baseline

- Phase 0 baseline used for route-contract comparison: `b065ea0` (`docs: mark Phase 0 stabilization tasks complete in PROGRESS.md`).
- Phase 1.9 starting point: `4dc1f2d` (`refactor: delete monolith body — korina_voice_lab.py is now a 3-line shim (Phase 1.9)`).

## Live service state

- `korina-voice-lab.service`: active, PID `756468`, serving `http://0.0.0.0:8001`.
- `llama-server.service`: active, PID `757160`, serving `http://0.0.0.0:8080`.
- `/api/health`: returned `ok: true` and `response_llm_chat_url: http://127.0.0.1:8080/v1/chat/completions`.

## Regression suite

Command:

```bash
cd /home/roggoz/Korina-Agent
python3 tests/regression_smoke.py
```

Result: **all 28 checks passed**, including:

- `/api/health`, `/api/config`, `/api/models`
- `/api/acks`, `/api/acks/status`, `/api/acks/rebuild`
- `/api/agent/status`, `/api/agent/events`, `/api/agent/models`, `/api/agent/reset`, `/api/agent/state-report`, `/api/agent/transcript`, `/api/agent/permission-answer`
- `/api/chat` empty payload → 400
- `/api/chat` missing required `message` → 422
- `/api/chat` real LLM round-trip → 200 with reply `Hello there!\nHow can I help you today?`
- `/api/transcribe`, `/api/transcribe/partial`
- `/openapi.json` exposes 19 paths
- `korina.schemas` exports all 5 Pydantic request models
- `git ls-files` has exactly 1 `korina_voice_lab.py`
- `korina.app_factory.create_app()` builds a wired FastAPI app with 19 paths
- `korina.app.main()` has no `app` argument and dispatches to `uvicorn.run()` with host/port forwarding

## OpenAPI / route contract comparison

The full historical `b065ea0` OpenAPI generation is not reproducible under the current installed FastAPI/Pydantic stack because the old module hits a Pydantic forward-ref error involving `Request` during `app.openapi()` generation. I therefore used the verifiable fallback from the actual `b065ea0` FastAPI app object:

1. Extract registered route path/method table from `b065ea0`.
2. Extract current live path/method table from `/openapi.json`.
3. Compare path set and method set.

Result:

```text
phase0 route count 19
phase1 openapi path count 19
missing paths []
added paths []
method diffs []
routes match: True
```

A lower-level FastAPI dependency comparison also confirmed 20 route-method entries in both versions with no missing/added entries. Two dependency-shape diffs remain for `/api/agent/permission-answer` and `/api/agent/state-report`: `b065ea0` used `request: Request`, which the current FastAPI/Pydantic stack treats as a query dependency and is the same reason old OpenAPI generation fails. The current Phase 1 version uses the intended extracted Pydantic schemas for those endpoints.

During 1.10, the route signatures for the other POST endpoints were restored from manual `Request` parsing to typed/body parameters so FastAPI exposes request bodies again in OpenAPI:

- `/api/chat` → `ChatRequest`
- `/api/llm/provider/activate` → `ProviderActivateRequest`
- `/api/agent/transcript` → `AgentTranscriptRequest`
- `/api/agent/permission-answer` → `AgentPermissionAnswer`
- `/api/agent/state-report` → `AgentStateRequest`
- `/api/config` → `dict`
- `/api/acks/rebuild` → `dict`

## Conclusion

Phase 1 behavior-preserving refactor is verified:

- API path/method contract matches Phase 0 baseline.
- Live endpoint regression passes with real chat, STT, ACK, agent, config, model, and app-factory checks.
- Monolith body removal is complete; `Korina/korina_voice_lab.py` is the compatibility shim and `korina.app_factory.create_app()` owns app construction.
