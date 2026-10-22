from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from collections.abc import Callable, Generator, Iterable
from contextlib import closing

from korina.model_ids import MAX_MODEL_IDS, ModelIds, limit_model_ids, normalize_model_id
from korina.util.labels import display_model_label

from korina.util.paths import LMSTUDIO_HUB_ROOT, LMS_CLI_BIN

from pathlib import Path

MAX_CATALOG_ENTRIES = 10_000
MAX_CATALOG_SOURCE_BYTES = 2 * 1024 * 1024


def _catalog_files(roots: Iterable[Path], info: dict) -> Generator[Path, None, None]:
    """Visit a fixed number of entries without materializing huge directories."""
    pending = list(roots)
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if info['visited_entries'] >= MAX_CATALOG_ENTRIES:
                        info['truncated'] = True
                        return
                    info['visited_entries'] += 1
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file():
                        yield Path(entry.path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            if len(info['errors']) < 8:
                info['errors'].append(f'Catalog directory unavailable ({type(exc).__name__})')


def _scan_info() -> dict:
    return {'visited_entries': 0, 'truncated': False, 'errors': []}


def local_model_roots() -> list[Path]:
    """Resolve the configured GGUF search roots.

    Reads explicit configured roots first; historical omitted/default-empty
    lists fall back to `KORINA_LOCAL_MODEL_ROOTS`. An intentional empty list
    stays empty.
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

def discover_local_gguf_models(*, pinned: Iterable[object] = ()) -> list[str]:
    found: set[str] = set()
    info = _scan_info()
    # Explicit existing paths remain visible even outside the display walk's
    # budget. Activation validates its selected file independently.
    for value in pinned:
        mid = normalize_model_id(value)
        if mid is None or not mid.endswith('.gguf'):
            continue
        try:
            if Path(mid).is_absolute() and Path(mid).is_file():
                found.add(mid)
        except (OSError, ValueError):
            pass
    with closing(_catalog_files(local_model_roots(), info)) as files:
        for path in files:
            if path.suffix.lower() != '.gguf':
                continue
            lname = path.name.lower()
            if 'mmproj' in lname or lname.endswith('-assistant.gguf'):
                continue
            found.add(str(path))
            if len(found) > MAX_MODEL_IDS:
                info['truncated'] = True
                break
    source = ModelIds(info=info)
    ordered = sorted(found, key=lambda s: display_model_label(s).lower())
    return limit_model_ids(ordered, pinned=pinned, sources=[source])


def _lms_listing() -> bytes:
    """Bound CLI output and wall time without another reader thread."""
    process = subprocess.Popen([str(LMS_CLI_BIN), 'ls', '--json'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdout is not None
    try:
        data = bytearray()
        deadline = time.monotonic() + 30
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('LM Studio catalog timed out')
                chunk = os.read(process.stdout.fileno(), min(65536, MAX_CATALOG_SOURCE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_CATALOG_SOURCE_BYTES:
                    raise ValueError('LM Studio catalog exceeds 2 MiB')
        if process.wait(timeout=max(.01, deadline - time.monotonic())) != 0:
            raise ValueError('LM Studio catalog command failed')
        return bytes(data)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)

def discover_lmstudio_local_models(*, pinned: Iterable[object] = ()) -> list[str]:
    """Read a bounded LM Studio catalog via ``lms ls --json``.

    This includes user-imported community quants (e.g.
    ``qwen3.5-2b-uncensored-hauhaucs-aggressive``) that are NOT in the
    ``manifest.json`` files under ``LMSTUDIO_HUB_ROOT``. The hub catalog
    only lists officially-published models; locally-imported GGUFs are
    tracked by LM Studio's own index and are returned here.

    Side-effect: ``lms ls`` wakes up the LM Studio service if it was
    sleeping, which is convenient during /api/llm/provider/activate.
    Missing/unparseable CLI output leaves other discovery sources available.
    The list-compatible result carries limit/CLI-failure diagnostics. Explicit
    selected IDs found in the bounded source are pinned through display limits.
    """
    if not LMS_CLI_BIN.exists():
        return []
    try:
        output = _lms_listing().decode('utf-8', errors='strict')
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as exc:
        return ModelIds(info={'errors': [f'LM Studio catalog unavailable ({type(exc).__name__})']})
    try:
        items = json.loads(output)
    except Exception:
        # ``lms ls --json`` may emit non-JSON preamble ("Waking up LM Studio
        # service...") on first call. Strip everything before the first '['.
        idx = output.find('[')
        if idx < 0:
            return []
        try:
            items = json.loads(output[idx:])
        except Exception:
            return []
    if not isinstance(items, list):
        return ModelIds(info={'errors': ['Invalid LM Studio catalog response']})
    found: list[object] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if entry.get('type') and entry.get('type') != 'llm':
            continue
        key = entry.get('modelKey')
        if isinstance(key, str) and key:
            found.append(key)
        sel = entry.get('selectedVariant')
        if isinstance(sel, str) and sel:
            found.append(sel)
    return limit_model_ids(found, pinned=pinned)


def _manifest_model(manifest: Path) -> str | None:
    with manifest.open('rb') as source:
        data = source.read(MAX_CATALOG_SOURCE_BYTES + 1)
    if len(data) > MAX_CATALOG_SOURCE_BYTES:
        raise ValueError('manifest exceeds 2 MiB')
    body = json.loads(data)
    if not isinstance(body, dict):
        raise ValueError('invalid manifest')
    owner, name = body.get('owner'), body.get('name')
    if isinstance(owner, str) and isinstance(name, str) and owner and name:
        return f'{owner}/{name}'
    return None


def discover_lmstudio_hub_manifests(*, pinned: Iterable[object] = ()) -> list[str]:
    """Enumerate LM Studio models from ``manifest.json`` files under
    ``LMSTUDIO_HUB_ROOT`` ONLY. This is the deterministic, filesystem-only
    source; suitable for unit tests that need a stable catalog. The hub
    catalog only covers officially-published models from LM Studio's own
    registry; user-imported community quants are NOT here. Use
    ``discover_lmstudio_catalog_models`` for the union.
    """
    found: set[str] = set()
    info = _scan_info()
    # A selected canonical owner/name does not need a catalog-wide walk to
    # prove its manifest exists. Do not turn arbitrary IDs into file reads.
    for value in pinned:
        mid = normalize_model_id(value)
        if mid is None:
            continue
        parts = mid.split('/')
        if len(parts) != 2 or any(not part or part in {'.', '..'} for part in parts):
            continue
        try:
            manifest = LMSTUDIO_HUB_ROOT.joinpath(*parts, 'manifest.json')
            if manifest.resolve().is_relative_to(LMSTUDIO_HUB_ROOT.resolve()) and _manifest_model(manifest) == mid:
                found.add(mid)
        except (OSError, ValueError, RecursionError):
            continue
    with closing(_catalog_files([LMSTUDIO_HUB_ROOT], info)) as files:
        for manifest in files:
            if manifest.name != 'manifest.json':
                continue
            try:
                mid = _manifest_model(manifest)
            except (OSError, ValueError, RecursionError):
                if len(info['errors']) < 8:
                    info['errors'].append('Invalid or unreadable LM Studio manifest')
                continue
            if mid is not None:
                found.add(mid)
                if len(found) > MAX_MODEL_IDS:
                    info['truncated'] = True
                    break
    return limit_model_ids(sorted(found), pinned=pinned, sources=[ModelIds(info=info)])


def discover_lmstudio_catalog_models(*, pinned: Iterable[object] = ()) -> list[str]:
    """Return a bounded union of LM Studio hub manifests and the local
    ``lms ls`` index. The hub catalog only covers officially-published
    models; ``lms ls`` covers user-imported community quants too. The
    activation pre-flight pins its explicitly selected model independently
    of which IDs fit in the display catalog.
    """
    hub = discover_lmstudio_hub_manifests(pinned=pinned)
    local = discover_lmstudio_local_models(pinned=pinned)
    return limit_model_ids([*hub, *local], pinned=pinned, sources=[hub, local])

def find_mmproj_for_model(model_path: str) -> str:
    path = Path(str(model_path or '').strip())
    if not path.exists():
        return ''
    matches = sorted(path.parent.glob('*mmproj*.gguf'))
    return str(matches[0]) if matches else ''




# --- Phase 1.5 helper moved from korina.config ---

def llm_models_for(base_url: str, api_env: str, *, pinned: Iterable[object] = (),
                   is_current: Callable[[], bool] | None = None) -> list:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return []
    if base.endswith("/chat/completions"):
        base = base.rsplit("/chat/completions", 1)[0]
    url = f"{base}/models"
    from korina.runtime.http import auth_headers_from_env
    headers = auth_headers_from_env(api_env)
    from korina.runtime.json_http import request_json
    from korina.runtime.request_owner import check_current
    body = request_json(url, headers=headers, timeout=15, is_current=is_current)
    if not isinstance(body, dict) or not isinstance(body.get('data', []), list):
        raise ValueError('Invalid provider model list')
    models = limit_model_ids((item.get('id') if isinstance(item, dict) else item for item in body.get('data', [])), pinned=pinned)
    check_current(is_current)
    if base == "http://127.0.0.1:8080/v1":
        local = discover_local_gguf_models(pinned=pinned)
        models = limit_model_ids([*models, *local], pinned=pinned, sources=[models, local])
    check_current(is_current)
    return models
