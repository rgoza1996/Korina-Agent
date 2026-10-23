from __future__ import annotations

import json
import logging
import re
import os
import shutil
import subprocess
import threading
import time
import ipaddress
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from korina.config import (
    config_llm_reasoning,
    config_llm_base_url,
    config_stt_llm_base_url,
    config_stt_llm_model,
    agent_base_url,
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

from korina.util.presets import capabilities_for_section, provider_preset_base_url
from korina.runtime.json_http import HTTPStatusError, request_bytes, request_json
from korina.services import provider_processes

from pathlib import Path

from typing import Optional


logger = logging.getLogger(__name__)
# Activation and refresh both supervise the same optional local processes.
provider_activation_lock = threading.Lock()


class ProviderRoleConflict(RuntimeError):
    """A provider change would interrupt another active role's service."""


class InvalidLocalModelPath(ValueError):
    """A candidate local path cannot be inspected before provider admission."""


@dataclass
class LifecycleActions:
    started: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    stop_requested: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    loaded_models: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: LifecycleActions | None) -> None:
        if other is not None:
            for name in self.__dataclass_fields__:
                values = getattr(self, name)
                values.extend(value for value in getattr(other, name) if value not in values)


def _terminated(label: str, result: provider_processes.TerminationResult) -> LifecycleActions:
    actions = LifecycleActions()
    remaining = provider_processes.wait_for_exit(result.signalled)
    if result.signalled:
        if remaining or result.failed:
            actions.stop_requested.append(label)
            actions.warnings.append(f'{label}: termination requested; process exit was not confirmed')
        else:
            actions.stopped.append(label)
    if result.failed:
        actions.warnings.append(f'{label}: permission denied while stopping a provider process')
    return actions


def _unit_stopped(label: str, unit: str) -> LifecycleActions:
    result = _systemctl('stop', unit)
    if result.returncode == 0:
        return LifecycleActions(stopped=[label])
    return LifecycleActions(warnings=[f'{label}: stop command failed (exit {result.returncode}); stop was not confirmed'])


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


def stop_lmstudio() -> LifecycleActions:
    return _terminated('lmstudio', provider_processes.terminate_executable(LMSTUDIO_BIN))


def start_lmstudio() -> bool:
    if not LMSTUDIO_BIN.is_file():
        raise RuntimeError(f'LM Studio binary not found at {LMSTUDIO_BIN}; set KORINA_LMSTUDIO_BIN')
    if provider_processes.has_executable(LMSTUDIO_BIN):
        return False
    subprocess.Popen(
        [str(LMSTUDIO_BIN)], env=gui_env(), cwd=str(LMSTUDIO_BIN.resolve().parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    return True


def stop_ollama(*, stop_service: bool = True) -> LifecycleActions:
    """Stop supervised Ollama; wrappers may handle eligible user units first."""
    actions = _unit_stopped('ollama.service', 'ollama.service') if stop_service else LifecycleActions()
    binary = shutil.which('ollama')
    if binary:
        actions.merge(_terminated('ollama', provider_processes.terminate_executable(Path(binary), command='serve')))
    return actions


def _kill_stray_llama_servers(protected_ports: set[int] | None = None) -> LifecycleActions:
    """Stop only this user's llama-server actually listening on LM Studio's port."""
    protected_ports = protected_ports or set()
    result = provider_processes.terminate_matching(lambda pid, executable, args: (
        executable.name == 'llama-server'
        and provider_processes.listens_on(pid, 1234)
        and not any(provider_processes.listens_on(pid, port) for port in protected_ports)
    ))
    return _terminated('stray-llama-server', result)


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
        request_bytes(f'http://127.0.0.1:{port}/v1/models', timeout=1)
        return True
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


def start_lmstudio_server(model_id: str, config: Optional[dict] = None) -> LifecycleActions:
    """Bring up LM Studio's OpenAI-compatible server on :1234 with ``model_id`` loaded.

    LM Studio's GUI auto-starts its HTTP server as soon as the process is up.
    We do NOT call ``lms server start`` ourselves - that races against the
    GUI's auto-restart and produces transient ``EADDRINUSE`` errors. The
    minimal correct sequence is:

      1. Activation resolves competing listeners before entering this helper.
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
    actions = LifecycleActions()
    if not _is_lmstudio_server_already_up():
        if start_lmstudio():
            actions.started.append('lm-studio-server')
        else:
            actions.reused.append('lm-studio-server')
    else:
        actions.reused.append('lm-studio-server')
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
            if load_proc.returncode == 0:
                actions.loaded_models.append(model)
    return actions


def stop_lmstudio_server() -> LifecycleActions:
    """Stop LM Studio's server; activation separately owns competing listeners."""
    actions = LifecycleActions()
    if LMS_CLI_BIN.exists():
        try:
            result = _lms_run('server', 'stop', check=False)
            if result.returncode == 0:
                actions.stopped.append('lmstudio')
            else:
                actions.warnings.append(f'lmstudio: stop command failed (exit {result.returncode}); stop was not confirmed')
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning('Could not stop LM Studio server: %s', exc)
            actions.warnings.append(f'lmstudio: stop was not confirmed ({type(exc).__name__})')
    return actions


def _wait_for_models(url: str, label: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = 'no successful response'
    while time.monotonic() < deadline:
        try:
            remaining = max(0.01, deadline - time.monotonic())
            request_bytes(url, timeout=min(2, remaining))
            return
        except (OSError, ValueError, HTTPStatusError) as exc:
            last_error = str(exc)
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    raise RuntimeError(f'{label} did not become ready within {timeout_seconds:.0f}s: {last_error}')


def wait_for_lmstudio_server_ready(timeout_seconds: float = 90.0) -> None:
    _wait_for_models('http://127.0.0.1:1234/v1/models', 'LM Studio server', timeout_seconds)


def start_ollama() -> LifecycleActions:
    if shutil.which('ollama') is None:
        raise RuntimeError('Ollama is not installed or is not on PATH')
    run = _systemctl('start', 'ollama.service')
    process = None
    if run.returncode != 0:
        process = subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        _wait_for_models('http://127.0.0.1:11434/v1/models', 'Ollama server', 90)
        if process is not None and process.poll() is not None:
            raise RuntimeError(f'ollama serve exited during startup (exit {process.returncode})')
    except BaseException:
        if process is not None:
            _cleanup_failed_start(process)
        raise
    return LifecycleActions(started=['ollama'])


def _cleanup_failed_start(process: subprocess.Popen) -> None:
    """Reap only the child this attempt created, preserving the startup error."""
    try:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # It exited between poll and terminate; still reap it below.
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
    except Exception:
        logger.warning('Could not confirm cleanup of the Ollama child from failed startup', exc_info=True)


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


def start_llama_server(model_path: str, config: Optional[dict] = None) -> LifecycleActions:
    write_llama_server_unit(model_path, config=config)
    unit_path = LLAMA_SERVER_USER_UNIT.resolve()
    default_dir = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'systemd/user'
    if unit_path.parent != default_dir.resolve():
        _systemctl('link', str(unit_path), required=True)
    _systemctl('daemon-reload', required=True)
    _systemctl('enable', unit_path.name)
    _systemctl('restart', unit_path.name, required=True)
    wait_for_llama_server_ready()
    return LifecycleActions(started=[unit_path.name])

def stop_llama_server() -> LifecycleActions:
    return _unit_stopped(LLAMA_SERVER_USER_UNIT.name, LLAMA_SERVER_USER_UNIT.name)

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


def _local_model_file(value: str) -> Path | None:
    """Inspect only the candidate path, with a bounded public diagnostic.

    This does not catch executable, service, discovery, or startup failures.
    Resolve here too so path admission finishes before settings are saved.
    """
    try:
        if '\x00' in value:
            raise ValueError('path contains NUL')
        path = Path(value)
        return path.resolve() if path.is_file() else None
    except (OSError, ValueError) as exc:
        raise InvalidLocalModelPath('Invalid or unreadable local model path') from exc


@dataclass(frozen=True)
class _LocalRole:
    name: str
    port: int
    model: str


_MANAGED_PORTS = {'llama.cpp': 8080, 'lmstudio': 1234, 'ollama': 11434}


def _local_role(name: str, url: str, model: str) -> _LocalRole | None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').rstrip('.').lower()
        if parsed.scheme not in {'http', 'https'}:
            return None
        if host != 'localhost':
            address = ipaddress.ip_address(host)
            mapped = getattr(address, 'ipv4_mapped', None)
            if not address.is_loopback and not (mapped and mapped.is_loopback):
                return None
        return _LocalRole(name, parsed.port or (443 if parsed.scheme == 'https' else 80), model)
    except ValueError:
        return None


def _process_provider(executable: Path, args: list[str]) -> str | None:
    def matches(expected: Path) -> bool:
        return executable == expected.resolve() or bool(args and Path(args[0]).resolve() == expected.resolve())

    if matches(LLAMA_SERVER_BIN) or executable.name == 'llama-server':
        return 'llama.cpp'
    if matches(LMSTUDIO_BIN):
        return 'lmstudio'
    ollama = shutil.which('ollama')
    if (ollama and matches(Path(ollama))) or executable.name == 'ollama':
        return 'ollama'
    return None


def _retained_services(provider: str, config: dict) -> tuple[dict[str, list[_LocalRole]], list[_LocalRole]]:
    candidates = []
    if provider == 'openai-compatible':
        candidates.append(('response_llm', config_llm_base_url(config), str(config.get('lm_model') or '')))
    if config.get('stt_backend') == 'llm':
        candidates.append(('stt', config_stt_llm_base_url(config), config_stt_llm_model(config)))
    if config.get('agent_enabled') == 'on':
        candidates.append(('agent', agent_base_url(config), str(config.get('agent_model') or config.get('lm_model') or '')))
    roles = [role for name, url, model in candidates if (role := _local_role(name, url, model)) is not None]
    retained: dict[str, list[_LocalRole]] = {}

    def retain(service: str, role: _LocalRole) -> None:
        uses = retained.setdefault(service, [])
        if role not in uses:
            uses.append(role)

    # Default managed listeners remain protected even while temporarily down.
    for role in roles:
        for service, port in _MANAGED_PORTS.items():
            if role.port == port:
                retain(service, role)
    # Also recognize a configured managed executable serving a custom port.
    for pid, executable, args in provider_processes.user_processes():
        found_service = _process_provider(executable, args)
        if found_service:
            for role in roles:
                if provider_processes.listens_on(pid, role.port):
                    retain(found_service, role)
    return retained, roles


def _require_executable(path: Path, label: str) -> None:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f'{label} executable not found or not executable: {path}')


def _llama_selection(model: Optional[str], config: dict) -> str:
    """Resolve the effective selection and reject an ambiguous projector early."""
    selected = _resolve_llama_cpp_model_id(str(model or config.get('lm_model') or '').strip())
    if not selected:
        discovered = discover_local_gguf_models()
        selected = discovered[0] if discovered else ''
    selected_path = _local_model_file(selected) if selected else None
    if selected_path is not None:
        find_mmproj_for_model(str(selected_path))
    return selected


def _activation_model(provider: str, model: Optional[str], config: dict) -> str:
    if provider not in capabilities_for_section('response_llm'):
        raise ValueError(f'Unsupported response provider: {provider}')
    selected = str(model or config.get('lm_model') or '').strip()
    if provider == 'llama.cpp':
        selected = _llama_selection(model, config)
        selected_path = _local_model_file(selected) if selected else None
        if selected_path is None:
            raise RuntimeError(f'Requested llama.cpp model not found: {model or config.get("lm_model") or "no local GGUF models"}')
        selected = str(selected_path)
        _require_executable(LLAMA_SERVER_BIN, 'llama-server')
        if shutil.which('systemctl') is None:
            raise RuntimeError('Managed llama.cpp requires systemd; run an external endpoint on this host')
        if LLAMA_SERVER_USER_UNIT.suffix != '.service':
            raise ValueError('KORINA_LLAMA_SERVER_USER_UNIT must name a .service file')
    elif provider == 'lmstudio':
        _require_executable(LMS_CLI_BIN, 'lms CLI')
        if selected:
            supported, reason = provider_supports_model(provider, selected)
            if not supported:
                raise RuntimeError(f'Cannot activate LM Studio model: {reason}')
        competing = any(
            _process_provider(executable, args) not in (None, 'lmstudio')
            and provider_processes.listens_on(pid, 1234)
            for pid, executable, args in provider_processes.user_processes()
        )
        if competing or not _is_lmstudio_server_already_up():
            _require_executable(LMSTUDIO_BIN, 'LM Studio')
    elif provider == 'ollama' and shutil.which('ollama') is None:
        raise RuntimeError('Ollama is not installed or is not on PATH')
    return selected


def _check_role_conflicts(provider: str, selected: str, retained: dict[str, list[_LocalRole]], roles: list[_LocalRole]) -> None:
    if provider == 'llama.cpp':
        for role in retained.get(provider, []):
            other_model = _resolve_llama_cpp_model_id(role.model) if role.model else ''
            if other_model and Path(other_model).is_file():
                other_model = str(Path(other_model).resolve())
            if other_model and other_model != selected:
                raise ProviderRoleConflict(
                    f'Cannot replace the single llama.cpp model while {role.name} uses another model; '
                    'choose the same model or a separate endpoint'
                )
    target_port = _MANAGED_PORTS.get(provider)
    for pid, executable, args in provider_processes.user_processes():
        if target_port and provider_processes.listens_on(pid, target_port):
            other = _process_provider(executable, args)
            protected = [role for role in roles if provider_processes.listens_on(pid, role.port)]
            if protected and other and other != provider:
                names = ', '.join(sorted({role.name for role in protected}))
                raise ProviderRoleConflict(f'Port {target_port} is served by {other} and required by {names}; keep its endpoint or use another service')


def _llama_serves_model(selected: str) -> bool:
    try:
        body = request_json('http://127.0.0.1:8080/v1/models', timeout=1)
        items = body.get('data') if isinstance(body, dict) else None
        if not isinstance(items, list):
            return False
        for item in items:
            identifier = item.get('id') if isinstance(item, dict) else None
            if isinstance(identifier, str) and (identifier == selected or (
                not Path(identifier).is_absolute() and Path(identifier).name == Path(selected).name
            )):
                return True
    except (OSError, ValueError, HTTPStatusError):
        pass
    return False


def _protected_llama_running(roles: list[_LocalRole]) -> bool:
    return any(
        _process_provider(executable, args) == 'llama.cpp'
        and any(provider_processes.listens_on(pid, role.port) for role in roles)
        for pid, executable, args in provider_processes.user_processes()
    )


def prepare_provider(
    provider: str, config: dict, model: Optional[str] = None, *, restart: bool = False,
) -> tuple[str, dict[str, list[_LocalRole]], list[_LocalRole]]:
    """Validate prerequisites and role ownership before writing or stopping services."""
    selected = _activation_model(provider, model, config)
    retained, roles = _retained_services(provider, config)
    _check_role_conflicts(provider, selected, retained, roles)
    if restart and provider == 'llama.cpp' and _protected_llama_running(roles):
        raise ProviderRoleConflict(
            'Cannot restart a running llama.cpp service used by active STT or Agent; '
            'stop that role or choose a separate endpoint before refreshing'
        )
    return selected, retained, roles


def activate_llm_provider(provider: str, config: Optional[dict] = None, model: Optional[str] = None) -> dict:
    config = dict(config or load_config())
    provider = str(provider or config.get('llm_provider') or '').strip().lower()
    selected, retained, roles = prepare_provider(provider, config, model)
    reuse_llama = provider == 'llama.cpp' and provider in retained and _llama_serves_model(selected)
    if provider == 'llama.cpp' and provider in retained and not reuse_llama and _protected_llama_running(roles):
        raise ProviderRoleConflict(
            'A running llama.cpp process serves another active role; its selected model could not be '
            'confirmed reusable on port 8080. Keep that service or use a separate response endpoint'
        )
    protected_ports = {role.port for role in roles}
    actions = LifecycleActions()
    teardown = {
        'llama.cpp': stop_llama_server,
        'lmstudio': stop_lmstudio if provider == 'ollama' else stop_lmstudio_server,
        'ollama': stop_ollama,
    }
    for service, stop in teardown.items():
        if service != provider and service not in retained:
            actions.merge(stop())
    # Competing :1234 listeners are a separate action, never a hidden side
    # effect of starting/stopping another provider. Do not touch a PID that
    # also serves a protected role on any port.
    if provider != 'ollama' and 1234 not in protected_ports:
        actions.merge(_kill_stray_llama_servers(protected_ports))
    if provider == 'llama.cpp':
        if reuse_llama:
            actions.reused.append(LLAMA_SERVER_USER_UNIT.name)
        else:
            actions.merge(start_llama_server(selected, config=config))
    elif provider == 'lmstudio':
        actions.merge(start_lmstudio_server(selected, config=config))
    elif provider == 'ollama':
        actions.merge(start_ollama())
    preset = provider_preset_base_url(provider)
    return {
        'provider': provider, 'model': selected,
        'base_url': preset or str(config.get('llm_base_url') or '').strip(),
        **asdict(actions),
        'retained': [
            {'provider': service, 'roles': sorted({role.name for role in uses}),
             'reason': 'active loopback endpoint on port ' + ', '.join(str(port) for port in sorted({role.port for role in uses}))}
            for service, uses in retained.items()
        ],
    }



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
      - openai-compatible / meta: allow (remote endpoint checked by discovery)
      - anthropic: always allow (user-provided endpoint)
    """
    from korina.services.model_catalog import (
        discover_lmstudio_catalog_models,
    )

    pid = str(provider or "").strip()
    mid = str(model or "").strip()

    if pid in ("openai-compatible", "anthropic", "meta"):
        return (True, "user_provided_endpoint")

    if pid == "ollama":
        # Cannot pre-check; ollama's /v1/models is only reachable once
        # the server is up. Allow and let the chat request fail loud.
        return (True, "ollama_endpoint_checked_later")

    if pid == "llama.cpp":
        if not mid:
            return (False, "llama.cpp requires a model id (local GGUF path)")
        if not mid.lower().endswith(".gguf"):
            return (False, f"llama.cpp does not support endpoint-loaded ids; got '{mid}'")
        try:
            model_file = _local_model_file(mid)
        except InvalidLocalModelPath as exc:
            return (False, str(exc))
        if model_file is None:
            return (False, f"llama.cpp model file not found: {mid}")
        # NOTE: do NOT call `discover_local_gguf_models()` here. That walks
        # `LOCAL_MODEL_ROOTS` + `LMSTUDIO_HUB_ROOT` via `rglob('*.gguf')`
        # on every activate request -- a non-trivial filesystem scan on
        # hosts with many GGUFs. The user-provided file existing on disk
        # is sufficient evidence of legitimacy; the llama-server will
        # reject it with a clear error if it's actually broken.
        return (True, "local_gguf")

    if pid == "lmstudio":
        if not mid:
            return (False, "lmstudio requires a model id (catalog id)")
        try:
            # Discovery is a bounded display catalog. Preserve a legitimate
            # explicit selection even when it sorts beyond the displayed IDs.
            catalog = discover_lmstudio_catalog_models(pinned=[mid])
        except Exception:
            catalog = []
        if mid in catalog:
            return (True, "lmstudio_catalog")
        return (False, f"lmstudio catalog does not contain '{mid}'")

    return (False, f"unknown provider '{pid}'")
