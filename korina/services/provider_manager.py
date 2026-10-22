from __future__ import annotations

import json
import logging
import re
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
from korina.services import provider_processes

from pathlib import Path

from typing import Optional


logger = logging.getLogger(__name__)


def _systemctl(*args: str, required: bool = False) -> subprocess.CompletedProcess:
    """Optional teardown also works on hosts without systemd."""
    command = ['systemctl', '--user', *args]
    try:
        return subprocess.run(command, check=required, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30)
    except FileNotFoundError:
        if required:
            raise RuntimeError('Managed llama.cpp requires systemd; run an external endpoint on this host')
        return subprocess.CompletedProcess(command, 127)


def gui_env() -> dict:
    """Use the launching user's session without inventing a display server."""
    env = os.environ.copy()
    if hasattr(os, 'getuid'):
        runtime = Path(env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}')
        if runtime.is_dir():
            env['XDG_RUNTIME_DIR'] = str(runtime)
            if not env.get('DBUS_SESSION_BUS_ADDRESS') and (runtime / 'bus').exists():
                env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={runtime}/bus'
            if not env.get('XAUTHORITY'):
                candidates = sorted(runtime.glob('.mutter-Xwaylandauth.*'))
                if candidates:
                    env['XAUTHORITY'] = str(candidates[-1])
    authority = Path.home() / '.Xauthority'
    if not env.get('XAUTHORITY') and authority.is_file():
        env['XAUTHORITY'] = str(authority)
    return env


def stop_lmstudio() -> None:
    provider_processes.terminate_executable(LMSTUDIO_BIN)


def start_lmstudio() -> None:
    if not LMSTUDIO_BIN.is_file():
        raise RuntimeError(f'LM Studio binary not found at {LMSTUDIO_BIN}; set KORINA_LMSTUDIO_BIN')
    if provider_processes.has_executable(LMSTUDIO_BIN):
        return
    subprocess.Popen(
        [str(LMSTUDIO_BIN)], env=gui_env(), cwd=str(LMSTUDIO_BIN.resolve().parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def stop_ollama() -> None:
    _systemctl('stop', 'ollama.service')
    binary = shutil.which('ollama')
    if binary:
        provider_processes.terminate_executable(Path(binary), command='serve')


def _kill_stray_llama_servers() -> None:
    """Stop only this user's llama-server actually listening on LM Studio's port."""
    provider_processes.terminate_matching(lambda pid, executable, args: (
        executable.name == 'llama-server'
        and provider_processes.listens_on(pid, 1234)
    ))


def _lms_run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run an `lms` CLI subcommand and return the CompletedProcess."""
    cmd = [str(LMS_CLI_BIN), *args]
    result = subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=60)
    if not check and result.returncode:
        logger.warning('lms %s failed: %s', ' '.join(args), (result.stderr or result.stdout or '').strip())
    return result



def _is_lmstudio_server_already_up(port: int = 1234) -> bool:
    """True if LM Studio HTTP server is already serving :port."""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/v1/models', timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False



def _is_lmstudio_model_loaded(model: str) -> bool:
    """True if ``model`` is currently loaded in LM Studio's runtime.

    Uses ``lms ps --json`` which returns one entry per loaded model with a
    ``modelKey`` field. This avoids the failure mode where ``lms load``
    returns non-zero because the model is already in memory and a second
    instance would not fit.
    """
    if not LMS_CLI_BIN.exists():
        return False
    try:
        proc = subprocess.run(
            [str(LMS_CLI_BIN), 'ps', '--json'],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return False
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    try:
        items = json.loads(proc.stdout)
    except Exception:
        idx = proc.stdout.find('[')
        if idx < 0:
            return False
        try:
            items = json.loads(proc.stdout[idx:])
        except Exception:
            return False
    target = str(model or '').strip()
    if not target:
        return False
    for entry in items:
        if not isinstance(entry, dict):
            continue
        key = entry.get('modelKey') or entry.get('identifier') or ''
        if key == target:
            return True
    return False


def start_lmstudio_server(model_id: str, config: Optional[dict] = None) -> None:
    """Bring up LM Studio's OpenAI-compatible server on :1234 with ``model_id`` loaded.

    LM Studio's GUI auto-starts its HTTP server as soon as the process is up.
    We do NOT call ``lms server start`` ourselves - that races against the
    GUI's auto-restart and produces transient ``EADDRINUSE`` errors. The
    minimal correct sequence is:

      1. Free :1234 of any competing llama-server first.
      2. Spawn the GUI if it is not already running. Its embedded HTTP server
         starts automatically as part of GUI startup.
      3. Poll ``http://127.0.0.1:1234/v1/models`` until 200 - this is the
         source of truth that the server is ready.
      4. ``lms load <id> --yes`` so the OpenAI-compatible ``/v1/models``
         endpoint advertises the user's choice. Tolerate "already loaded".
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
    if not _is_lmstudio_server_already_up():
        start_lmstudio()
    wait_for_lmstudio_server_ready()
    if model:
        # Skip the load entirely if the model is already in memory. `lms
        # load <model>` fails when a second instance can't fit in VRAM,
        # even though the model is healthy and serving requests.
        if not _is_lmstudio_model_loaded(model):
            load_proc = _lms_run('load', model, '--yes', check=False)
            if load_proc.returncode != 0:
                stderr = (load_proc.stderr or load_proc.stdout or '').strip()
                if 'already loaded' not in stderr.lower() and 'no change' not in stderr.lower():
                    raise RuntimeError(f'lms load {model!r} failed: {stderr}')


def stop_lmstudio_server() -> None:
    """Tear down LM Studio's headless server and any stray llama-server on :1234."""
    if LMS_CLI_BIN.exists():
        try:
            _lms_run('server', 'stop', check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning('Could not stop LM Studio server: %s', exc)
    _kill_stray_llama_servers()


def _wait_for_models(url: str, label: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = 'no successful response'
    while time.monotonic() < deadline:
        try:
            remaining = max(0.01, deadline - time.monotonic())
            with urllib.request.urlopen(url, timeout=min(2, remaining)) as resp:
                if resp.status == 200:
                    return
                last_error = f'HTTP {resp.status}'
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    raise RuntimeError(f'{label} did not become ready within {timeout_seconds:.0f}s: {last_error}')


def wait_for_lmstudio_server_ready(timeout_seconds: float = 90.0) -> None:
    _wait_for_models('http://127.0.0.1:1234/v1/models', 'LM Studio server', timeout_seconds)


def start_ollama() -> None:
    if shutil.which('ollama') is None:
        raise RuntimeError('Ollama is not installed or is not on PATH')
    run = _systemctl('start', 'ollama.service')
    if run.returncode != 0:
        subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

def _systemd_argument(value: str) -> str:
    """Quote one systemd argument; protect specifier/environment expansion."""
    if any(ord(char) < 32 for char in value):
        raise ValueError('systemd arguments cannot contain control characters')
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$')
    return f'"{escaped}"' if re.search(r'[\s"\\%$]', value) else value


def write_llama_server_unit(model_path: str, config: Optional[dict] = None) -> None:
    config = dict(config or load_config())
    model = Path(str(model_path or '').strip())
    if not model.exists():
        raise RuntimeError(f'llama.cpp model not found: {model}')
    if not LLAMA_SERVER_BIN.exists():
        raise RuntimeError(f'llama-server binary not found: {LLAMA_SERVER_BIN}')
    model = model.resolve()
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
    ]
    exec_parts = [str(LLAMA_SERVER_BIN.resolve()), '-m', str(model)]
    if mmproj:
        exec_parts += ['--mmproj', mmproj]
    exec_parts += ['--reasoning', config_llm_reasoning(config), '--host', os.environ.get('KORINA_LLM_HOST', '127.0.0.1'), '--port', '8080', '-ngl', '99', '-t', '4']
    if LLAMA_SERVER_MEDIA_PATH:
        exec_parts += ['--media-path', LLAMA_SERVER_MEDIA_PATH]
    unit.append('ExecStart=' + ' '.join(_systemd_argument(part) for part in exec_parts))
    unit.append('WorkingDirectory=' + str(LLAMA_SERVER_BIN.resolve().parent).replace('%', '%%'))
    unit += ['', '[Install]', 'WantedBy=default.target', '']
    service_path = LLAMA_SERVER_USER_UNIT
    if service_path.suffix != '.service':
        raise ValueError('KORINA_LLAMA_SERVER_USER_UNIT must name a .service file')
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text('\n'.join(unit))

def wait_for_llama_server_ready(timeout_seconds: float = 90.0) -> None:
    _wait_for_models('http://127.0.0.1:8080/v1/models', 'llama-server', timeout_seconds)


def start_llama_server(model_path: str, config: Optional[dict] = None) -> None:
    write_llama_server_unit(model_path, config=config)
    unit_path = LLAMA_SERVER_USER_UNIT.resolve()
    default_dir = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'systemd/user'
    if unit_path.parent != default_dir.resolve():
        _systemctl('link', str(unit_path), required=True)
    _systemctl('daemon-reload', required=True)
    _systemctl('enable', unit_path.name)
    _systemctl('restart', unit_path.name, required=True)
    wait_for_llama_server_ready()

def stop_llama_server() -> None:
    _systemctl('stop', LLAMA_SERVER_USER_UNIT.name)

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
        if raw_selected and (not selected or not Path(selected).is_file()):
            raise RuntimeError(f'Requested llama.cpp model not found: {raw_selected}')
        if not selected:
            discovered = discover_local_gguf_models()
            if discovered:
                selected = discovered[0]
            else:
                raise RuntimeError('No local GGUF models found for llama.cpp')
        stop_lmstudio_server()
        stop_ollama()
        start_llama_server(selected, config=config)
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': selected, 'base_url': preset or 'http://127.0.0.1:8080/v1', 'stopped': ['lmstudio', 'ollama', 'stray-llama-server'], 'started': [LLAMA_SERVER_USER_UNIT.name]}
    if provider == 'lmstudio':
        stop_ollama()
        start_lmstudio_server(model or '', config=config)
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:1234/v1', 'stopped': [LLAMA_SERVER_USER_UNIT.name, 'ollama', 'stray-llama-server'], 'started': ['lm-studio-server']}
    if provider == 'ollama':
        stop_llama_server()
        stop_lmstudio()
        start_ollama()
        preset = provider_preset_base_url(provider)
        return {'provider': provider, 'model': str(model or config.get('lm_model') or ''), 'base_url': preset or 'http://127.0.0.1:11434/v1', 'stopped': [LLAMA_SERVER_USER_UNIT.name, 'lmstudio'], 'started': ['ollama']}
    stop_llama_server()
    stop_lmstudio_server()
    stop_ollama()
    saved_base = str(config.get('llm_base_url') or '').strip()
    return {'provider': provider or 'openai-compatible', 'model': str(model or config.get('lm_model') or ''), 'base_url': saved_base, 'stopped': [LLAMA_SERVER_USER_UNIT.name, 'lmstudio', 'ollama', 'stray-llama-server'], 'started': []}



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
