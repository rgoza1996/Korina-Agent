"""Config loading, saving, and derived getters.

The DEFAULT_CONFIG is the single source of truth. Legacy key normalization
runs on every load_config() call so old config.json files still work.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

from korina.util.paths import (
    APP_DIR, CONFIG_PATH, ACK_DEFAULT_VOICE, LMSTUDIO_MODEL, KOKORO_URL,
)

from korina.util.presets import provider_preset_base_url, is_local_provider_base_url
from korina.runtime.http import api_key_from_config

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
    'lm_model': LMSTUDIO_MODEL,
    'tts_device': 'cpu',
    'tts_provider': 'kokoro',
    'tts_port': 8880,
    'tts_base_url': '',
    'tts_model': 'kokoro',
    'llm_provider': 'llama.cpp',
    'llm_base_url': 'http://127.0.0.1:8080/v1',
    'llm_api_key_env': '',
    'llm_reasoning': 'off',
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


def load_config() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        example = _config_example_path()
        if example.exists():
            try:
                seed = json.loads(example.read_text())
                if isinstance(seed, dict):
                    merged_seed = dict(DEFAULT_CONFIG)
                    for key, value in seed.items():
                        if key in CONFIG_KEYS:
                            merged_seed[key] = _normalize_config_value(key, value)
                    save_config(merged_seed)
                    return merged_seed
            except Exception:
                pass
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


def save_config(config: dict) -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONFIG)
    for key, value in (config or {}).items():
        if key in CONFIG_KEYS:
            merged[key] = _normalize_config_value(key, value)
    tmp = CONFIG_PATH.with_suffix('.tmp.json')
    tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + '\n')
    tmp.replace(CONFIG_PATH)
    return merged





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
        config['stt_llm_provider'] = provider or str(config.get('stt_llm_provider') or '').strip()
        if provider != 'openai-compatible' and preset:
            config['stt_llm_base_url'] = preset
        elif not str(config.get('stt_llm_base_url') or '').strip():
            config['stt_llm_base_url'] = str(config.get('llm_base_url') or '').strip()
        if current_model:
            config['stt_llm_model'] = current_model

    agent_provider_value = str(config.get('agent_provider') or 'openai-compatible').strip().lower()
    if agent_provider_value != 'anthropic':
        config['agent_provider'] = 'openai-compatible'
        if provider != 'openai-compatible' and preset:
            config['agent_base_url'] = preset
        elif not str(config.get('agent_base_url') or '').strip() or str(config.get('agent_base_url') or '').strip().rstrip('/') == prev_preset.rstrip('/'):
            config['agent_base_url'] = str(config.get('llm_base_url') or '').strip()
        agent_model = str(config.get('agent_model') or '').strip()
        if not agent_model or agent_model == prev_model:
            config['agent_model'] = current_model

    return config


def config_tts_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    explicit = str(config.get('tts_base_url') or '').strip().rstrip('/')
    if explicit:
        return explicit
    port = int(config.get('tts_port') or 8880)
    return f'http://127.0.0.1:{port}'


def config_llm_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('llm_base_url') or 'http://127.0.0.1:8080/v1').strip().rstrip('/')


def config_llm_chat_url(config: Optional[dict] = None) -> str:
    base = config_llm_base_url(config)
    return base if base.endswith('/chat/completions') else f'{base}/chat/completions'


def config_llm_models_url(config: Optional[dict] = None) -> str:
    base = config_llm_base_url(config)
    if base.endswith('/chat/completions'):
        base = base.rsplit('/chat/completions', 1)[0]
    return f'{base}/models'


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
    base = config_stt_llm_base_url(config)
    if base.endswith('/chat/completions'):
        base = base.rsplit('/chat/completions', 1)[0]
    return f'{base}/models'


def config_stt_llm_api_env(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('stt_llm_api_key_env') or config.get('llm_api_key_env') or '').strip()


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


def config_llm_reasoning(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return 'on' if str(config.get('llm_reasoning') or 'off').strip().lower() == 'on' else 'off'


def config_stt_llm_reasoning(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return 'on' if str(config.get('stt_llm_reasoning') or 'off').strip().lower() == 'on' else 'off'




def agent_provider(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('agent_provider') or 'openai-compatible').strip().lower()


def agent_base_url(config: Optional[dict] = None) -> str:
    config = config or load_config()
    return str(config.get('agent_base_url') or config.get('llm_base_url') or 'http://127.0.0.1:8080/v1').strip().rstrip('/')


def agent_models_url(config: Optional[dict] = None) -> str:
    base = agent_base_url(config)
    if base.endswith('/messages'):
        base = base.rsplit('/messages', 1)[0]
    if base.endswith('/chat/completions'):
        base = base.rsplit('/chat/completions', 1)[0]
    return f'{base}/models'


def agent_chat_url(config: Optional[dict] = None) -> str:
    base = agent_base_url(config)
    provider = agent_provider(config)
    if provider == 'anthropic':
        return base if base.endswith('/messages') else f'{base}/messages'
    return base if base.endswith('/chat/completions') else f'{base}/chat/completions'


def agent_auth_headers(config: dict) -> dict:
    key = api_key_from_config(config, config.get('agent_api_key') or '', config.get('llm_api_key_env') or '')
    provider = agent_provider(config)
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


def set_audio_unsupported(provider: str, base_url: str, model: str,
                          reason: str, last_error: str) -> None:
    """Persist a probe failure. Atomic write via save_config()."""
    triple = audio_unsupported_key(provider, base_url, model)
    cfg = load_config()
    cache = cfg.get("audio_unsupported") or {}
    if not isinstance(cache, dict):
        cache = {}
    from datetime import datetime, timezone
    cache[triple] = {
        "reason": reason,
        "since": datetime.now(timezone.utc).isoformat(),
        "last_error": last_error[:500],  # truncate to keep config.json small
    }
    cfg["audio_unsupported"] = cache
    save_config(cfg)


def clear_audio_unsupported(provider: str, base_url: str, model: str) -> bool:
    """Remove one entry. Returns True if removed."""
    triple = audio_unsupported_key(provider, base_url, model)
    cfg = load_config()
    cache = cfg.get("audio_unsupported") or {}
    if not isinstance(cache, dict) or triple not in cache:
        return False
    del cache[triple]
    cfg["audio_unsupported"] = cache
    save_config(cfg)
    return True


def clear_audio_unsupported_for_provider(provider: str) -> int:
    """Remove all entries for a given provider. Returns count removed."""
    cfg = load_config()
    cache = cfg.get("audio_unsupported") or {}
    if not isinstance(cache, dict):
        return 0
    prefix = f"{str(provider or '').strip()}::"
    kept = {k: v for k, v in cache.items() if not k.startswith(prefix)}
    removed = len(cache) - len(kept)
    if removed > 0:
        cfg["audio_unsupported"] = kept
        save_config(cfg)
    return removed
