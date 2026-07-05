from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request

from korina.config import (
    agent_auth_headers,
    agent_chat_url,
    agent_models_url,
    agent_provider,
    load_config,
)

from korina.agents.state import agent_state

from korina.schemas import (
    AgentStateRequest,
    AgentTranscriptRequest,
)

from korina.util.paths import LMSTUDIO_MODEL


def generate_agent_state_report(req: AgentStateRequest) -> str:
    config = load_config()
    selected_model = (req.model or str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL)).strip()
    turns = []
    for m in req.transcript[-int(config.get('agent_max_turns') or 16):]:
        role = m.get('role')
        content = m.get('content')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            turns.append({'role': role, 'content': content.strip()[:2000]})
    autonomy_note = (
        f"Pi-style settings: yolo_mode={config.get('agent_yolo_mode')}, project_trust={config.get('agent_project_trust')}, "
        f"injection_mode={config.get('agent_injection_mode')}, follow_up_mode={config.get('agent_follow_up_mode')}, "
        f"thinking_level={config.get('agent_thinking_level')}, auto_compact={config.get('agent_auto_compact')}."
    )
    system = (
        'You are Korina Agent, an agentic state tracker inspired by Pi Agent Harness concepts: maintain compact state, infer next useful injection, and do not chat with the user.\n'
        'Create a concise state report for Korina Converse to inject into its next spoken reply. Do not write the spoken reply. Do not use emojis.\n'
        'Start with exactly one line: Priority: low|normal|important|critical.\n'
        'Priority rules: low = bookkeeping/debug/no user-facing update. normal = useful state for the next reply only. important = user should hear this soon, but it can wait for a sentence boundary. critical = immediate safety/security/data-loss risk, time-sensitive blocking result, or explicit permission required before a tool call.\n'
        'Do NOT mark garbled STT/Whisper output, uncertain transcript text, routine model errors, repeated observations, or general warnings as critical. Treat transcript uncertainty as low or normal unless it creates an immediate unsafe action.\n'
        'If permission is required, include the exact phrase Permission request: followed by the requested action, risk, and yes/no question.\n'
        'Include: current user intent, relevant facts, unresolved tasks/questions, emotional/interaction notes, and suggested next-response injection.\n'
        'Korina Agent and Korina Converse exchange hidden state. Never speak directly to the user except by emitting important/critical reports for Converse to relay.\n'
        'If yolo_mode is on, be more decisive in suggested injection, but still never perform external side effects from this state-report endpoint.\n'
        f'{autonomy_note}\n'
        'Use compact plain text with short headings.'
    )
    user_payload = {
        'previous_report': req.previous_report[-4000:],
        'recent_transcript': turns,
    }
    provider = agent_provider(config)
    headers = {'Content-Type': 'application/json'} | agent_auth_headers(config)
    if provider == 'anthropic':
        payload = {
            'model': selected_model,
            'system': system,
            'messages': [{'role': 'user', 'content': json.dumps(user_payload, ensure_ascii=False)}],
            'temperature': 0.2,
            'max_tokens': int(req.max_tokens or config.get('agent_max_tokens') or 512),
        }
    else:
        payload = {
            'model': selected_model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(user_payload, ensure_ascii=False)},
            ],
            'temperature': 0.2,
            'max_tokens': int(req.max_tokens or config.get('agent_max_tokens') or 512),
            'stream': False,
        }
    http_req = urllib.request.Request(
        agent_chat_url(config),
        data=json.dumps(payload).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urllib.request.urlopen(http_req, timeout=90) as resp:
            body = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Korina Agent {provider} HTTP {e.code}: {detail}')
    except Exception as e:
        raise RuntimeError(f'Korina Agent request failed: {e}')
    try:
        if provider == 'anthropic':
            parts = body.get('content') or []
            return ''.join(part.get('text', '') for part in parts if isinstance(part, dict)).strip()
        return (body['choices'][0]['message']['content'] or '').strip()
    except Exception:
        raise RuntimeError(f'Unexpected Korina Agent response: {body!r}')

def sanitize_agent_report(report: str) -> str:
    text = report or ''
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.I)
    text = re.sub(r'<think>[\s\S]*', '', text, flags=re.I)
    text = re.sub(r'\[\s*Ack Phrase\s*\]\s*[^.?!]*(?:[.?!]\s*)?', '', text, flags=re.I)
    text = re.sub(r'\bAck Phrase\b\s*', '', text, flags=re.I)
    text = re.sub(r'\[\s*Korina Agent Interrupt\s*\]\s*[^\n]*', '', text, flags=re.I)
    text = re.sub(r'\bKorina Agent Interrupt\s*:\s*[^\n]*', '', text, flags=re.I)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def classify_agent_priority(report: str) -> str:
    text = (report or '').lower()
    m = re.search(r'^\s*priority\s*:\s*(low|normal|important|critical)\b', text, re.I | re.M)
    explicit = m.group(1).lower() if m else None
    critical_cues = ['permission request:', 'explicit permission', 'approve this command', 'authorization required', 'destructive command', 'delete data', 'data loss', 'secret leaked', 'credential exposed']
    garbled_cues = ['garbled', 'whisper', 'transcription uncertainty', 'stt', 'unclear transcript']
    self_loop_cues = ['<think>', 'state report', 'korina agent interrupt', 'acknowledgment loop', 'ack phrase']
    # Guardrail: even if the model says critical/important, garbled STT or self-referential reporting alone is not interrupt-worthy.
    has_critical_cue = any(word in text for word in critical_cues)
    if explicit in ('critical', 'important') and any(word in text for word in garbled_cues + self_loop_cues) and not has_critical_cue:
        return 'normal'
    # Critical is intentionally narrow: immediate safety/security/data-loss/time-sensitive risk or explicit permission.
    # A critical cue must override a lower explicit label from the model (for example: "Priority: normal" plus
    # "Permission request:" still needs to become a permission_request event downstream).
    if has_critical_cue:
        return 'critical'
    if explicit:
        return explicit
    if any(word in text for word in ['critical:', 'urgent:', 'blocked:', 'security risk', 'cannot continue without', 'task is blocked']):
        return 'important'
    # Garbled STT, Whisper uncertainty, generic errors, and routine warnings should not interrupt by keyword accident.
    if any(word in text for word in ['garbled', 'whisper', 'transcription uncertainty', 'stt', 'unclear transcript']):
        return 'normal'
    return 'normal'

def push_agent_event(event: dict) -> dict:
    with agent_state.lock:
        agent_state.event_seq += 1
        event = dict(event)
        event['id'] = agent_state.event_seq
        event['created_at'] = time.time()
        agent_state.events.append(event)
        del agent_state.events[:-100]
        return event

def agent_snapshot() -> dict:
    with agent_state.lock:
        return {
            'busy': agent_state.busy,
            'status': agent_state.status,
            'last_report': agent_state.last_report,
            'pending_injections': len(agent_state.pending_injections),
            'last_error': agent_state.last_error,
            'last_event_id': agent_state.event_seq,
        }

def run_agent_transcript_job(req: AgentTranscriptRequest) -> None:
    config = load_config()
    with agent_state.lock:
        if agent_state.busy:
            agent_state.pending_injections.append({
                'transcript': req.transcript,
                'reason': req.reason,
                'turn_count': req.turn_count,
                'created_at': time.time(),
            })
            queued_busy = True
        else:
            queued_busy = False
        if not queued_busy:
            agent_state.busy = True
            agent_state.status = 'working'
    if queued_busy:
        push_agent_event({'type': 'agent_status', 'status': 'busy_queued_injection', 'priority': 'low', 'message': 'Korina Agent is busy; transcript delta queued as injection.'})
        return
    push_agent_event({'type': 'agent_status', 'status': 'working', 'priority': 'low', 'message': 'Korina Agent received transcript update.'})
    try:
        previous = agent_state.last_report
        with agent_state.lock:
            if agent_state.pending_injections:
                injection_text = '\n\nQueued injection while busy:\n' + json.dumps(agent_state.pending_injections[-5:], ensure_ascii=False)
                agent_state.pending_injections.clear()
            else:
                injection_text = ''
        state_req = AgentStateRequest(
            transcript=req.transcript,
            previous_report=(previous + injection_text)[-6000:],
            model=str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
            max_tokens=int(config.get('agent_max_tokens') or 512),
        )
        agent_input = {
            'delivery_mode': req.delivery_mode,
            'reason': req.reason,
            'turn_count': req.turn_count,
            'previous_report': state_req.previous_report,
            'transcript': state_req.transcript,
            'model': state_req.model,
        }
        raw_report = generate_agent_state_report(state_req)
        report = sanitize_agent_report(raw_report)
        if not report:
            push_agent_event({'type': 'agent_status', 'status': 'empty_report_suppressed', 'priority': 'low', 'message': 'Empty/internal Korina Agent report suppressed.', 'agent_input': agent_input})
            return
        priority = classify_agent_priority(raw_report + '\n' + report)
        report_hash = hashlib.sha256(report.encode('utf-8')).hexdigest()
        now = time.time()
        duplicate_recent = report_hash == agent_state.last_emitted_report_hash and (now - agent_state.last_emitted_report_at) < 60 and priority != 'critical'
        agent_state.last_emitted_report_hash = report_hash
        agent_state.last_emitted_report_at = now
        with agent_state.lock:
            agent_state.last_report = report
            agent_state.status = 'idle'
            agent_state.last_error = None
        event_type = 'permission_request' if priority == 'critical' and 'permission request:' in report.lower() else 'state_report'
        if not duplicate_recent:
            push_agent_event({
                'type': event_type,
                'priority': priority,
                'report': report,
                'message': report,
                'delivery_mode': req.delivery_mode,
                'reason': req.reason,
                'turn_count': req.turn_count,
                'agent_input': agent_input,
                'duplicate_suppressed': False,
            })
        else:
            push_agent_event({'type': 'agent_status', 'status': 'duplicate_report_suppressed', 'priority': 'low', 'message': 'Duplicate Korina Agent report suppressed.', 'agent_input': agent_input})
    except Exception as e:
        with agent_state.lock:
            agent_state.status = 'idle'
            agent_state.last_error = str(e)
        push_agent_event({'type': 'agent_error', 'priority': 'important', 'message': str(e)})
    finally:
        with agent_state.lock:
            agent_state.busy = False

def submit_agent_transcript(req: AgentTranscriptRequest) -> dict:
    config = load_config()
    if str(config.get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'accepted': False, 'disabled': True, 'status': agent_snapshot()}
    if req.delivery_mode == 'injection':
        with agent_state.lock:
            agent_state.pending_injections.append({'transcript': req.transcript, 'reason': req.reason, 'turn_count': req.turn_count, 'created_at': time.time()})
        push_agent_event({'type': 'agent_status', 'status': 'injection_received', 'priority': 'low', 'message': 'Transcript injection queued for Korina Agent.'})
        return {'ok': True, 'accepted': True, 'queued_as': 'injection', 'status': agent_snapshot()}
    threading.Thread(target=run_agent_transcript_job, args=(req,), daemon=True).start()
    return {'ok': True, 'accepted': True, 'queued_as': 'prompt', 'status': agent_snapshot()}



# --- Phase 1.5 helpers moved from korina.config ---

def parse_model_ids(body: dict) -> list:
    models = []
    data = body.get('data', []) if isinstance(body, dict) else []
    if isinstance(data, list):
        for item in data:
            mid = item.get('id') if isinstance(item, dict) else item
            if isinstance(mid, str) and mid.strip():
                models.append(mid.strip())
    return models


def agent_model_choices() -> list:
    from korina.config import agent_models_url, agent_auth_headers, load_config
    config = load_config()
    import urllib.request as _ur
    req = _ur.Request(agent_models_url(config), headers=agent_auth_headers(config), method='GET')
    with _ur.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    return parse_model_ids(body)
