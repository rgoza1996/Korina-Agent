# Phase 4 — Capability registry for model selection

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Each task = one commit on `beta`. **This is the first phase that changes user-visible behavior** (some currently-pickable combinations become filtered), so commits are individually auditable and the regression suite grows with the change.

**Goal:** the user cannot pick a text-only model for multimodal STT, and a clearly-incompatible (provider, model) pair fails fast at `/api/llm/provider/activate` with a 400 instead of failing deep inside the chat request.

**Architecture:** add a parallel `MODEL_CAPABILITIES` registry in `korina/util/presets.py` next to the existing `PROVIDER_CAPABILITIES` (Phase 2 work). New `korina/services/model_capability.py` computes per-model capability at request time using a primary signal (`find_mmproj_for_model` for local GGUFs — already in `korina/services/model_catalog.py:46`) and a name-based heuristic as fallback. `/api/models` exposes `*_models_capabilities` dicts alongside the existing model id lists. Frontend gets a tiny `capability-filter.js` that gates the multimodal STT dropdown, with a `?All models` toggle for power users. Compat check happens in `routes/providers.py:activate_provider` before delegating to `activate_llm_provider`.

**Tech Stack:** FastAPI, Pydantic, vanilla JS (no build step, matches Phase 1/2/3 pattern).

**Target branch:** `beta` (per live state; refactor series lives on `beta`; do not touch `alpha` or `master`).

---

## Verified preconditions (2026-06-23)

- `beta` HEAD: `63a85da` (Phase 3 close-out). Source anchor for any "pre-Phase-4" line numbers: `git show 63a85da:<file>`.
- Phase 0, 1, 2, 3: complete on `beta`. Backend modular (Phase 1); provider capabilities contract live (Phase 2); frontend modular into 16 ES modules (Phase 3).
- Live `/api/models` returns 12 keys: `whisper_models`, `llm_models`, `llm_default`, `llm_base_url`, `llm_error`, `stt_llm_models`, `stt_llm_default`, `stt_llm_base_url`, `stt_llm_error`, `llama_cpp_local_models`, `lmstudio_catalog_models`, `labels`. **No capabilities anywhere.**
- Live `/api/capabilities` returns `providers` (4 response-LLM) + `agent_providers` (2 agent) + `version: 1`. Provider caps already include `model_sources` per provider.
- `korina/util/presets.py`: 114 lines, single source of truth for provider caps (Phase 2). Phase 4 adds `MODEL_CAPABILITIES` next to it.
- `korina/services/model_catalog.py:46` already has `find_mmproj_for_model(model_path) -> str` — returns the matching `mmproj*.gguf` if one exists in the same directory. **This is the primary signal for multimodal support on local GGUFs**, far more authoritative than name heuristics.
- `korina/schemas_capabilities.py`: Pydantic for provider caps. Phase 4 adds `ModelCapabilities` schema.
- `Korina/js/providers-ui.js:125` is the **only** site that populates `sttLlmModel` `<select>`. Phase 4 modifies this loop to filter.
- `Korina/js/providers-ui.js:133` is `loadAgentModelOptions()` for the agent dropdown (Phase 4 does **not** filter this one).
- `korina/routes/providers.py:18-37` (`activate_provider`): accepts `provider` + `model`, no compat check. Phase 4 adds a `provider_supports_model()` call before line 34.
- Live `llama_cpp_local_models` (29 entries at HEAD `63a85da`): includes both multimodal-capable (`gemma-4-E2B-it-Q8_0.gguf`, `Qwen3-VL-4B-Instruct-Q4_K_M.gguf`, `ultravox-v0_5-llama-3_1-8b-GGUF/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf`) and text-only (`Qwen3.5-2B-BF16.gguf`, `Qwen3.5-9B-Q4_K_M.gguf`, `gemma-4-12B-it-QAT-Q4_0.gguf`, `gemma-4-31B-it-Q8_0.gguf`, all `gemma4-coding-Q*.gguf`).
- Live runtime `config.json`: `llm_default`, `stt_llm_default`, and `agent_model` all = `gemma-4-E2B-it-Q8_0.gguf`. User is currently running the multimodal-capable E2B model for everything. The filter will preserve this default selection.

---

## Design decisions baked into this plan (override at any task)

| Decision | Choice | Override cost |
|---|---|---|
| Multimodal filter scope | **Multimodal STT dropdown only.** Response-LLM and agent dropdowns show all models. | medium — would touch settings UI markup |
| "All models" toggle | Yes, per blueprint §4.3. Default = filtered. Toggle persists in `state.capabilityFilterOverride` (in-memory, no backend change). | low — UI toggle only |
| Allowlist config field | `multimodal_stt_model_allowlist: list[str]` (per blueprint §4.1) | low — single config field |
| Primary capability signal (local GGUF) | `find_mmproj_for_model(model_path) != ""` returns `True` for `supports_audio_input` | low — already implemented |
| Fallback name heuristic | model id (lower) contains `"vision"` / `"audio"` / `"ultravox"` / `"-vl-"` / `"qwen2-audio"`, OR exact match in allowlist, OR matches the small hardcoded list `("gemma-4-e2b", "gpt-4o-audio", "ultravox", "qwen2-audio", "qwen3-vl")` (case-insensitive substring) | low — pure function |
| Embedding/TTS models | `nomic-embed-text-*`, `orpheus-*` → `supports_audio_input = False` (explicit blacklist via substring match) | low |
| Capability source field | `source: "endpoint_loaded" \| "local_gguf" \| "catalog"` (matches Phase 2 `model_sources` enum in `schemas_capabilities.py:13`) | n/a |
| `/api/models` shape | Add `llm_models_capabilities` and `stt_llm_models_capabilities` dicts alongside existing list fields. Keep all existing fields. | low |
| Compat check | Raise `HTTPException(status_code=400, detail=...)` in `routes/providers.py:activate_provider` before calling `activate_llm_provider()` | low |
| Compat check rules | llama.cpp + local GGUF: file exists on disk; llama.cpp + non-GGUF: 400; lmstudio + catalog id: must be in `lmstudio_catalog_models`; lmstudio + local GGUF: 400; ollama: model must be in `/v1/models` (only checkable after server is up — for now, accept anything); openai-compatible: always allow | medium — would touch `services/provider_manager.py` |
| `provider_supports_model()` location | `korina/services/provider_manager.py` (matches blueprint §4.4) | low |
| Capability endpoint version bump | `CapabilitiesResponse.version` stays at 1; new `ModelCapabilitiesResponse.version = 1` (separate schema, separate route or expand `/api/models`) | low |
| Push pattern | One commit per task to `beta` only; do **not** touch `alpha` or `master` | n/a |
| Existing `/api/models` consumers | None broken — every existing field preserved verbatim, capabilities are additive | n/a |

---

## Phase 4 overview

| # | Step | LOC touched | Risk |
|---|---|---|---|
| 4.1 | Backend `MODEL_CAPABILITIES` registry + `get_model_capability()` + allowlist config | ~120 | low |
| 4.2 | Surface capabilities in `/api/models` (additive — preserves existing shape) | ~50 | low |
| 4.3 | Frontend capability filter + "All models" toggle (multimodal STT dropdown only) | ~120 | medium (user-visible filter) |
| 4.4 | Provider-model compatibility check in activate route | ~80 | medium (rejects previously-allowed combos) |
| 4.5 | Phase 4 verification | n/a | n/a |

Each step ends with a working tree, a green smoke run, and a commit on `beta`.

---

## Step 4.1 — Backend `MODEL_CAPABILITIES` registry + `get_model_capability()`

**Goal:** one Python function returns a per-model capability dict. Reads from a `MODEL_CAPABILITIES` registry (parallel to `PROVIDER_CAPABILITIES` from Phase 2) for **explicitly-listed** models, falls back to a name-based heuristic for everything else.

**Files:**
- Modify: `korina/util/presets.py` (add `MODEL_CAPABILITIES` registry)
- Create: `korina/services/model_capability.py` (`get_model_capability()` + `_heuristic_capability()` + allowlist)
- Modify: `korina/config.py` (add `multimodal_stt_model_allowlist` default + getter if not present)
- Modify: `tests/regression_smoke.py` (add a `test_model_capability_heuristic()` block)

### Task 4.1.1 — Add `MODEL_CAPABILITIES` registry

**Files:** Modify `korina/util/presets.py`

Append to the bottom of the existing registry (after `PROVIDER_CAPABILITIES`):

```python
# Per-model capability registry. Same shape as the heuristic in
# get_model_capability(), but explicitly opt-in. Models listed here
# ALWAYS use the listed values; the heuristic only runs for models
# not in this registry.
#
# Capability keys (snake_case JSON, served verbatim by /api/models
# *_models_capabilities):
#   supports_audio_input: bool    -- can the model accept audio input
#                                    for the multimodal STT path
#   source: str                   -- one of: endpoint_loaded, local_gguf,
#                                    catalog, inferred
#   has_mmproj: bool              -- only meaningful for local_gguf:
#                                    True iff find_mmproj_for_model() != ""
#   approx_vram_gb: float | None  -- when known; None for unknown
MODEL_CAPABILITIES: dict[str, dict[str, Any]] = {
    # ---- Explicitly-known multimodal-capable GGUFs on this box ----
    "gemma-4-e2b": {
        "supports_audio_input": True,
        "source": "local_gguf",
        "has_mmproj": True,
        "approx_vram_gb": 4.0,
    },
    "qwen3-vl-4b": {
        "supports_audio_input": False,  # vision only, no audio input
        "source": "local_gguf",
        "has_mmproj": True,
        "approx_vram_gb": 3.5,
    },
    "ultravox-v0.5-llama-3.1-8b": {
        "supports_audio_input": True,
        "source": "local_gguf",
        "has_mmproj": True,
        "approx_vram_gb": 6.0,
    },
    # ---- Explicitly-known text-only / non-multimodal GGUFs ----
    "nomic-embed-text-v1.5": {
        "supports_audio_input": False,
        "source": "local_gguf",
        "has_mmproj": False,
        "approx_vram_gb": 0.3,
    },
    "nomic-embed-text-v2-moe": {
        "supports_audio_input": False,
        "source": "local_gguf",
        "has_mmproj": False,
        "approx_vram_gb": 0.5,
    },
    "orpheus-3b": {
        "supports_audio_input": False,
        "source": "local_gguf",
        "has_mmproj": False,
        "approx_vram_gb": 2.0,
    },
}


def _lookup_registry_capability(model_id: str) -> dict[str, Any] | None:
    """Look up a model by lowercased substring. Returns the first match."""
    if not model_id:
        return None
    needle = model_id.lower()
    for key, cap in MODEL_CAPABILITIES.items():
        if key in needle:
            return dict(cap)  # return a copy so callers can't mutate registry
    return None
```

> **Important:** the registry entries use **substring keys** (e.g. `"gemma-4-e2b"`), not exact ids, so a path like `/home/roggoz/Disks/SN750/models/lmstudio-community/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q8_0.gguf` matches the `"gemma-4-e2b"` key.

**Step:** Commit:
```bash
git add korina/util/presets.py
git commit -m "feat: MODEL_CAPABILITIES registry with explicit-known entries (4.1.1)"
```

### Task 4.1.2 — Create `korina/services/model_capability.py`

**Files:** Create `korina/services/model_capability.py`

```python
"""Per-model capability detection.

Returns capability metadata for any model id the user can pick in the
UI. The shape matches `ModelCapabilities` in korina/schemas_capabilities.py.

Strategy:
  1. If the model id is in MODEL_CAPABILITIES (substring match),
     return the explicit entry.
  2. Otherwise, compute a heuristic based on:
     a. find_mmproj_for_model(model_id) -- for local GGUFs
     b. name substring matches: vision/audio/ultravox/vl/qwen2-audio
     c. embedding/TTS blacklist: nomic-embed-*, orpheus-*
  3. If still unknown, default to supports_audio_input=False.

Heuristic is a UX filter, not a truth claim. Users can override via
the `?All models` dropdown toggle in the multimodal STT UI.
"""

from __future__ import annotations

import os
from typing import Any

from korina.util.presets import (
    MODEL_CAPABILITIES,
    _lookup_registry_capability,
)


# Name-substring patterns that strongly suggest audio input support.
# Substring (not regex) matching; lowercased.
_MULTIMODAL_HINTS = ("vision", "audio", "ultravox", "-vl-", "qwen2-audio", "qwen3-vl")

# Name-substring patterns that explicitly mark a model as non-multimodal
# even if it has an mmproj (e.g. embeddings always have one for completeness,
# TTS models don't consume audio input).
_NON_MULTIMODAL_HINTS = ("nomic-embed", "orpheus-", "embed-text")

# Hardcoded allowlist per blueprint §4.1.
_HARD_ALLOWLIST = ("gemma-4-e2b", "gpt-4o-audio", "ultravox", "qwen2-audio", "qwen3-vl")


def _has_mmproj(model_id: str) -> bool:
    """True iff find_mmproj_for_model() finds a matching mmproj file.

    Imported lazily to avoid a top-level circular import (model_catalog
    imports util.paths which is fine, but keeping the import local makes
    the dependency direction obvious).
    """
    try:
        from korina.services.model_catalog import find_mmproj_for_model
        return bool(find_mmproj_for_model(model_id))
    except Exception:
        return False


def _heuristic_capability(model_id: str) -> dict[str, Any]:
    """Compute capability from name and filesystem signals.

    The result is a best-effort filter, not a truth claim. Defaults to
    `supports_audio_input=False` when no positive signal is present.
    """
    if not model_id:
        return _default_capability(model_id, reason="empty")

    needle = model_id.lower()
    name = os.path.basename(needle)

    # Explicit blacklist: embeddings and TTS models are never multimodal.
    for hint in _NON_MULTIMODAL_HINTS:
        if hint in needle:
            return {
                "supports_audio_input": False,
                "source": "local_gguf",
                "has_mmproj": _has_mmproj(model_id),
                "approx_vram_gb": None,
                "_inference": "blacklist",
            }

    # Positive signal 1: model has a real mmproj file on disk.
    if model_id.endswith(".gguf") and _has_mmproj(model_id):
        return {
            "supports_audio_input": True,
            "source": "local_gguf",
            "has_mmproj": True,
            "approx_vram_gb": None,
            "_inference": "mmproj_present",
        }

    # Positive signal 2: allowlist hit.
    for allowed in _HARD_ALLOWLIST:
        if allowed in needle:
            return {
                "supports_audio_input": True,
                "source": "local_gguf" if model_id.endswith(".gguf") else "endpoint_loaded",
                "has_mmproj": False,
                "approx_vram_gb": None,
                "_inference": f"allowlist:{allowed}",
            }

    # Positive signal 3: name heuristic.
    for hint in _MULTIMODAL_HINTS:
        if hint in needle or hint in name:
            return {
                "supports_audio_input": True,
                "source": "local_gguf" if model_id.endswith(".gguf") else "endpoint_loaded",
                "has_mmproj": False,
                "approx_vram_gb": None,
                "_inference": f"name_hint:{hint}",
            }

    return _default_capability(model_id, reason="no_signal")


def _default_capability(model_id: str, reason: str) -> dict[str, Any]:
    return {
        "supports_audio_input": False,
        "source": "inferred",
        "has_mmproj": False,
        "approx_vram_gb": None,
        "_inference": reason,
    }


def get_model_capability(
    model_id: str,
    *,
    allowlist: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Return capability metadata for a single model id.

    Lookup order:
      1. Runtime allowlist (from config.multimodal_stt_model_allowlist)
         — exact id match (case-sensitive).
      2. MODEL_CAPABILITIES registry (substring match, lowercased).
      3. Heuristic.

    The `_inference` key is internal — schemas_capabilities strips it
    before serialization. It exists so debug logs can show why the
    heuristic made a particular decision.
    """
    if allowlist and model_id in allowlist:
        return {
            "supports_audio_input": True,
            "source": "endpoint_loaded",
            "has_mmproj": False,
            "approx_vram_gb": None,
            "_inference": "runtime_allowlist",
        }

    explicit = _lookup_registry_capability(model_id)
    if explicit is not None:
        explicit["_inference"] = "registry"
        return explicit

    return _heuristic_capability(model_id)
```

> **Note on the `_inference` field:** it is internal and will be stripped by `ModelCapabilities` schema (see Task 4.2.1). Kept here for debuggability — when a user asks "why is X filtered out?", the answer is in the log.

**Step:** Commit:
```bash
git add korina/services/model_capability.py
git commit -m "feat: model capability detection with heuristic + registry (4.1.2)"
```

### Task 4.1.3 — Add `multimodal_stt_model_allowlist` config field

**Files:** Modify `korina/config.py`

First, check whether `config.example.json` already has this field (it shouldn't — blueprint §4.1 says "Add a way to opt out per model" so it's a Phase 4 addition).

```bash
grep -n "multimodal_stt_model_allowlist" /home/roggoz/Korina-Agent/Korina/config/config.example.json
```

If not present, add to `Korina/config/config.example.json` (next to the other STT/LLM fields, around line 60):

```json
  "multimodal_stt_model_allowlist": [],
```

Empty array default = no opt-in override. Users can add model ids to this array to force-enable models the heuristic filters out.

Also add a getter in `korina/config.py`:

```python
def config_multimodal_stt_model_allowlist(c: dict | None = None) -> tuple[str, ...]:
    """Return the user-configured allowlist as an immutable tuple.

    Used by routes/models.py to override the heuristic for users who
    want to opt in specific text-only models (e.g. running Qwen3.5-9B
    with clever prompting as a multimodal STT).
    """
    cfg = c if c is not None else load_config()
    raw = cfg.get("multimodal_stt_model_allowlist") or []
    if not isinstance(raw, list):
        return ()
    return tuple(str(x) for x in raw if isinstance(x, str) and x.strip())
```

**Step:** Commit:
```bash
git add korina/config.py Korina/config/config.example.json
git commit -m "feat: multimodal_stt_model_allowlist config field (4.1.3)"
```

### Task 4.1.4 — Regression test for the heuristic

**Files:** Modify `tests/regression_smoke.py`

Add a new test function after the existing capability tests (~line 480 in the current source). Run from source checkout (`cd /home/roggoz/Korina-Agent`), not runtime — the script's `git ls-files` check requires it.

```python
def test_model_capability_heuristic():
    """Phase 4.1 -- the model capability detector must identify multimodal
    GGUFs via mmproj presence, the explicit registry, and the allowlist;
    and must NOT false-positive on embeddings, TTS, or plain Qwen text models.
    """
    import importlib
    mc = importlib.import_module("korina.services.model_capability")

    # 1. gemma-4-E2B (live default) MUST be multimodal.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q8_0.gguf"
    )
    assert cap["supports_audio_input"] is True, (
        f"gemma-4-E2B-it-Q8_0.gguf not flagged multimodal: {cap}"
    )
    assert cap["_inference"] in ("registry", "allowlist:gemma-4-e2b",
                                 "name_hint:gemma-4-e2b"), cap

    # 2. Qwen3-VL must have mmproj but supports_audio_input=False (vision only).
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "Qwen3-VL-4B-Instruct-GGUF/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    )
    assert cap["supports_audio_input"] is False, (
        f"Qwen3-VL wrongly flagged as audio-capable: {cap}"
    )

    # 3. Plain Qwen3.5 text model MUST NOT be multimodal.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "Qwen3.5-2B-GGUF/Qwen3.5-2B-Q8_0.gguf"
    )
    assert cap["supports_audio_input"] is False, cap
    assert cap["_inference"] in ("no_signal", "blacklist"), cap

    # 4. nomic-embed MUST be blacklisted even though it has mmproj.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/nomic-ai/"
        "nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.f32.gguf"
    )
    assert cap["supports_audio_input"] is False, cap
    assert cap["_inference"] == "blacklist", cap

    # 5. Allowlist override: text-only model in allowlist becomes multimodal.
    cap = mc.get_model_capability(
        "/home/roggoz/Disks/SN750/models/lmstudio-community/"
        "Qwen3.5-2B-GGUF/Qwen3.5-2B-Q8_0.gguf",
        allowlist=("/home/roggoz/Disks/SN750/models/lmstudio-community/"
                   "Qwen3.5-2B-GGUF/Qwen3.5-2B-Q8_0.gguf",),
    )
    assert cap["supports_audio_input"] is True, cap
    assert cap["_inference"] == "runtime_allowlist", cap
```

Wire it into `run()` next to the existing capability tests (around the `test_capabilities_endpoint()` block):

```python
    test_model_capability_heuristic()
```

**Step:** Verify:
```bash
cd /home/roggoz/Korina-Agent && python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe 2>&1 | tail -3
# Expect: "All checks passed against http://127.0.0.1:8001."
```

**Step:** Commit:
```bash
git add tests/regression_smoke.py
git commit -m "test: cover model capability heuristic + allowlist override (4.1.4)"
```

---

## Step 4.2 — Surface capabilities in `/api/models`

**Goal:** add `llm_models_capabilities` and `stt_llm_models_capabilities` dicts to the existing `/api/models` response, parallel to the existing list fields. All existing fields preserved.

**Files:**
- Modify: `korina/routes/models.py` (add capability computation per model)
- Modify: `korina/schemas_capabilities.py` (add `ModelCapabilities` schema, strip `_inference`)

### Task 4.2.1 — Add `ModelCapabilities` schema

**Files:** Modify `korina/schemas_capabilities.py`

Append:

```python
from typing import Optional

# Phase 4.2 model-level capabilities. Mirrors the shape returned by
# korina.services.model_capability.get_model_capability(). The `_inference`
# internal key is excluded from the serialized response.

class ModelCapabilities(BaseModel):
    supports_audio_input: bool
    source: Literal["endpoint_loaded", "local_gguf", "catalog", "inferred"]
    has_mmproj: bool = False
    approx_vram_gb: Optional[float] = None
```

The `_inference` field is internal and will not appear in JSON serialization because Pydantic's `BaseModel` only outputs declared fields.

**Step:** Commit:
```bash
git add korina/schemas_capabilities.py
git commit -m "feat(schemas): ModelCapabilities for /api/models (4.2.1)"
```

### Task 4.2.2 — Populate `_models_capabilities` in `/api/models`

**Files:** Modify `korina/routes/models.py`

After the existing model-list assembly (around line 60), add capability computation:

```python
    # Phase 4.2: per-model capabilities for the multimodal-STT filter
    # and the response-LLM info dropdown. Cached per request so we
    # don't recompute on every model in the list.
    from korina.services.model_capability import get_model_capability
    from korina.config import config_multimodal_stt_model_allowlist
    _allowlist = config_multimodal_stt_model_allowlist(config)

    def _cap_for(mid: str) -> dict:
        cap = get_model_capability(mid, allowlist=_allowlist)
        # Strip the internal _inference key before returning.
        return {k: v for k, v in cap.items() if not k.startswith("_")}

    llm_models_capabilities = {m: _cap_for(m) for m in llm_models}
    stt_llm_models_capabilities = {m: _cap_for(m) for m in stt_llm_models}
```

Then add the two new fields to the returned dict (insert next to the existing `llm_models` and `stt_llm_models` keys):

```python
    return {
        'whisper_models': WHISPER_MODEL_CHOICES,
        'llm_models': llm_models,
        'llm_models_capabilities': llm_models_capabilities,
        'llm_default': str(config.get('lm_model') or LMSTUDIO_MODEL),
        ...
        'stt_llm_models': stt_llm_models,
        'stt_llm_models_capabilities': stt_llm_models_capabilities,
        ...
    }
```

**Step:** Verify with curl:
```bash
ssh -F /dev/null -o User=roggoz roggoz@100.71.89.62 \
  "cd /home/roggoz/Korina-Agent && python3 -c \"
import urllib.request, json
b = json.loads(urllib.request.urlopen('http://127.0.0.1:8001/api/models').read())
print('llm_models_capabilities keys:', len(b.get('llm_models_capabilities', {})))
print('stt_llm_models_capabilities keys:', len(b.get('stt_llm_models_capabilities', {})))
print('sample gemma-4-E2B cap:', {k: v for k, v in b.get('stt_llm_models_capabilities', {}).items() if 'gemma-4-E2B' in k})
\""
# Expect: 0 + 0 + empty (service has to be restarted to pick up the new field)
# OR: N + N + gemma-4-E2B.supports_audio_input = True
```

> **Note:** the running service was started before this change. To pick up the new field, the service must be restarted. The cron execution recipe restarts the service as needed (Task 4.5.2). For this task, restart is **deferred** — verifying the source compiles and the regression suite passes is sufficient.

```bash
python3 -m py_compile korina/routes/models.py  # expect: no error
cd /home/roggoz/Korina-Agent && python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe 2>&1 | tail -3
# Expect: regression still passes (the route change is additive)
```

**Step:** Commit:
```bash
git add korina/routes/models.py
git commit -m "feat(models): /api/models returns per-model capabilities (4.2.2)"
```

### Task 4.2.3 — Regression test for the new `/api/models` fields

**Files:** Modify `tests/regression_smoke.py`

Add next to existing `/api/models` test:

```python
def test_api_models_includes_capabilities():
    """Phase 4.2 -- /api/models MUST include llm_models_capabilities and
    stt_llm_models_capabilities dicts, and the live default
    (gemma-4-E2B-it-Q8_0.gguf) MUST be flagged as supports_audio_input=True
    in stt_llm_models_capabilities.
    """
    import urllib.request, json
    body = json.loads(http_get(BASE, "/api/models")[1])
    assert "llm_models_capabilities" in body, \
        "/api/models missing llm_models_capabilities"
    assert "stt_llm_models_capabilities" in body, \
        "/api/models missing stt_llm_models_capabilities"
    assert isinstance(body["llm_models_capabilities"], dict)
    assert isinstance(body["stt_llm_models_capabilities"], dict)
    # The live multimodal STT default is gemma-4-E2B -- it MUST show up
    # as multimodal-capable. (It will only be in the dict if the local
    # llama.cpp is up; if not, skip the per-model assertion.)
    audio_capable = [
        m for m, c in body["stt_llm_models_capabilities"].items()
        if c.get("supports_audio_input") is True
    ]
    if audio_capable:
        assert any("gemma-4-E2B" in m for m in audio_capable), \
            f"gemma-4-E2B not in multimodal set: {audio_capable}"
```

Wire into `run()` near the existing `/api/models` test.

**Step:** Commit:
```bash
git add tests/regression_smoke.py
git commit -m "test: /api/models includes per-model capabilities (4.2.3)"
```

---

## Step 4.3 — Frontend capability filter + "All models" toggle

**Goal:** the multimodal STT dropdown (`sttLlmModel`) shows only models where `supports_audio_input === true`. A `?All models` toggle at the top of the dropdown bypasses the filter. Response-LLM and agent dropdowns are unchanged.

**Files:**
- Create: `Korina/js/capability-filter.js`
- Modify: `Korina/js/providers-ui.js` (gate the stt_llm population)
- Modify: `Korina/js/app.js` (import + window re-export the new module)
- Modify: `Korina/index.html` (add the `?All models` toggle near the stt dropdown)
- Modify: `tests/regression_smoke.py` (frontend test)

### Task 4.3.1 — Create `Korina/js/capability-filter.js`

**Files:** Create `Korina/js/capability-filter.js`

```js
// js/capability-filter.js
//
// Pure helpers for filtering model lists by capability. No DOM, no fetch.
// Imported by providers-ui.js to gate the multimodal STT dropdown.

import { state } from './state.js';

/**
 * Return the subset of model ids that match the capability requirement.
 *
 * @param {string[]} modelIds      -- all available model ids
 * @param {Object<string,Object>} capabilitiesDict  -- {modelId: {supports_audio_input: bool, ...}}
 * @param {Object} requirement
 *   { kind: "any" }                  -- no filter
 *   { kind: "audio_input" }          -- requires supports_audio_input === true
 * @returns {string[]} filtered subset
 */
export function filterModelsByCapability(modelIds, capabilitiesDict, requirement) {
  if (!Array.isArray(modelIds)) return [];
  if (!requirement || requirement.kind === 'any') return modelIds.slice();
  if (requirement.kind === 'audio_input') {
    return modelIds.filter((mid) => {
      const cap = capabilitiesDict && capabilitiesDict[mid];
      return cap && cap.supports_audio_input === true;
    });
  }
  // Unknown requirement kind: fail safe (no filter).
  return modelIds.slice();
}

/**
 * Toggle the "All models" override. Persisted in state.capabilityFilterOverride
 * for the session lifetime (no backend change).
 *
 * @param {boolean} enabled  -- true = show all models regardless of capability
 */
export function setCapabilityFilterOverride(enabled) {
  state.capabilityFilterOverride = !!enabled;
}

/**
 * Return the requirement object to pass to filterModelsByCapability for the
 * multimodal STT dropdown, honoring the user's "All models" override.
 */
export function sttLlmModelRequirement() {
  if (state.capabilityFilterOverride) return { kind: 'any' };
  return { kind: 'audio_input' };
}
```

**Step:** Commit:
```bash
git add Korina/js/capability-filter.js
git commit -m "feat(frontend): capability-filter.js for multimodal STT dropdown (4.3.1)"
```

### Task 4.3.2 — Wire the filter into `providers-ui.js`

**Files:** Modify `Korina/js/providers-ui.js`

Add to imports (top of file, after existing imports):

```js
import {
  filterModelsByCapability,
  sttLlmModelRequirement,
  setCapabilityFilterOverride,
} from './capability-filter.js';
```

Modify the stt_llm dropdown population loop (around `providers-ui.js:125`). Current code:

```js
for(const m of (j.stt_llm_models||[])){ const o=document.createElement("option"); o.value=m; o.textContent=(j.labels&&j.labels[m])||prettyModelLabel(m); $("sttLlmModel").appendChild(o); }
```

New code:

```js
const sttRequirement = sttLlmModelRequirement();
const sttModelsFiltered = filterModelsByCapability(
  j.stt_llm_models || [],
  j.stt_llm_models_capabilities || {},
  sttRequirement,
);
for(const m of sttModelsFiltered){
  const o=document.createElement("option");
  o.value=m;
  o.textContent=(j.labels&&j.labels[m])||prettyModelLabel(m);
  $("sttLlmModel").appendChild(o);
}
// Informational: how many were filtered out.
const sttHidden = (j.stt_llm_models || []).length - sttModelsFiltered.length;
if (sttHidden > 0 && $("settingsInfo")) {
  const current = $("settingsInfo").textContent;
  $("settingsInfo").textContent = `${current} · ${sttHidden} hidden by audio capability filter (toggle ?All models to show)`.trim();
}
```

> **Note:** the response-LLM dropdown (`llmBaseUrl`/`llmModel` population, around line 122) is **not modified** — per the design decision, only multimodal STT is filtered.

**Step:** Commit:
```bash
git add Korina/js/providers-ui.js
git commit -m "feat(frontend): filter multimodal STT dropdown by audio capability (4.3.2)"
```

### Task 4.3.3 — Add `?All models` toggle UI

**Files:** Modify `Korina/index.html`

Around the `sttLlmModel` `<select>` (line 50-52 area), add a toggle link/checkbox:

Find the line containing `<select id="sttLlmModel">` and add directly after the `</select>`:

```html
<div class="settingsGroupNote">
  <label class="toggleLine">
    <input type="checkbox" id="sttCapabilityFilterOverride">
    Show all models (ignore audio-capability filter)
  </label>
</div>
```

Then in `Korina/js/app.js` (already imports `state`), add the wiring at the bottom (just before the `await loadCapabilities()...` call):

```js
// Wire the multimodal STT capability filter override.
const _sttOverride = $('sttCapabilityFilterOverride');
if (_sttOverride) {
  _sttOverride.addEventListener('change', () => {
    setCapabilityFilterOverride(_sttOverride.checked);
    // Reload the model list so the dropdown reflects the change immediately.
    (async () => {
      try { await loadModelOptions(true); } catch (e) { /* best effort */ }
    })();
  });
}
```

Add `setCapabilityFilterOverride` to the `Object.assign(window, ...)` re-export block in `app.js` so the toggle handler can call it without an import cycle (and so the test runner can poke it from DevTools).

**Step:** Commit:
```bash
git add Korina/index.html Korina/js/app.js
git commit -m "feat(frontend): ?All models toggle for multimodal STT filter (4.3.3)"
```

### Task 4.3.4 — Frontend wiring regression test

**Files:** Modify `tests/regression_smoke.py`

```python
def test_frontend_multimodal_stt_filter():
    """Phase 4.3 -- the served Korina/js/capability-filter.js must export
    filterModelsByCapability that drops non-multimodal models, and the
    served index.html must include the All models toggle near the
    sttLlmModel select."""
    import urllib.request, re
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode("utf-8")

    # Toggle present.
    assert 'id="sttCapabilityFilterOverride"' in html, \
        "All models toggle missing from index.html"

    # capability-filter.js is loaded as a module.
    assert re.search(r'<script[^>]*type="module"[^>]*src="\./js/app\.js"', html), \
        "index.html no longer loads app.js as module"
    # (capability-filter.js is imported by providers-ui.js, which app.js loads;
    # verifying the served file exists is sufficient.)
    with urllib.request.urlopen(BASE + "/Korina/js/capability-filter.js", timeout=10) as r:
        js = r.read().decode("utf-8")
    assert "filterModelsByCapability" in js, \
        "filterModelsByCapability not exported from capability-filter.js"
    assert "sttLlmModelRequirement" in js
```

Wire into `run()` near the existing frontend tests.

**Step:** Verify the static checks (capability-filter.js + toggle exist on disk). Live `?All models` interaction is manual and not in the regression.

**Step:** Commit:
```bash
git add tests/regression_smoke.py
git commit -m "test: cover multimodal STT filter + ?All models toggle (4.3.4)"
```

---

## Step 4.4 — Provider-model compatibility check

**Goal:** `POST /api/llm/provider/activate` validates that `(provider, model)` is compatible before delegating to `activate_llm_provider`. Incompatible combos return HTTP 400 with a clear error.

**Files:**
- Modify: `korina/services/provider_manager.py` (add `provider_supports_model()`)
- Modify: `korina/routes/providers.py` (call the check in `activate_provider`)
- Modify: `tests/regression_smoke.py` (compat test)

### Task 4.4.1 — Add `provider_supports_model()`

**Files:** Modify `korina/services/provider_manager.py`

Append:

```python
def provider_supports_model(provider: str, model: str) -> tuple[bool, str]:
    """Return (supported, reason).

    Phase 4.4: pre-flight check for /api/llm/provider/activate. Raises
    no exceptions; returns (False, reason) for the caller to surface
    as HTTP 400.

    Rules:
      - llama.cpp + local GGUF path: file must exist on disk
      - llama.cpp + endpoint-loaded id (not a path): NOT supported
      - lmstudio + catalog id: must be in lmstudio_catalog_models()
      - lmstudio + GGUF path: NOT supported
      - ollama: always allow (model list is server-side, only
        verifiable after server start)
      - openai-compatible: always allow (user-provided endpoint)
      - anthropic: always allow (user-provided endpoint)
    """
    from pathlib import Path
    from korina.services.model_catalog import (
        discover_lmstudio_catalog_models,
        discover_local_gguf_models,
    )

    pid = str(provider or "").strip()
    mid = str(model or "").strip()

    if pid in ("openai-compatible", "anthropic"):
        return (True, "user_provided_endpoint")

    if pid == "ollama":
        # Cannot pre-check; ollama's /v1/models is only reachable once
        # the server is up. Allow and let the chat request fail loud.
        return (True, "ollama_endpoint_checked_later")

    if pid == "llama.cpp":
        if not mid:
            return (False, "llama.cpp requires a model id (local GGUF path)")
        if not mid.endswith(".gguf"):
            return (False, f"llama.cpp does not support endpoint-loaded ids; got '{mid}'")
        if not Path(mid).exists():
            return (False, f"llama.cpp model file not found: {mid}")
        # Sanity: must be in our local GGUF discovery (best-effort).
        try:
            if mid not in discover_local_gguf_models():
                return (False, f"llama.cpp model not in local GGUF roots: {mid}")
        except Exception:
            pass  # filesystem walk failed; allow and let llama-server reject
        return (True, "local_gguf")

    if pid == "lmstudio":
        if not mid:
            return (False, "lmstudio requires a model id (catalog id)")
        try:
            catalog = discover_lmstudio_catalog_models()
        except Exception:
            catalog = []
        if mid in catalog:
            return (True, "lmstudio_catalog")
        return (False, f"lmstudio catalog does not contain '{mid}'")

    return (False, f"unknown provider '{pid}'")
```

**Step:** Commit:
```bash
git add korina/services/provider_manager.py
git commit -m "feat: provider_supports_model() pre-flight check (4.4.1)"
```

### Task 4.4.2 — Wire compat check into activate route

**Files:** Modify `korina/routes/providers.py`

Insert after the existing `provider = str(req.provider ...)` line (around line 21) and **before** the `activate_llm_provider` call (line 34):

```python
    # Phase 4.4: pre-flight provider/model compatibility check.
    if req.model:
        from korina.services.provider_manager import provider_supports_model
        supported, reason = provider_supports_model(provider, str(req.model).strip())
        if not supported:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "incompatible_provider_model",
                    "provider": provider,
                    "model": str(req.model).strip(),
                    "reason": reason,
                },
            )
```

**Step:** Verify by manually probing:
```bash
ssh -F /dev/null -o User=roggoz roggoz@100.71.89.62 \
  'cd /home/roggoz/Korina-Agent && python3 -c "
import urllib.request, json
body = json.dumps({\"provider\": \"llama.cpp\", \"model\": \"qwen/qwen3-14b\"}).encode()
req = urllib.request.Request(\"http://127.0.0.1:8001/api/llm/provider/activate\", data=body, headers={\"Content-Type\":\"application/json\"}, method=\"POST\")
try:
    urllib.request.urlopen(req, timeout=10)
except urllib.error.HTTPError as e:
    print(\"status:\", e.status)
    print(\"body:\", e.read().decode())
"'
# Expect: HTTP 400 with detail.error == "incompatible_provider_model"
```

(Requires service restart to pick up route change. Phase 4.5.2 handles restart.)

**Step:** Commit:
```bash
git add korina/routes/providers.py
git commit -m "feat(routes): activate endpoint validates provider/model compat (4.4.2)"
```

### Task 4.4.3 — Regression test for compat check

**Files:** Modify `tests/regression_smoke.py`

```python
def test_provider_model_compatibility():
    """Phase 4.4 -- /api/llm/provider/activate MUST 400 when given a
    model that the named provider cannot serve. The live default config
    uses llama.cpp + a local GGUF, so the test must:
      1. Expect 400 for llama.cpp + an LM Studio catalog id.
      2. Expect 200 for llama.cpp + a known local GGUF (after restart).
    The 200 case requires the service to have picked up the new code;
    if the service is still on the pre-Phase-4 binary, we accept 200
    for case 2 and only fail on case 1."""
    import urllib.request, urllib.error, json
    # Case 1: llama.cpp + catalog id MUST 400 (regardless of restart).
    body = json.dumps({"provider": "llama.cpp", "model": "qwen/qwen3-14b"}).encode()
    req = urllib.request.Request(
        BASE + "/api/llm/provider/activate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected 400 for llama.cpp + catalog id, got 200")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"expected 400, got {e.code}: {e.read().decode()}"
        detail = json.loads(e.read().decode()).get("detail", {})
        assert detail.get("error") == "incompatible_provider_model", \
            f"unexpected detail shape: {detail}"
        assert detail.get("provider") == "llama.cpp"
        assert detail.get("model") == "qwen/qwen3-14b"

    # Case 2: lmstudio + bogus catalog id MUST 400.
    body = json.dumps({"provider": "lmstudio", "model": "fake-org/fake-model"}).encode()
    req = urllib.request.Request(
        BASE + "/api/llm/provider/activate", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected 400 for lmstudio + bogus catalog id, got 200")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"expected 400, got {e.code}: {e.read().decode()}"
```

Wire into `run()` near the existing activate tests.

**Step:** Commit:
```bash
git add tests/regression_smoke.py
git commit -m "test: provider/model compat check on activate (4.4.3)"
```

---

## Step 4.5 — Phase 4 verification

**Goal:** confirm the live service picks up all Phase 4 changes (capabilities in `/api/models`, compat check, frontend filter) and the regression suite stays green.

### Task 4.5.1 — Restart live service

The running service was started before this phase. Restart it to pick up the route changes.

```bash
ssh -F /dev/null -o User=roggoz roggoz@100.71.89.62 \
  'cd /home/roggoz/Korina && (./stop.sh && sleep 2 && ./start.sh) 2>&1 | tail -10'
# Expect: "Started" + service back up.
sleep 3
curl -fsS http://127.0.0.1:8001/api/health | head -c 200
# Expect: {"ok":true,...}
```

> **Note:** the runtime `/home/roggoz/Korina/` already has the new files synced from the source checkout (cron already pushed all 23 Phase 3 commits + the 4.1–4.4 Phase 4 commits). But the Python service needs a restart to re-import `korina.routes.providers` and `korina.routes.models`.

### Task 4.5.2 — Live regression

```bash
cd /home/roggoz/Korina-Agent && python3 tests/regression_smoke.py --base http://127.0.0.1:8001 --no-chat --no-transcribe 2>&1 | tail -3
# Expect: "All checks passed against http://127.0.0.1:8001."
```

If any test fails, **stop and report raw output**. Do not power through.

### Task 4.5.3 — Manual smoke check

```bash
# 1. /api/models now exposes *_models_capabilities
ssh -F /dev/null -o User=roggoz roggoz@100.71.89.62 \
  'curl -fsS http://127.0.0.1:8001/api/models | python3 -c "import sys, json; d=json.load(sys.stdin); print(\"llm_models_capabilities:\", len(d.get(\"llm_models_capabilities\", {}))); print(\"stt_llm_models_capabilities:\", len(d.get(\"stt_llm_models_capabilities\", {}))); print(\"sample gemma-4-E2B stt cap:\", {k: v for k, v in d.get(\"stt_llm_models_capabilities\", {}).items() if \"gemma-4-E2B\" in k})"'

# Expect:
# llm_models_capabilities: <N> (whatever llama.cpp has loaded)
# stt_llm_models_capabilities: <N>
# sample gemma-4-E2B stt cap: { "...gemma-4-E2B-it-Q8_0.gguf": {"supports_audio_input": true, "source": "local_gguf", ...} }

# 2. Activate rejects incompatible combos
ssh -F /dev/null -o User=roggoz roggoz@100.71.89.62 \
  'curl -sS -X POST http://127.0.0.1:8001/api/llm/provider/activate -H "Content-Type: application/json" -d "{\"provider\":\"llama.cpp\",\"model\":\"qwen/qwen3-14b\"}" -w "\nHTTP %{http_code}\n"'
# Expect: HTTP 400, detail.error == "incompatible_provider_model"

# 3. Frontend page now includes the toggle
curl -fsS http://127.0.0.1:8001/Korina/index.html | grep -c 'sttCapabilityFilterOverride'
# Expect: 1

# 4. capability-filter.js serves cleanly
curl -fsS http://127.0.0.1:8001/Korina/js/capability-filter.js | head -3
# Expect: comment + import line
```

### Task 4.5.4 — Update PROGRESS.md and commit

Mark Phase 4 complete in `docs/refactor/PROGRESS.md`:

```markdown
## Phase 4 — Capability registry

- [✓] 4.1 model capability metadata — `MODEL_CAPABILITIES` registry in `korina/util/presets.py`; `get_model_capability()` in `korina/services/model_capability.py` with mmproj + name heuristic + allowlist; `multimodal_stt_model_allowlist` config field. Commits: `...` (TBD).
- [✓] 4.2 /api/models includes capabilities — `llm_models_capabilities` + `stt_llm_models_capabilities` dicts alongside existing list fields; existing fields preserved. `ModelCapabilities` Pydantic schema. Commits: `...` (TBD).
- [✓] 4.3 frontend filters multimodal STT dropdown — `Korina/js/capability-filter.js`; `providers-ui.js` gates the stt_llm population; `?All models` toggle in `index.html` lets power users override. Commits: `...` (TBD).
- [✓] 4.4 provider/model compatibility — `provider_supports_model()` in `korina/services/provider_manager.py`; activate endpoint raises 400 on incompatible combos. Commits: `...` (TBD).
- [✓] 4.5 verify — regression green from source against live service; manual smoke checks pass; live service restarted to pick up route changes. Commits: `...` (TBD).
```

Replace `(TBD)` with the actual SHAs produced by each task.

```bash
git add docs/refactor/PROGRESS.md
git commit -m "docs: mark Phase 4 complete in PROGRESS.md (4.5.4)"
git push origin beta
```

Final verification:
```bash
LOCAL=$(git rev-parse beta)
REMOTE=$(git ls-remote --heads origin beta | awk '{print $1}')
[ "$LOCAL" = "$REMOTE" ] && echo OK || echo MISMATCH
```

---

## Execution handoff

Plan complete. **~12 commits on `beta`**, all behavior-additive (no existing field removed or renamed; multimodal STT dropdown behavior changes from "show all" to "show filtered by default with override toggle").

**Branch:** `beta` (matches Phase 0–3 pattern; do not touch `alpha` or `master`).

**Execution approach:** dispatch a fresh subagent per task via the `subagent-driven-development` skill, with two-stage review (spec compliance, then code quality). One batch. Service restart in 4.5.1 is required for the new `/api/models` fields and the new compat check to take effect.

**Two questions to confirm before I execute:**

1. **Branch:** plan says `beta`. Confirm `beta`?
2. **Pace:** one batch (all ~12 commits in one subagent-driven run), or pause per step (4.1, 4.2, 4.3, 4.4, 4.5) so you can review incremental progress?