"""ACK endpoints — list / status / rebuild.

Phase 1.9: inlined from the monolith's _route_acks* helpers.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from korina.runtime import state
from korina.services.ack_service import (
    ack_files_for,
    ack_status,
    clear_ack_wavs,
    enqueue_missing_acks,
    load_ack_manifest,
)
from korina.services.ack_manifest import AckManifestError
from korina.util.paths import ACK_DEFAULT_VOICE

router = APIRouter()


def _manifest():
    try:
        return load_ack_manifest()
    except AckManifestError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


class AckRebuildRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    voice: str = Field(default=ACK_DEFAULT_VOICE, min_length=1, max_length=64)
    tag: str | None = Field(default=None, min_length=1, max_length=64)
    clear: bool = False


@router.get('/api/acks')
def acks(voice: str = Query(ACK_DEFAULT_VOICE, min_length=1, max_length=64), tag: Optional[str] = Query(None, min_length=1, max_length=64)):
    manifest = _manifest()
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    enqueue_missing_acks(voice, tag, manifest=manifest)
    return {
        'acks': ack_files_for(voice, tag, manifest=manifest),
        'tag': tag or 'global',
        'status': ack_status(voice, manifest=manifest),
    }


@router.get('/api/acks/status')
def acks_status(voice: str = Query(ACK_DEFAULT_VOICE, min_length=1, max_length=64)):
    return ack_status((voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE, manifest=_manifest())


@router.post('/api/acks/rebuild')
def acks_rebuild(payload: AckRebuildRequest):
    manifest = _manifest()  # Validate before deleting cache or invalidating work.
    voice, tag = payload.voice, payload.tag
    removed = clear_ack_wavs() if payload.clear else 0
    with state.ack.queue_lock:
        state.ack.current_voice = voice
    missing = enqueue_missing_acks(voice, tag, force=True, manifest=manifest)
    return {'ok': True, 'voice': voice, 'tag': tag, 'removed': removed, 'queued_or_missing': missing, 'status': ack_status(voice, manifest=manifest)}
