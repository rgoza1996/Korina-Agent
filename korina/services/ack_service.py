from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import threading
import urllib.request

from korina.config import config_tts_base_url, load_config

from korina.runtime import state

from korina.util.labels import safe_slug

from korina.util.paths import (
    ACK_DEFAULT_VOICE,
    ACK_DIR,
    ACK_PHRASES_PATH,
)

from pathlib import Path

from typing import Optional

logger = logging.getLogger(__name__)
MAX_ACK_QUEUE = 128
MAX_ACK_WAV_BYTES = 2 * 1024 * 1024
ACK_HTTP_TIMEOUT = 30


def acks_enabled() -> bool:
    return str(load_config().get('ack_enabled') or 'on') == 'on'


def _wav_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def load_ack_manifest() -> list[dict]:
    """Read ack_phrases.json. Supports old list[str] format and new tagged format."""
    if not ACK_PHRASES_PATH.exists():
        return []
    data = json.loads(ACK_PHRASES_PATH.read_text())
    if isinstance(data, list):
        phrases = data
        return [
            {'id': f'ack_{i:02d}', 'text': str(text), 'tags': ['global']}
            for i, text in enumerate(phrases, start=1)
            if str(text).strip()
        ]
    raw_phrases = data.get('phrases', []) if isinstance(data, dict) else []
    out = []
    for i, item in enumerate(raw_phrases, start=1):
        if isinstance(item, str):
            text = item.strip()
            tags = ['global']
            ack_id = f'ack_{i:02d}'
        elif isinstance(item, dict):
            text = str(item.get('text', '')).strip()
            tags = item.get('tags') or ['global']
            if isinstance(tags, str):
                tags = [tags]
            tags = [safe_slug(str(t)) for t in tags if str(t).strip()] or ['global']
            ack_id = safe_slug(str(item.get('id') or text or f'ack_{i:02d}'))
        else:
            continue
        if text:
            out.append({'id': ack_id, 'text': text, 'tags': tags})
    return out

def ack_filename(voice: str, phrase: dict) -> str:
    digest = hashlib.sha1(f"{phrase['id']}|{phrase['text']}".encode('utf-8')).hexdigest()[:8]
    return f"{safe_slug(voice)}__{safe_slug(phrase['id'])}__{digest}.wav"

def ack_path(voice: str, phrase: dict) -> Path:
    return ACK_DIR / ack_filename(voice, phrase)

def ack_files_for(voice: str, tag: Optional[str] = None) -> list[dict]:
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    requested_tag = safe_slug(tag or 'global')
    manifest = load_ack_manifest()
    matching = [p for p in manifest if requested_tag in p.get('tags', [])]
    if tag and not matching:
        matching = [p for p in manifest if 'global' in p.get('tags', [])]
    elif not tag:
        matching = [p for p in manifest if 'global' in p.get('tags', [])]
    files = []
    for phrase in matching:
        path = ack_path(voice, phrase)
        size = _wav_size(path)
        if size > 44:
            files.append({
                'id': phrase['id'],
                'text': phrase['text'],
                'tags': phrase.get('tags', ['global']),
                'voice': voice,
                'name': path.name,
                'url': f'/Ack/{path.name}',
                'bytes': size,
            })
    return files

def missing_ack_phrases(voice: str, tag: Optional[str] = None) -> list[dict]:
    requested_tag = safe_slug(tag) if tag else None
    manifest = load_ack_manifest()
    if requested_tag:
        manifest = [p for p in manifest if requested_tag in p.get('tags', [])]
    missing = []
    for phrase in manifest:
        path = ack_path(voice, phrase)
        if _wav_size(path) <= 44:
            missing.append(phrase)
    return missing

def enqueue_missing_acks(voice: str, tag: Optional[str] = None, *, force: bool = False) -> int:
    """Queue at most MAX_ACK_QUEUE missing phrases; return the missing count.

    Normal background work respects the enabled setting. An explicit rebuild
    can force generation even while playback is disabled.
    """
    voice = (voice or ACK_DEFAULT_VOICE).strip() or ACK_DEFAULT_VOICE
    missing = missing_ack_phrases(voice, tag)
    if not force and not acks_enabled():
        return len(missing)
    with state.ack.queue_lock:
        generation = state.ack.generation
        existing = {(v, pid) for v, pid, _, _ in state.ack.queue}
        existing.update(key for key, epoch in state.ack.in_progress.items() if epoch == generation)
        for phrase in missing:
            if len(state.ack.queue) >= MAX_ACK_QUEUE:
                break
            key = (voice, phrase['id'])
            if key not in existing:
                state.ack.queue.append((voice, phrase['id'], phrase['text'], force))
                existing.add(key)
        if state.ack.queue and not state.ack.worker_running:
            state.ack.worker_running = True
            try:
                threading.Thread(target=ack_generation_worker, daemon=True).start()
            except Exception:
                state.ack.worker_running = False
                raise
    return len(missing)


def _generation_allowed(generation: int, force: bool) -> bool:
    # Caller holds queue_lock, including the final atomic-publication check.
    return generation == state.ack.generation and (force or acks_enabled())


def synthesize_ack_wav(
    voice: str, phrase_id: str, text: str, *, generation: int | None = None, force: bool = False,
) -> bool:
    """Stream bounded audio to a temp file and publish only current work."""
    with state.ack.queue_lock:
        if generation is None:
            generation = state.ack.generation
        if not _generation_allowed(generation, force):
            return False
    phrase = next((p for p in load_ack_manifest() if p['id'] == phrase_id), {'id': phrase_id, 'text': text, 'tags': ['global']})
    out = ack_path(voice, phrase)
    payload = json.dumps({'input': text, 'voice': voice, 'speed': 1.0, 'device': 'cpu'}).encode('utf-8')
    request = urllib.request.Request(
        f'{config_tts_base_url()}/v1/audio/speech', data=payload,
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    ACK_DIR.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=ACK_DIR, prefix='ack-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            size = 0
            with urllib.request.urlopen(request, timeout=ACK_HTTP_TIMEOUT) as response:
                while True:
                    with state.ack.queue_lock:
                        if not _generation_allowed(generation, force):
                            return False
                    chunk = response.read(min(65536, MAX_ACK_WAV_BYTES - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ACK_WAV_BYTES:
                        raise RuntimeError(f'generated ack exceeds {MAX_ACK_WAV_BYTES} bytes')
                    output.write(chunk)
            if size <= 44:
                raise RuntimeError(f'generated ack for {phrase_id} was empty')
        with temporary.open('rb') as audio:
            header = audio.read(12)
        if header[:4] != b'RIFF' or header[8:12] != b'WAVE':
            raise RuntimeError(f'generated ack for {phrase_id} was not a WAV file')
        with state.ack.queue_lock:
            if not _generation_allowed(generation, force):
                return False
            temporary.replace(out)
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def ack_generation_worker() -> None:
    """One worker drains a bounded queue; cache clears invalidate old jobs."""
    while True:
        with state.ack.queue_lock:
            if not state.ack.queue:
                state.ack.worker_running = False
                return
            voice, phrase_id, text, force = state.ack.queue.pop(0)
            generation = state.ack.generation
            key = (voice, phrase_id)
            state.ack.in_progress[key] = generation
        try:
            generated = synthesize_ack_wav(voice, phrase_id, text, generation=generation, force=force)
            with state.ack.queue_lock:
                if generated and generation == state.ack.generation:
                    state.ack.last_generated = f'{voice}:{phrase_id}'
                    state.ack.last_error = None
        except Exception as exc:
            with state.ack.queue_lock:
                if generation == state.ack.generation:
                    state.ack.last_error = f'{voice}:{phrase_id}: {exc}'
                    logger.warning('ACK generation failed: %s', state.ack.last_error)
        finally:
            with state.ack.queue_lock:
                if state.ack.in_progress.get(key) == generation:
                    state.ack.in_progress.pop(key, None)


def clear_ack_wavs() -> int:
    """Invalidate queued/in-flight work before clearing published audio."""
    with state.ack.queue_lock:
        state.ack.generation += 1
        state.ack.queue.clear()
        state.ack.last_generated = None
        state.ack.last_error = None
        count = 0
        for path in ACK_DIR.glob('*.wav'):
            try:
                path.unlink()
                count += 1
            except FileNotFoundError:
                pass
        return count


def cancel_ack_generation() -> None:
    """Drop pending work on app shutdown while preserving published audio."""
    with state.ack.queue_lock:
        state.ack.generation += 1
        state.ack.queue.clear()


def ack_status(voice: str) -> dict:
    manifest = load_ack_manifest()
    generated_count = sum(_wav_size(ack_path(voice, phrase)) > 44 for phrase in manifest)
    with state.ack.queue_lock:
        snapshot = {
            'queue_depth': len(state.ack.queue),
            'queue_limit': MAX_ACK_QUEUE,
            'in_progress': [f'{v}:{pid}' for v, pid in sorted(state.ack.in_progress)],
            'generating': state.ack.worker_running,
            'last_generated': state.ack.last_generated,
            'last_error': state.ack.last_error,
        }
    return {
        'voice': voice, 'manifest_count': len(manifest),
        'generated_count': generated_count, 'missing_count': len(manifest) - generated_count,
        'enabled': acks_enabled(), **snapshot,
    }
