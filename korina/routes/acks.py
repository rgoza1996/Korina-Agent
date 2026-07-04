"""ACK endpoints — list / status / rebuild.

Phase 1.9: inlined from the monolith's _route_acks* helpers.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from korina.runtime import state
from korina.services.ack_service import (
    ack_files_for,
    ack_status,
    clear_ack_wavs,
    enqueue_missing_acks,
)
from korina.util.paths import ACK_DEFAULT_VOICE

router = APIRouter()


@router.get('/api/acks')
def acks(voice: str = Query('af_heart'), tag: Optional[str] = Query(None)):
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    enqueue_missing_acks(voice, tag)
    return {
        'acks': ack_files_for(voice, tag),
        'tag': tag or 'global',
        'status': ack_status(voice),
    }


@router.get('/api/acks/status')
def acks_status(voice: str = Query('af_heart')):
    return ack_status((voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE)


@router.post('/api/acks/rebuild')
async def acks_rebuild(payload: dict):
    voice = (payload.get('voice') or ACK_DEFAULT_VOICE).strip()
    tag = payload.get('tag')
    clear = bool(payload.get('clear', False))
    if clear:
        removed = clear_ack_wavs()
    else:
        removed = 0
    state.ack.current_voice = voice
    missing = enqueue_missing_acks(voice, tag)
    return {'ok': True, 'voice': voice, 'tag': tag, 'removed': removed, 'queued_or_missing': missing, 'status': ack_status(voice)}