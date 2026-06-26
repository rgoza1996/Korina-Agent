from __future__ import annotations

import json

from korina.util.labels import display_model_label

from korina.util.paths import LMSTUDIO_HUB_ROOT

from pathlib import Path


def local_model_roots() -> list[Path]:
    """Resolve the configured GGUF search roots.

    Reads from `config['local_model_roots']` first; falls back to the
    `KORINA_LOCAL_MODEL_ROOTS` env-derived list when the config is empty.
    LMSTUDIO_HUB_ROOT is always appended as a non-user-visible catalog root.
    """
    try:
        from korina.config import config_local_model_roots
        roots = [Path(p) for p in config_local_model_roots()]
    except Exception:
        # Avoid cycles during early bootstrap; fall back to env defaults.
        from korina.util.paths import LOCAL_MODEL_ROOTS
        roots = [Path(p) for p in LOCAL_MODEL_ROOTS]
    if LMSTUDIO_HUB_ROOT not in roots:
        roots.append(LMSTUDIO_HUB_ROOT)
    return roots

def discover_local_gguf_models() -> list[str]:
    found: set[str] = set()
    for root in local_model_roots():
        if not root.exists():
            continue
        for path in root.rglob('*.gguf'):
            lname = path.name.lower()
            if 'mmproj' in lname or lname.endswith('-assistant.gguf'):
                continue
            found.add(str(path))
    return sorted(found, key=lambda s: display_model_label(s).lower())

def discover_lmstudio_catalog_models() -> list[str]:
    found: set[str] = set()
    if LMSTUDIO_HUB_ROOT.exists():
        for manifest in LMSTUDIO_HUB_ROOT.rglob('manifest.json'):
            try:
                body = json.loads(manifest.read_text())
            except Exception:
                continue
            owner = body.get('owner')
            name = body.get('name')
            if isinstance(owner, str) and isinstance(name, str) and owner and name:
                found.add(f'{owner}/{name}')
    return sorted(found)

def find_mmproj_for_model(model_path: str) -> str:
    path = Path(str(model_path or '').strip())
    if not path.exists():
        return ''
    matches = sorted(path.parent.glob('*mmproj*.gguf'))
    return str(matches[0]) if matches else ''




# --- Phase 1.5 helper moved from korina.config ---

def llm_models_for(base_url: str, api_env: str) -> list:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return []
    if base.endswith("/chat/completions"):
        base = base.rsplit("/chat/completions", 1)[0]
    url = f"{base}/models"
    from korina.runtime.http import auth_headers_from_env
    headers = auth_headers_from_env(api_env)
    import urllib.request as _ur
    req = _ur.Request(url, headers=headers, method="GET")
    with _ur.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    models = []
    for item in body.get("data", []):
        mid = item.get("id")
        if isinstance(mid, str) and mid.strip():
            models.append(mid.strip())
    if base == "http://127.0.0.1:8080/v1":
        for local_model in discover_local_gguf_models():
            if local_model not in models:
                models.append(local_model)
    return models
