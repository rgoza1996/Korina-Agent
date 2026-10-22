"""ACK endpoints — list / status / rebuild.

Phase 1.9: inlined from the monolith's _route_acks* helpers.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from korina.runtime import state
from korina.services.ack_service import (
    ack_files_for,
    ack_status,
    clear_ack_wavs,
    enqueue_missing_acks,
)
from korina.util.paths import ACK_DEFAULT_VOICE

router = APIRouter()


class AckRebuildRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    voice: str = Field(default=ACK_DEFAULT_VOICE, min_length=1, max_length=64)
    tag: str | None = Field(default=None, min_length=1, max_length=64)
    clear: bool = False


@router.get('/api/acks')
def acks(voice: str = Query(ACK_DEFAULT_VOICE, min_length=1, max_length=64), tag: Optional[str] = Query(None, min_length=1, max_length=64)):
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    enqueue_missing_acks(voice, tag)
    return {
        'acks': ack_files_for(voice, tag),
        'tag': tag or 'global',
        'status': ack_status(voice),
    }


@router.get('/api/acks/status')
def acks_status(voice: str = Query(ACK_DEFAULT_VOICE, min_length=1, max_length=64)):
    return ack_status((voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE)


@router.post('/api/acks/rebuild')
def acks_rebuild(payload: AckRebuildRequest):
    voice, tag = payload.voice, payload.tag
    removed = clear_ack_wavs() if payload.clear else 0
    with state.ack.queue_lock:
        state.ack.current_voice = voice
    missing = enqueue_missing_acks(voice, tag, force=True)
    return {'ok': True, 'voice': voice, 'tag': tag, 'removed': removed, 'queued_or_missing': missing, 'status': ack_status(voice)}
