"""Validate user settings updates without revalidating older saved configs.

Defaults come from config.py, but only explicitly supplied fields are saved.
Historical agent controls remain representable; validation does not imply that
retry, compaction, transport, or tool execution has been implemented.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from korina.model_roots import ModelRootList, validate_model_root_paths

from korina.config import (
    DEFAULT_CONFIG,
    LEGACY_AGENT_MODE_KEY,
    LEGACY_DELIVERY_VALUE,
    LEGACY_INJECTION_MODE_VALUE,
)

Toggle = Literal['on', 'off']
Reasoning = Literal['on', 'off', 'auto']
Device = Literal['auto', 'cpu', 'gpu', 'cuda']
Priority = Literal['low', 'normal', 'important', 'critical']
Delivery = Literal['prompt', 'injection']
Name = Annotated[str, Field(max_length=512)]
Provider = Annotated[str, Field(max_length=64)]
SettingText = Annotated[str, Field(max_length=8192)]
# Browser timers use signed 32-bit millisecond delays. Leave room for custom
# timing choices without accepting values that wrap into immediate callbacks.
Milliseconds = Annotated[int, Field(ge=0, le=2_147_483_647)]
PositiveMilliseconds = Annotated[int, Field(ge=1, le=2_147_483_647)]
Seconds = Annotated[int, Field(ge=1, le=2_147_483)]
Count = Annotated[int, Field(ge=0, le=2_147_483_647)]
PositiveCount = Annotated[int, Field(ge=1, le=2_147_483_647)]
_CONVERSE_DEFAULTS = cast(dict[str, Any], DEFAULT_CONFIG['converse'])


def _setting(name: str) -> Any:
    return Field(default_factory=lambda: deepcopy(DEFAULT_CONFIG[name]))


class _UpdateModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore', allow_inf_nan=False)


class ConverseSettingsUpdate(_UpdateModel):
    channel: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        default_factory=lambda: _CONVERSE_DEFAULTS['channel'],
    )
    allowed_origins: list[Annotated[str, Field(min_length=1, max_length=8192)]] = Field(
        default_factory=lambda: list(_CONVERSE_DEFAULTS['allowed_origins']),
    )


class SettingsUpdate(_UpdateModel):
    """A partial update: omission preserves a setting, explicit null is invalid.

    Provider/model names remain generic strings, and blank inherited endpoint
    fields are valid. audio_unsupported is intentionally absent: only server
    probes may update that cache. Unknown keys retain the API's ignore behavior.
    """

    voice: Annotated[str, Field(min_length=1, max_length=128)] = _setting('voice')
    speed: Annotated[float, Field(ge=0.25, le=4.0)] = _setting('speed')
    mode: Literal['sse', 'buf'] = _setting('mode')
    ack_enabled: Toggle = _setting('ack_enabled')
    stt_backend: Literal['whisper', 'llm'] = _setting('stt_backend')
    stt_device: Device = _setting('stt_device')
    stt_model: Name = _setting('stt_model')
    stt_llm_provider: Provider = _setting('stt_llm_provider')
    stt_llm_base_url: SettingText = _setting('stt_llm_base_url')
    stt_llm_api_key_env: Name = _setting('stt_llm_api_key_env')
    stt_llm_model: Name = _setting('stt_llm_model')
    stt_llm_reasoning: Reasoning = _setting('stt_llm_reasoning')
    multimodal_stt_model_allowlist: list[Name] = _setting('multimodal_stt_model_allowlist')
    lm_model: Name = _setting('lm_model')
    tts_device: Device = _setting('tts_device')
    tts_provider: Provider = _setting('tts_provider')
    tts_health_url: SettingText = _setting('tts_health_url')
    tts_port: Annotated[int, Field(ge=1, le=65535)] = _setting('tts_port')
    tts_base_url: SettingText = _setting('tts_base_url')
    tts_model: Name = _setting('tts_model')
    llm_provider: Provider = _setting('llm_provider')
    llm_base_url: SettingText = _setting('llm_base_url')
    llm_api_key_env: Name = _setting('llm_api_key_env')
    llm_reasoning: Reasoning = _setting('llm_reasoning')
    llm_max_tokens: Annotated[int, Field(ge=1, le=8192)] = _setting('llm_max_tokens')
    stt_cloud_provider: Provider = _setting('stt_cloud_provider')
    stt_cloud_base_url: SettingText = _setting('stt_cloud_base_url')
    stt_cloud_model: Name = _setting('stt_cloud_model')
    stt_api_key_env: Name = _setting('stt_api_key_env')
    agent_enabled: Toggle = _setting('agent_enabled')
    agent_provider: Provider = _setting('agent_provider')
    agent_base_url: SettingText = _setting('agent_base_url')
    agent_api_key: SettingText = _setting('agent_api_key')
    agent_model: Name = _setting('agent_model')
    agent_max_turns: Annotated[int, Field(ge=1, le=64)] = _setting('agent_max_turns')
    agent_max_tokens: Annotated[int, Field(ge=1, le=8192)] = _setting('agent_max_tokens')
    agent_yolo_mode: Toggle = _setting('agent_yolo_mode')
    agent_project_trust: Name = _setting('agent_project_trust')
    agent_injection_mode: Name = _setting('agent_injection_mode')
    agent_follow_up_mode: Name = _setting('agent_follow_up_mode')
    agent_thinking_level: Name = _setting('agent_thinking_level')
    agent_auto_compact: Toggle = _setting('agent_auto_compact')
    agent_compaction_reserve_tokens: Count = _setting('agent_compaction_reserve_tokens')
    agent_compaction_keep_recent_tokens: Count = _setting('agent_compaction_keep_recent_tokens')
    agent_hide_thinking: Toggle = _setting('agent_hide_thinking')
    agent_transport: Name = _setting('agent_transport')
    agent_retry_enabled: Toggle = _setting('agent_retry_enabled')
    agent_max_retries: Count = _setting('agent_max_retries')
    agent_retry_base_delay_ms: Milliseconds = _setting('agent_retry_base_delay_ms')
    agent_http_idle_timeout_ms: Milliseconds = _setting('agent_http_idle_timeout_ms')
    agent_enable_skill_commands: Toggle = _setting('agent_enable_skill_commands')
    agent_block_images: Toggle = _setting('agent_block_images')
    agent_first_delivery_mode: Literal['first_turn_or_timer', 'first_turn', 'timer'] = _setting('agent_first_delivery_mode')
    agent_first_delivery_seconds: Seconds = _setting('agent_first_delivery_seconds')
    agent_periodic_delivery_turns: PositiveCount = _setting('agent_periodic_delivery_turns')
    agent_periodic_delivery_seconds: Seconds = _setting('agent_periodic_delivery_seconds')
    agent_busy_delivery_mode: Delivery = _setting('agent_busy_delivery_mode')
    agent_idle_delivery_mode: Delivery = _setting('agent_idle_delivery_mode')
    agent_interrupts_enabled: Toggle = _setting('agent_interrupts_enabled')
    agent_interrupt_min_priority: Priority = _setting('agent_interrupt_min_priority')
    agent_hard_interrupt_min_priority: Priority = _setting('agent_hard_interrupt_min_priority')
    agent_interrupt_cooldown_padding_ms: Milliseconds = _setting('agent_interrupt_cooldown_padding_ms')
    agent_permission_interrupts: Toggle = _setting('agent_permission_interrupts')
    agent_report_injection_mode: Literal['next_reply', 'silent'] = _setting('agent_report_injection_mode')
    endpoint_mode: Literal['reading', 'conversation'] = _setting('endpoint_mode')
    silence_ms: PositiveMilliseconds = _setting('silence_ms')
    final_stt_mode: Literal['chunks', 'full'] = _setting('final_stt_mode')
    min_speech_ms: Annotated[int, Field(ge=300, le=2_147_483_647)] = _setting('min_speech_ms')
    partial_window_ms: Annotated[int, Field(ge=400, le=2_147_483_647)] = _setting('partial_window_ms')
    idle_ack_initial_ms: PositiveMilliseconds = _setting('idle_ack_initial_ms')
    idle_ack_step_ms: PositiveMilliseconds = _setting('idle_ack_step_ms')
    local_model_roots: ModelRootList = _setting('local_model_roots')
    converse: ConverseSettingsUpdate = Field(default_factory=ConverseSettingsUpdate)

    @field_validator('local_model_roots')
    @classmethod
    def valid_model_roots(cls, value: list[str]) -> list[str]:
        return validate_model_root_paths(value)

    @model_validator(mode='before')
    @classmethod
    def normalize_legacy_values(cls, value):
        if not isinstance(value, dict):
            return value
        updated = dict(value)
        if LEGACY_AGENT_MODE_KEY in updated and 'agent_injection_mode' not in updated:
            updated['agent_injection_mode'] = updated[LEGACY_AGENT_MODE_KEY]
        if updated.get('agent_injection_mode') == LEGACY_INJECTION_MODE_VALUE:
            updated['agent_injection_mode'] = 'one-at-a-time'
        for key in ('agent_busy_delivery_mode', 'agent_idle_delivery_mode'):
            if updated.get(key) == LEGACY_DELIVERY_VALUE:
                updated[key] = 'injection'
        return updated
