"""GET /api/health — reports service status, loaded models, config snapshot.

Phase 1.9: inlined from the monolith's _route_health helper.
"""

from __future__ import annotations

from fastapi import APIRouter

import torch
from korina.config import (
    agent_base_url,
    agent_chat_url,
    agent_provider,
    config_llm_base_url,
    config_llm_chat_url,
    config_min_speech_ms,
    config_partial_window_ms,
    config_stt_llm_base_url,
    config_stt_llm_chat_url,
    config_stt_llm_model,
    config_stt_llm_provider,
    config_tts_base_url,
    load_config,
)
from korina.runtime import state
from korina.services.ack_service import ack_files_for, ack_status
from korina.services.whisper_service import compute_type_for, normalize_device
from korina.util.paths import (
    LMSTUDIO_MODEL,
    PARTIAL_MIN_SECONDS,
    WHISPER_BEAM_SIZE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_CPU_THREADS,
    WHISPER_DEVICE,
    WHISPER_MODEL_CHOICES,
    WHISPER_MODEL_ID,
)

router = APIRouter()


@router.get('/api/health')
def health():
    config = load_config()
    return {
        'ok': True,
        'service': 'korina-voice-lab',
        'whisper_backend': 'faster-whisper',
        'whisper_model': WHISPER_MODEL_ID,
        'whisper_model_choices': WHISPER_MODEL_CHOICES,
        'whisper_loaded': bool(state.asr.models),
        'whisper_loaded_devices': sorted(state.asr.models.keys()),
        'whisper_loaded_at_by_device': state.asr.loaded_at_by_device,
        'whisper_device': state.asr.device or normalize_device(None, default_env=WHISPER_DEVICE),
        'whisper_compute_type': state.asr.compute_type or compute_type_for(normalize_device(None, default_env=WHISPER_DEVICE)),
        'cuda_available': torch.cuda.is_available(),
        'whisper_beam_size': WHISPER_BEAM_SIZE,
        'whisper_cpu_threads': WHISPER_CPU_THREADS,
        'partial_min_seconds': PARTIAL_MIN_SECONDS,
        'min_speech_ms': config_min_speech_ms(config),
        'partial_window_ms': config_partial_window_ms(config),
        'cuda': torch.cuda.is_available(),
        'response_llm_provider': str(config.get('llm_provider') or 'llama.cpp'),
        'response_llm_base_url': config_llm_base_url(config),
        'response_llm_chat_url': config_llm_chat_url(config),
        'response_llm_model': str(config.get('lm_model') or LMSTUDIO_MODEL),
        'agent_provider': agent_provider(config),
        'agent_base_url': agent_base_url(config),
        'agent_chat_url': agent_chat_url(config),
        'agent_model': str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
        'multimodal_stt_provider': config_stt_llm_provider(config),
        'multimodal_stt_base_url': config_stt_llm_base_url(config),
        'multimodal_stt_chat_url': config_stt_llm_chat_url(config),
        'multimodal_stt_model': config_stt_llm_model(config),
        'tts_base_url': config_tts_base_url(config),
        'ack_count': len(ack_files_for(state.ack.current_voice)),
        'ack_status': ack_status(state.ack.current_voice),
    }