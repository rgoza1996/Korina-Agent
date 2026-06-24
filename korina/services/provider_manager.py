from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from korina.config import (
    config_llm_reasoning,
    load_config,
)

from korina.services.model_catalog import (
    discover_local_gguf_models,
    find_mmproj_for_model,
)

from korina.util.paths import (
    LLAMA_SERVER_BIN,
    LLAMA_SERVER_MEDIA_PATH,
    LLAMA_SERVER_USER_UNIT,
    LMSTUDIO_BIN,
)

from korina.util.presets import provider_preset_base_url

from pathlib import Path

from typing import Optional


def gui_env() -> dict:
    env = os.environ.copy()
    env.setdefault('DISPLAY', ':0')
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/1000')
    if not env.get('XAUTHORITY'):
        candidates = sorted(Path('/run/user/1000').glob('.mutter-Xwaylandauth.*'))
        if candidates:
            env['XAUTHORITY'] = str(candidates[-1])
        elif Path('/home/roggoz/.Xauthority').exists():
            env['XAUTHORITY'] = '/home/roggoz/.Xauthority'
    return env

def stop_lmstudio() -> None:
    subprocess.run(['pkill', '-f', '/opt/LM-Studio/lm-studio'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def start_lmstudio() -> None:
    if not LMSTUDIO_BIN.exists():
        raise RuntimeError('LM Studio binary not found at /opt/LM-Studio/lm-studio')
    already = subprocess.run(['pgrep', '-f', '/opt/LM-Studio/lm-studio'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if already.returncode == 0:
        return
    subprocess.Popen([str(LMSTUDIO_BIN)], env=gui_env(), cwd='/opt/LM-Studio', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def stop_ollama() -> None:
    subprocess.run(['systemctl', '--user', 'stop', 'ollama.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['pkill', '-f', 'ollama serve'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def start_ollama() -> None:
    if shutil.which('ollama') is None:
        raise RuntimeError('Ollama is not installed on roggoz')
    run = subprocess.run(['systemctl', '--user', 'start', 'ollama.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if run.returncode != 0:
        subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def write_llama_server_unit(model_path: str, config: Optional[dict] = None) -> None:
    config = dict(config or load_config())
    model = Path(str(model_path or '').strip())
    if not model.exists():
        raise RuntimeError(f'llama.cpp model not found: {model}')
    if not LLAMA_SERVER_BIN.exists():
        raise RuntimeError(f'llama-server binary not found: {LLAMA_SERVER_BIN}')
    mmproj = find_mmproj_for_model(str(model))
    unit = [
        '[Unit]',
        'Description=Llama.cpp Server (Korina-selected model)',
        'After=network.target',
        '',
        '[Service]',
        'Type=simple',
        'Restart=always',
        'RestartSec=5',
        'Environment=VK_ICD_FILE=/usr/share/vulkan/icd.d/radeon_icd.json',
    ]
    exec_parts = [str(LLAMA_SERVER_BIN), '-m', str(model)]
    if mmproj:
        exec_parts += ['--mmproj', mmproj]
    exec_parts += ['--reasoning', config_llm_reasoning(config), '--host', '0.0.0.0', '--port', '8080', '--media-path', LLAMA_SERVER_MEDIA_PATH, '-ngl', '99', '-t', '4']
    unit.append('ExecStart=' + ' '.join(exec_parts))
    unit.append(f'WorkingDirectory={LLAMA_SERVER_BIN.parent}')
    unit += ['', '[Install]', 'WantedBy=default.target', '']
    service_path = LLAMA_SERVER_USER_UNIT
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text('\n'.join(unit))

def wait_for_llama_server_ready(timeout_seconds: float = 90.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ''
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/v1/models', timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as e:
            last_error = str(e)
        time.sleep(0.5)
    raise RuntimeError(f'llama-server did not become ready within {timeout_seconds:.0f}s: {last_error}')

def start_llama_server(model_path: str, config: Optional[dict] = None) -> None:
    write_llama_server_unit(model_path, config=config)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'enable', 'llama-server.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'restart', 'llama-server.service'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for_llama_server_ready()

def stop_llama_server() -> None:
    subprocess.run(['systemctl', '--user', 'stop', 'llama-server.service'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _resolve_llama_cpp_model_id(value: str) -> str:
    """Resolve a configured llama.cpp model to an absolute GGUF path on disk.

    Accepts:
    - an absolute path to a .gguf file
    - a bare model id like 'qwen3.5-2b-uncensored-hauhaucs-aggressive' that
      matches the basename of a discovered GGUF

    Returns the resolved absolute path, or the original string if it already
    looks like an absolute path (so write_llama_server_unit can raise its
    own clearer error). Returns '' if the input is empty.
    """
    v = str(value or '').strip()
    if not v:
        return ''
    p = Path(v)
    if p.is_absolute():
        return str(p)
    candidates = [str(g) for g in discover_local_gguf_models()]
    # Exact filename match first
    for g in candidates:
        if Path(g).name == v or Path(g).stem == v:
            return g
    # Suffix match (e.g. 'gemma-4-e2b' should match 'gemma-4-E2B_q4_0-it.gguf')
    lv = v.lower()
    for g in candidates:
        if lv in Path(g).name.lower():
            return g
    return v

def activate_llm_provider(provider: str, config: Optional[dict] = None, model: Optional[str] = None) -> dict:
    config = dict(config or load_config())
    provider = str(provider or config.get('llm_provider') or '').strip().lower()
    if provider == 'llama.cpp':
        raw_selected = str(model or config.get('lm_model') or '').strip()
        selected = _resolve_llama_cpp_model_id(raw_selected)
        if not selected or not Path(selected).exists():
            discovered = discover_local_gguf_models()
            if discovered:
                selected = discovered[0]
            else:
                raise RuntimeError('No local GGUF models found for llama.cpp')
        stop_lmstudio()
        stop_ollama()
        start_llama_server(selected, config=config)
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': selected, 'base_url': preset or 'http://127.0.0.1:8080/v1', 'stopped': ['lmstudio', 'ollama'], 'started': ['llama-server.service']}
    if provider == 'lmstudio':
        stop_llama_server()
        stop_ollama()
        start_lmstudio()
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:1234/v1', 'stopped': ['llama-server.service', 'ollama'], 'started': ['lm-studio']}
    if provider == 'ollama':
        stop_llama_server()
        stop_lmstudio()
        start_ollama()
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:11434/v1', 'stopped': ['llama-server.service', 'lmstudio'], 'started': ['ollama']}
    stop_llama_server()
    stop_lmstudio()
    stop_ollama()
    saved_base = str(config.get('llm_base_url') or '').strip()
    return {'provider': provider or 'openai-compatible', 'model': str(model or config.get('lm_model') or ''), 'base_url': saved_base, 'stopped': ['llama-server.service', 'lmstudio', 'ollama'], 'started': []}



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
        # NOTE: do NOT call `discover_local_gguf_models()` here. That walks
        # `LOCAL_MODEL_ROOTS` + `LMSTUDIO_HUB_ROOT` via `rglob('*.gguf')`
        # on every activate request -- a non-trivial filesystem scan on
        # hosts with many GGUFs. The user-provided path existing on disk
        # is sufficient evidence of legitimacy; the llama-server will
        # reject it with a clear error if it's actually broken.
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
