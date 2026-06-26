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
    LMS_CLI_BIN,
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

def _kill_stray_llama_servers() -> None:
    """Kill any `llama-server` process currently bound to LM Studio's port (:1234).

    Korina's systemd `llama-server.service` is handled separately by
    `stop_llama_server()`. The remaining case is the `llama-server` that LM
    Studio launches on demand from its GUI (`lms load` with a GUI bound to
    :1234) and that keeps running after the GUI is closed. Without this kill,
    picking `lmstudio` in Korina returns success but `:1234` is still owned
    by the stray binary, so Korina's chat endpoint gets the wrong model id
    back from `/v1/models`.
    """
    subprocess.run(
        ['pkill', '-f', 'llama-server.*--port 1234'],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _lms_run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run an `lms` CLI subcommand and return the CompletedProcess."""
    cmd = [str(LMS_CLI_BIN), *args]
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def start_lmstudio_server(model_id: str, config: Optional[dict] = None) -> None:
    """Start LM Studio's headless OpenAI-compatible server on :1234 with `model_id` loaded.

    Order of operations:
      1. Stop Korina's systemd `llama-server.service`.
      2. Kill any stray `llama-server` that LM Studio's GUI may have started.
      3. Make sure the LM Studio GUI/RPC is running (its in-process RPC is
         what `lms` CLI talks to).
      4. `lms server start --port 1234 --bind 0.0.0.0 --cors`.
      5. `lms load <id> --yes` so /v1/models advertises the user's choice.
      6. Poll /v1/models until 200.
    """
    config = dict(config or load_config())
    model = str(model_id or config.get('lm_model') or '').strip()
    if not LMS_CLI_BIN.exists():
        raise RuntimeError(
            f'lms CLI not found at {LMS_CLI_BIN}. Set KORINA_LMS_CLI_BIN or '
            f'install LM Studio with the CLI bundle.'
        )
    stop_llama_server()
    _kill_stray_llama_servers()
    start_lmstudio()
    _lms_run('server', 'stop', check=False)
    start_proc = _lms_run('server', 'start', '--port', '1234', '--bind', '0.0.0.0', '--cors')
    if start_proc.returncode != 0:
        raise RuntimeError(
            'lms server start failed: ' + (start_proc.stderr or start_proc.stdout or 'unknown error').strip()
        )
    if model:
        load_proc = _lms_run('load', model, '--yes', check=False)
        if load_proc.returncode != 0:
            stderr = (load_proc.stderr or load_proc.stdout or '').strip()
            raise RuntimeError(f'lms load {model!r} failed: {stderr}')
    wait_for_lmstudio_server_ready()


def stop_lmstudio_server() -> None:
    """Tear down LM Studio's headless server and any stray llama-server on :1234."""
    if LMS_CLI_BIN.exists():
        try:
            _lms_run('server', 'stop', check=False)
        except Exception:
            pass
    _kill_stray_llama_servers()


def wait_for_lmstudio_server_ready(timeout_seconds: float = 90.0) -> None:
    """Poll http://127.0.0.1:1234/v1/models until HTTP 200."""
    deadline = time.time() + timeout_seconds
    last_error = ''
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:1234/v1/models', timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as e:
            last_error = str(e)
        time.sleep(0.5)
    raise RuntimeError(
        f'LM Studio server did not become ready within {timeout_seconds:.0f}s: {last_error}'
    )


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
        stop_lmstudio_server()
        stop_ollama()
        start_llama_server(selected, config=config)
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': selected, 'base_url': preset or 'http://127.0.0.1:8080/v1', 'stopped': ['lmstudio', 'ollama', 'stray-llama-server'], 'started': ['llama-server.service']}
    if provider == 'lmstudio':
        stop_ollama()
        start_lmstudio_server(model or '', config=config)
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:1234/v1', 'stopped': ['llama-server.service', 'ollama', 'stray-llama-server'], 'started': ['lm-studio-server']}
    if provider == 'ollama':
        stop_llama_server()
        stop_lmstudio()
        start_ollama()
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:11434/v1', 'stopped': ['llama-server.service', 'lmstudio'], 'started': ['ollama']}
    stop_llama_server()
    stop_lmstudio_server()
    stop_ollama()
    saved_base = str(config.get('llm_base_url') or '').strip()
    return {'provider': provider or 'openai-compatible', 'model': str(model or config.get('lm_model') or ''), 'base_url': saved_base, 'stopped': ['llama-server.service', 'lmstudio', 'ollama', 'stray-llama-server'], 'started': []}



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
