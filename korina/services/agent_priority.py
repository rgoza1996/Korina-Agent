"""Priority rules for state reports, kept independent of the agent runtime."""
from __future__ import annotations

import re

_PRIORITY = re.compile(r'^\s*priority\s*:\s*(low|normal|important|critical)\b', re.I | re.M)
_CRITICAL = (
    'permission request:', 'explicit permission', 'approve this command',
    'authorization required', 'destructive command', 'delete data',
    'data loss', 'secret leaked', 'credential exposed',
)
_UNCERTAIN = (
    'garbled', 'whisper', 'transcription uncertainty', 'stt', 'unclear transcript',
    '<think>', 'state report', 'korina agent interrupt', 'acknowledgment loop', 'ack phrase',
)
_IMPORTANT = (
    'critical:', 'urgent:', 'blocked:', 'security risk',
    'cannot continue without', 'task is blocked',
)
PERMISSION_REQUEST = re.compile(r'(?<!\w)permission\s+request\s*:', re.I)


def _contains_cue(text: str, cues: tuple[str, ...]) -> bool:
    """Match whole phrases; 'stt' in 'posttest' is not transcript uncertainty."""
    return any(re.search(r'(?<!\w)' + re.escape(cue) + r'(?!\w)', text, re.I) for cue in cues)


def classify_agent_priority(report: str) -> str:
    text = report or ''
    match = _PRIORITY.search(text)
    explicit = match.group(1).lower() if match else None
    # Permission requests must override lower model labels so the user is asked.
    critical = bool(PERMISSION_REQUEST.search(text)) or _contains_cue(text, _CRITICAL)
    if critical:
        return 'critical'
    if explicit in ('critical', 'important') and _contains_cue(text, _UNCERTAIN):
        return 'normal'
    if explicit:
        return explicit
    return 'important' if _contains_cue(text, _IMPORTANT) else 'normal'
