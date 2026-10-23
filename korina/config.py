"""Config loading, saving, and derived getters.

The DEFAULT_CONFIG is the single source of truth. Legacy key normalization
runs on every load_config() call so old config.json files still work.
"""
from __future__ import annotations

import json
import stat
import tempfile
import threading
from functools import wraps
from pathlib import Path
from typing import Callable, Optional, ParamSpec, TypeVar

from korina.util.paths import (
    APP_DIR, CONFIG_PATH, ACK_DEFAULT_VOICE, LMSTUDIO_MODEL,
)

from korina.util.presets import normalize_meta_reasoning_effort, provider_default_api_key_env, provider_preset_base_url
from korina.runtime.http import api_key_from_config
from korina.model_roots import MAX_MODEL_ROOTS, MAX_MODEL_ROOT_PATH_CHARS

_CONFIG_LOCK = threading.RLock()
_P = ParamSpec('_P')
_R = TypeVar('_R')


def _config_transaction(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialize atomic file writes and related read/modify/write operations."""
    @wraps(function)
    def call(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _CONFIG_LOCK:
            return function(*args, **kwargs)
    return call


DEFAULT_CONFIG = {
    'voice': ACK_DEFAULT_VOICE,
    'speed': 1.0,
    'mode': 'sse',
    'ack_enabled': 'on',
    'stt_backend': 'whisper',
    'stt_device': 'cpu',
    'stt_model': 'base.en',
    'stt_llm_provider': '',
    'stt_llm_base_url': '',
    'stt_llm_api_key_env': '',
    'stt_llm_model': '',
    'stt_llm_reasoning': 'off',
    'multimodal_stt_model_allowlist': [],
    'lm_model': LMSTUDIO_MODEL,
    'tts_device': 'cpu',
    'tts_provider': 'kokoro',
    'tts_health_url': '',
    'tts_port': 8880,
    'tts_base_url': '',
    'tts_model': 'kokoro',
    'llm_provider': 'llama.cpp',
    'llm_base_url': 'http://127.0.0.1:8080/v1',
    'llm_api_key_env': '',
    'llm_reasoning': 'off',
    'llm_reasoning_effort': '',
    'llm_max_tokens': 180,
    'stt_cloud_provider': '',
    'stt_cloud_base_url': '',
    'stt_cloud_model': '',
    'stt_api_key_env': '',
    'agent_enabled': 'on',
    'agent_provider': 'openai-compatible',
    'agent_base_url': 'http://127.0.0.1:8080/v1',
    'agent_api_key': '',
    'agent_model': '',
    'agent_max_turns': 16,
    'agent_max_tokens': 512,
    'agent_reasoning_effort': '',
    'agent_yolo_mode': 'off',
    'agent_project_trust': 'ask',
    'agent_injection_mode': 'one-at-a-time',
    'agent_follow_up_mode': 'one-at-a-time',
    'agent_thinking_level': 'low',
    'agent_auto_compact': 'on',
    'agent_compaction_reserve_tokens': 16384,
    'agent_compaction_keep_recent_tokens': 20000,
    'agent_hide_thinking': 'on',
    'agent_transport': 'auto',
    'agent_retry_enabled': 'on',
    'agent_max_retries': 3,
    'agent_retry_base_delay_ms': 2000,
    'agent_http_idle_timeout_ms': 0,
    'agent_enable_skill_commands': 'on',
    'agent_block_images': 'off',
    'agent_first_delivery_mode': 'first_turn_or_timer',
    'agent_first_delivery_seconds': 20,
    'agent_periodic_delivery_turns': 2,
    'agent_periodic_delivery_seconds': 45,
    'agent_busy_delivery_mode': 'injection',
    'agent_idle_delivery_mode': 'prompt',
    'agent_interrupts_enabled': 'on',
    'agent_interrupt_min_priority': 'important',
    'agent_hard_interrupt_min_priority': 'critical',
    'agent_interrupt_cooldown_padding_ms': 3000,
    'agent_permission_interrupts': 'on',
    'agent_report_injection_mode': 'next_reply',
    'endpoint_mode': 'reading',
    'silence_ms': 3200,
    'final_stt_mode': 'chunks',
    'min_speech_ms': 1200,
    'partial_window_ms': 1800,
    'idle_ack_initial_ms': 5000,
    'idle_ack_step_ms': 5000,
    'audio_unsupported': {},
    'local_model_roots': [],
    'local_model_roots_explicit': False,
    'converse': {
        'channel': 'korina',
        'allowed_origins': ['http://127.0.0.1:8001', 'http://localhost:8001'],
    },
}

CONFIG_KEYS = set(DEFAULT_CONFIG.keys())
LEGACY_AGENT_MODE_KEY = 'agent_' + 'st' + 'eering_mode'
LEGACY_DELIVERY_VALUE = 'st' + 'eer'
LEGACY_INJECTION_MODE_VALUE = 'st' + 'eering'


def _normalize_config_value(key: str, value):
    if key.endswith('_delivery_mode') and value == LEGACY_DELIVERY_VALUE:
        return 'injection'
    if key == 'agent_injection_mode' and value == LEGACY_INJECTION_MODE_VALUE:
        return 'one-at-a-time'
    return value


def _config_example_path() -> Path:
    return APP_DIR / 'config' / 'config.example.json'


@_config_transaction
def load_config() -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        example = _config_example_path()
        if example.exists():
            try:
                seed = json.loads(example.read_text())
            except (OSError, ValueError, RecursionError):
                seed = None
            if isinstance(seed, dict):
                merged_seed = dict(DEFAULT_CONFIG)
                for key, value in seed.items():
                    if key in CONFIG_KEYS:
                        merged_seed[key] = _normalize_config_value(key, value)
                # A persistence failure is not an invalid example. Preserve
                # its error instead of retrying with different defaults.
                save_config(merged_seed)
                return merged_seed
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_PATH.read_text())
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    merged = dict(DEFAULT_CONFIG)
    for key, value in data.items():
        if key in CONFIG_KEYS:
            merged[key] = _normalize_config_value(key, value)
    if LEGACY_AGENT_MODE_KEY in data and 'agent_injection_mode' not in data:
        merged['agent_injection_mode'] = _normalize_config_value('agent_injection_mode', data[LEGACY_AGENT_MODE_KEY])
    return merged


@_config_transaction
def save_config(config: dict) -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONFIG)
    for key, value in (config or {}).items():
        if key in CONFIG_KEYS:
            merged[key] = _normalize_config_value(key, value)
    _write_config_text(json.dumps(merged, indent=2, sort_keys=True) + '\n')
    return merged


def _write_config_text(content: str) -> None:
    """Replace this config path atomically, retaining its permission bits.

    New files and unfinished writes stay private. Unlike the systemd unit
    writer, replacing a config symlink retains the legacy alias-replacement
    behavior rather than modifying its target.
    """
    mode = 0o600
    try:
        mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
    except FileNotFoundError:
        pass
    temporary = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=CONFIG_PATH.parent,
        prefix='.korina-config-', suffix='.tmp', delete=False,
    )
    path = Path(temporary.name)
    try:
        temporary.write(content)
        temporary.close()
        path.chmod(mode)
        path.replace(CONFIG_PATH)
    finally:
        # Cleanup failures must not hide a write, close, chmod, or replace error.
        try:
            temporary.close()
        except OSError:
            pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


class ProviderSettingsBusy(RuntimeError):
    """A role change raced an active provider lifecycle operation."""


def _provider_role_settings(config: dict) -> dict:
    """Compare effective role settings, including equivalent blank inheritance."""
    return {
        'llm_provider': str(config.get('llm_provider') or '').strip().lower(),
        'llm_base_url': config_llm_chat_url(config),
        'lm_model': str(config.get('lm_model') or LMSTUDIO_MODEL).strip(),
        'llm_reasoning': config_llm_reasoning(config),
        'llm_api_key_env': config_llm_api_env(config),
        'stt_backend': str(config.get('stt_backend') or 'whisper').strip(),
        'stt_llm_provider': config_stt_llm_provider(config),
        'stt_llm_base_url': config_stt_llm_chat_url(config),
        'stt_llm_model': config_stt_llm_model(config),
        'stt_llm_api_key_env': config_stt_llm_api_env(config),
        'agent_enabled': str(config.get('agent_enabled') or 'on').strip(),
        'agent_provider': agent_provider(config),
        'agent_base_url': agent_chat_url(config),
        'agent_model': str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL).strip(),
        'agent_api_key': str(config.get('agent_api_key') or '').strip(),
    }


@_config_transaction
def update_config_values(payload: dict, *, reject_role_changes: bool = False) -> dict:
    """Merge user settings while preserving server-owned probe cache state."""
    previous = load_config()
    updates = {key: value for key, value in payload.items()
               if key in CONFIG_KEYS and key not in {'audio_unsupported', 'local_model_roots_explicit'}}
    if 'local_model_roots' in updates:
        updates['local_model_roots_explicit'] = True
    if isinstance(updates.get('converse'), dict):
        previous_converse = previous.get('converse')
        updates['converse'] = {
            **(previous_converse if isinstance(previous_converse, dict) else {}),
            **updates['converse'],
        }
    current = {**previous, **updates}
    current = synchronize_llm_dependents(current, previous)
    if reject_role_changes and _provider_role_settings(current) != _provider_role_settings(previous):
        raise ProviderSettingsBusy('Provider activation or refresh is busy; retry role settings after it finishes')
    saved = save_config(current)
    if previous.get('agent_enabled') != 'off' and saved.get('agent_enabled') == 'off':
        # Only Korina's transient built-in work/context is retired here. A
        # custom adapter's reset may affect durable memory, so never call it.
        from korina.services.agent_service import reset_agent_state
        reset_agent_state()
    return saved


@_config_transaction
def select_response_provider(provider: str, model: str | None = None) -> dict:
    """Commit only activation-owned fields against the latest settings/cache."""
    previous = load_config()
    current = {**previous, 'llm_provider': provider}
    preset = provider_preset_base_url(provider)
    if preset and provider != 'openai-compatible':
        current['llm_base_url'] = preset
    if model:
        current['lm_model'] = model.strip()
    current = synchronize_llm_dependents(current, previous)
    prefix = f'{provider}::'
    current['audio_unsupported'] = {
        key: value for key, value in config_audio_unsupported(previous).items()
        if not key.startswith(prefix)
    }
    return save_config(current)


class ConfigConflictError(RuntimeError):
    """A provider operation's configuration changed while it was preparing."""


@_config_transaction
def prepare_llama_refresh(expected_provider: str, expected_model: str, model: str | None = None) -> tuple[dict, int]:
    """Merge a resolved model/cache clear into current settings before restart."""
    previous = load_config()
    provider = str(previous.get('llm_provider') or '').strip().lower()
    if provider != expected_provider or str(previous.get('lm_model') or '').strip() != expected_model:
        raise ConfigConflictError('LLM settings changed during model discovery; retry refresh')
    current = dict(previous)
    if provider == 'llama.cpp' and model:
        current['lm_model'] = model
        current = synchronize_llm_dependents(current, previous)
    cache = config_audio_unsupported(previous)
    current['audio_unsupported'] = {key: value for key, value in cache.items() if not key.startswith('llama.cpp::')}
    cleared = len(cache) - len(current['audio_unsupported'])
    return save_config(current), cleared


@_config_transaction
def save_converse_channel(name: str) -> dict:
    """Persist a channel without overwriting simultaneous settings/probe writes."""
    current = load_config()
    converse = current.get('converse')
    current['converse'] = {**(converse if isinstance(converse, dict) else {}), 'channel': name}
    return save_config(current)




def _normalize_local_model_roots(value) -> list[str]:
    """Coerce a config-supplied local_model_roots value into a clean list of strings."""
    if value is None or value == '':
        return []
    if isinstance(value, str):
        parts = [p for p in value.replace('\n', ':').split(':') if p.strip()]
    elif isinstance(value, (list, tuple)):
        parts = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                parts.append(entry.strip())
            elif isinstance(entry, str):
                pass
            else:
                parts.append(str(entry).strip())
    else:
        parts = [str(value).strip()]
    seen = set()
    out = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def config_local_model_roots(config: Optional[dict] = None) -> list[str]:
    """Return the configured list of GGUF search roots.

    Resolution order:
      1. config['local_model_roots'] (always a list, possibly empty)
      2. KORINA_LOCAL_MODEL_ROOTS env var (split by os.pathsep)
    """
    config = config if config is not None else load_config()
    roots = config.get('local_model_roots')
    roots = _normalize_local_model_roots(roots)
    if roots or config.get('local_model_roots_explicit') is True:
        return roots
    from korina.util.paths import LOCAL_MODEL_ROOTS  # late import to avoid cycles
    return [str(p) for p in LOCAL_MODEL_ROOTS]


@_config_transaction
def set_local_model_roots(roots) -> list[str]:
    """Persist a new local_model_roots list and return the normalized list."""
    normalized = _normalize_local_model_roots(roots)
    _check_model_root_limits(normalized)
    cfg = load_config()
    cfg['local_model_roots'] = normalized
    cfg['local_model_roots_explicit'] = True
    save_config(cfg)
    return normalized


def _check_model_root_limits(roots: list[str]) -> None:
    if len(roots) > MAX_MODEL_ROOTS:
        raise ValueError(f'at most {MAX_MODEL_ROOTS} model roots are allowed')
    if any(not root or len(root) > MAX_MODEL_ROOT_PATH_CHARS or '\x00' in root for root in roots):
        raise ValueError(f'model root paths must contain 1–{MAX_MODEL_ROOT_PATH_CHARS} characters without NUL')


@_config_transaction
def append_local_model_roots(roots: list[str]) -> list[str]:
    current = config_local_model_roots(load_config())
    return set_local_model_roots([*current, *roots])


@_config_transaction
def remove_local_model_root(target: str) -> tuple[int, list[str]]:
    """Remove against the latest list, preserving concurrent settings/roots."""
    cfg = load_config()
    current = config_local_model_roots(cfg)
    if target not in current:
        return 0, current
    remaining = [root for root in current if root != target]
    cfg['local_model_roots'] = remaining
    cfg['local_model_roots_explicit'] = True
    save_config(cfg)
    return 1, remaining


def synchronize_llm_dependents(config: dict, previous: Optional[dict] = None) -> dict:
    config = dict(config or {})
    previous = previous or {}
    provider = str(config.get('llm_provider') or '').strip().lower()
    preset = provider_preset_base_url(provider)
    prev_provider = str(previous.get('llm_provider') or '').strip().lower()
    prev_preset = provider_preset_base_url(prev_provider)
    prev_model = str(previous.get('lm_model') or '').strip()
    current_model = str(config.get('lm_model') or '').strip()

    if provider and provider != 'openai-compatible' and preset:
        config['llm_base_url'] = preset

    if str(config.get('stt_backend') or '').strip() == 'llm':
        keys = ('stt_llm_provider', 'stt_llm_base_url', 'stt_llm_model')
        unchanged = all(config.get(key) == previous.get(key) for key in keys)
        followed_response = (
            str(previous.get('stt_llm_provider') or '').strip() in ('', prev_provider)
            and normalize_api_base_url(previous.get('stt_llm_base_url') or '') in (
                '', normalize_api_base_url(previous.get('llm_base_url') or ''), normalize_api_base_url(prev_preset))
            and str(previous.get('stt_llm_model') or '').strip() in ('', prev_model)
        )
        if unchanged and followed_response:
            config['stt_llm_provider'] = provider
            config['stt_llm_base_url'] = str(config.get('llm_base_url') or '').strip()
            config['stt_llm_model'] = current_model

    agent_provider_value = str(config.get('agent_provider') or 'openai-compatible').strip().lower()
    if agent_provider_value == 'meta':
        config['agent_base_url'] = provider_preset_base_url('meta')
    elif agent_provider_value != 'anthropic':
        previous_base = normalize_api_base_url(previous.get('agent_base_url') or '')
        follows_base = previous_base in ('', normalize_api_base_url(previous.get('llm_base_url') or ''), normalize_api_base_url(prev_preset))
        unchanged_base = config.get('agent_base_url') == previous.get('agent_base_url')
        if follows_base and unchanged_base:
            config['agent_base_url'] = str(config.get('llm_base_url') or '').strip()
        agent_model = str(config.get('agent_model') or '').strip()
        if follows_base and unchanged_base and agent_model in ('', prev_model) and config.get('agent_model') == previous.get('agent_model'):
            config['agent_model'] = current_model

    return config


def config_tts_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    explicit = str(config.get('tts_base_url') or '').strip().rstrip('/')
    if explicit:
        return explicit
    raw_port = config.get('tts_port')
    try:
        if raw_port is None or isinstance(raw_port, bool):
            raise ValueError
        port = int(raw_port)
        if not 1 <= port <= 65535 or (isinstance(raw_port, float) and raw_port != port):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        # Older files bypassed update validation. Resolve a usable endpoint
        # without rewriting the original value, so it remains repairable.
        port = 8880
    return f'http://127.0.0.1:{port}'


def config_converse_allowed_origins(config: Optional[dict] = None) -> list[str]:
    """Resolve CORS origins, retaining an explicit empty list as deny-all."""
    config = config if config is not None else load_config()
    converse = config.get('converse')
    origins = converse.get('allowed_origins') if isinstance(converse, dict) else None
    if isinstance(origins, list) and all(isinstance(origin, str) for origin in origins):
        return list(origins)
    defaults = DEFAULT_CONFIG['converse']
    assert isinstance(defaults, dict)
    return list(defaults['allowed_origins'])


def config_tts_provider(config: Optional[dict] = None) -> str:
    """Resolved TTS provider name from config (defaults to 'kokoro')."""
    config = config or load_config()
    return str(config.get('tts_provider') or 'kokoro').strip() or 'kokoro'


def config_llm_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    if str(config.get('llm_provider') or '').strip().lower() == 'meta':
        return provider_preset_base_url('meta')
    return str(config.get('llm_base_url') or 'http://127.0.0.1:8080/v1').strip().rstrip('/')


def config_llm_api_env(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('llm_api_key_env') or '').strip() or provider_default_api_key_env(
        str(config.get('llm_provider') or ''), config_llm_base_url(config),
    )


def config_llm_max_tokens(config: Optional[dict] = None) -> int:
    config = config if config is not None else load_config()
    value = config.get('llm_max_tokens', 180)
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 8192 else 180


def config_llm_chat_url(config: Optional[dict] = None) -> str:
    base = config_llm_base_url(config)
    return base if base.endswith('/chat/completions') else f'{base}/chat/completions'


def config_llm_models_url(config: Optional[dict] = None) -> str:
    return f'{normalize_api_base_url(config_llm_base_url(config))}/models'


def config_stt_llm_provider(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('stt_llm_provider') or config.get('llm_provider') or 'openai-compatible').strip()


def config_stt_llm_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    explicit = normalize_api_base_url(config.get('stt_llm_base_url') or '')
    if explicit:
        return explicit
    return config_llm_base_url(config)


def config_stt_llm_chat_url(config: Optional[dict] = None) -> str:
    base = config_stt_llm_base_url(config)
    return base if base.endswith('/chat/completions') else f'{base}/chat/completions'


def config_stt_llm_models_url(config: Optional[dict] = None) -> str:
    return f'{normalize_api_base_url(config_stt_llm_base_url(config))}/models'


def config_stt_llm_api_env(config: Optional[dict] = None) -> str:
    config = config or load_config()
    explicit = str(config.get('stt_llm_api_key_env') or '').strip()
    if explicit:
        return explicit
    if normalize_api_base_url(config_stt_llm_base_url(config)) == normalize_api_base_url(config_llm_base_url(config)):
        return config_llm_api_env(config)
    return ''


def config_stt_llm_model(config: Optional[dict] = None) -> str:
    config = config or load_config()
    explicit = str(config.get('stt_llm_model') or '').strip()
    if explicit:
        return explicit
    if str(config.get('stt_llm_provider') or '').strip() or str(config.get('stt_llm_base_url') or '').strip():
        return ''
    return str(config.get('lm_model') or LMSTUDIO_MODEL).strip()


def config_min_speech_ms(config: Optional[dict] = None) -> int:
    config = config or load_config()
    try:
        return max(300, int(config.get('min_speech_ms') or 1200))
    except Exception:
        return 1200


def config_partial_window_ms(config: Optional[dict] = None) -> int:
    config = config or load_config()
    try:
        return max(400, int(config.get('partial_window_ms') or 1800))
    except Exception:
        return 1800


def _reasoning_mode(value) -> str:
    mode = str(value or 'off').strip().lower()
    return mode if mode in {'on', 'off', 'auto'} else 'off'


def config_llm_reasoning(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return _reasoning_mode(config.get('llm_reasoning'))


def config_llm_reasoning_effort(config: Optional[dict] = None) -> str:
    config = config if config is not None else load_config()
    return normalize_meta_reasoning_effort(config.get('llm_reasoning_effort'))


def config_agent_reasoning_effort(config: Optional[dict] = None) -> str:
    config = config if config is not None else load_config()
    return normalize_meta_reasoning_effort(config.get('agent_reasoning_effort'))


def config_stt_llm_reasoning(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return _reasoning_mode(config.get('stt_llm_reasoning'))




def agent_provider(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('agent_provider') or 'openai-compatible').strip().lower()


def agent_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    if agent_provider(config) == 'meta':
        return provider_preset_base_url('meta')
    return str(config.get('agent_base_url') or config.get('llm_base_url') or 'http://127.0.0.1:8080/v1').strip().rstrip('/')


def _agent_api_endpoint(config: Optional[dict] = None) -> tuple[str, str]:
    """Resolve the API base using only the selected provider's chat suffix."""
    config = config or load_config()
    suffix = '/messages' if agent_provider(config) == 'anthropic' else '/chat/completions'
    return agent_base_url(config).removesuffix(suffix), suffix


def agent_models_url(config: Optional[dict] = None) -> str:
    base, _ = _agent_api_endpoint(config)
    return f'{base}/models'


def agent_chat_url(config: Optional[dict] = None) -> str:
    base, suffix = _agent_api_endpoint(config)
    return f'{base}{suffix}'


def agent_auth_headers(config: dict) -> dict:
    # A blank role-specific key must not send the response provider's secret
    # to an independently configured endpoint.
    agent_base, _ = _agent_api_endpoint(config)
    response_base = normalize_api_base_url(config_llm_base_url(config))
    provider = agent_provider(config)
    inherited_env = config_llm_api_env(config) if agent_base == response_base else ''
    env_name = inherited_env or provider_default_api_key_env(provider, agent_base)
    key = api_key_from_config(config, config.get('agent_api_key') or '', env_name)
    if not key:
        return {}
    if provider == 'anthropic':
        return {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    return {'Authorization': f'Bearer {key}'}



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




def normalize_api_base_url(base_url: str) -> str:
    """Canonical OpenAI-compatible API base URL (no trailing slash/chat path)."""
    base = str(base_url or '').strip().rstrip('/')
    if base.endswith('/chat/completions'):
        base = base.rsplit('/chat/completions', 1)[0]
    return base



def audio_unsupported_key(provider: str, base_url: str, model: str) -> str:
    """Canonical key for persistent audio-unsupported probe cache."""
    return (
        f"{str(provider or '').strip()}::"
        f"{normalize_api_base_url(base_url)}::"
        f"{str(model or '').strip()}"
    )



def config_audio_unsupported(c: dict | None = None) -> dict[str, dict]:
    """Return the persistent probe cache: {triple: {reason, since, last_error}}.

    Key format: "<provider>::<base_url>::<model>".
    Value: {"reason": str, "since": iso8601, "last_error": str}.
    """
    cfg = c if c is not None else load_config()
    raw = cfg.get("audio_unsupported") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = {
                "reason": str(v.get("reason") or ""),
                "since": str(v.get("since") or ""),
                "last_error": str(v.get("last_error") or ""),
            }
    return out


@_config_transaction
def set_audio_unsupported(provider: str, base_url: str, model: str,
                          reason: str, last_error: str, *,
                          is_current: Callable[[], bool] | None = None) -> None:
    """Persist a probe failure unless its owner retired while awaiting config."""
    from korina.runtime.request_owner import check_current

    triple = audio_unsupported_key(provider, base_url, model)
    cfg = load_config()
    check_current(is_current)
    cache = cfg.get("audio_unsupported") or {}
    if not isinstance(cache, dict):
        cache = {}
    from datetime import datetime, timezone
    check_current(is_current)
    cache[triple] = {
        "reason": reason,
        "since": datetime.now(timezone.utc).isoformat(),
        "last_error": last_error[:500],  # truncate to keep config.json small
    }
    cfg["audio_unsupported"] = dict(list(cache.items())[-512:])
    check_current(is_current)
    save_config(cfg)


@_config_transaction
def clear_audio_unsupported(provider: str, base_url: str, model: str, *,
                            is_current: Callable[[], bool] | None = None) -> bool:
    """Remove one entry if still owned. Returns True if removed."""
    from korina.runtime.request_owner import check_current

    triple = audio_unsupported_key(provider, base_url, model)
    cfg = load_config()
    check_current(is_current)
    cache = cfg.get("audio_unsupported") or {}
    if not isinstance(cache, dict) or triple not in cache:
        return False
    check_current(is_current)
    del cache[triple]
    cfg["audio_unsupported"] = cache
    check_current(is_current)
    save_config(cfg)
    return True
