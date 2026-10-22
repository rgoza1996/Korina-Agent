from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
import time
from collections.abc import Callable

from korina.config import (
    agent_auth_headers,
    agent_chat_url,
    agent_provider,
    load_config,
)

from korina.agents.state import agent_state, expected_builtin_generation
from korina.runtime.json_http import HTTPStatusError, RequestCancelledError, request_json
from korina.runtime.completion import anthropic_text_completion, openai_text_completion
from korina.services.agent_priority import PERMISSION_REQUEST, classify_agent_priority
from korina.services.agent_context import (
    AgentContext, build_agent_context, prepare_queued_injection, previous_with_injections,
)
from korina.services.agent_events import MAX_AGENT_EVENT_BYTES, MAX_AGENT_EVENTS, agent_json_size

from korina.schemas import (
    AgentStateRequest,
    AgentTranscriptRequest,
)

from korina.util.paths import LMSTUDIO_MODEL


def generate_agent_state_report(
    req: AgentStateRequest, *, is_current: Callable[[], bool] | None = None,
    config: dict | None = None, context: AgentContext | None = None,
) -> str:
    config = config if config is not None else load_config()
    selected_model = (req.model or str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL)).strip()
    user_payload = context if context is not None else build_agent_context(req, config)
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
    try:
        body = request_json(agent_chat_url(config), method='POST', headers=headers, json_body=payload, timeout=90, is_current=is_current)
    except RequestCancelledError:
        raise
    except HTTPStatusError as e:
        raise RuntimeError(f'Korina Agent {provider} HTTP {e.status_code}: {e.detail}') from e
    except Exception as e:
        raise RuntimeError(f'Korina Agent request failed: {e}')
    if provider == 'anthropic':
        return anthropic_text_completion(body)
    return openai_text_completion(body).text

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

def push_agent_event(event: dict, *, generation: int | None = None) -> dict:
    """Publish owned JSON data; nested values must stay immutable afterward."""
    with agent_state.lock:
        if generation is not None and generation != agent_state.generation:
            return {}
        event = dict(event)
        event['id'] = agent_state.event_seq + 1
        event['created_at'] = time.time()
        size = agent_json_size(event)
        agent_state.event_seq += 1
        agent_state.events.append(event)
        agent_state.event_sizes.append(size)
        agent_state.event_bytes += size
        while len(agent_state.events) > MAX_AGENT_EVENTS or agent_state.event_bytes > MAX_AGENT_EVENT_BYTES:
            agent_state.events.pop(0)
            agent_state.event_bytes -= agent_state.event_sizes.pop(0)
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

# One running request and one replaceable follow-up snapshot. A burst of
# transcript deliveries never creates another thread or an unbounded job queue.
_MAX_PENDING_INJECTIONS = 5
_executor: ThreadPoolExecutor | None = None
_worker_running = False
_worker_epoch = 0
_queued_prompt: tuple[AgentTranscriptRequest, int] | None = None


def _queue_injection(req: AgentTranscriptRequest) -> None:
    """Caller holds agent_state.lock; keep only recent bounded snapshots."""
    agent_state.pending_injections.append(prepare_queued_injection(req, time.time()))
    del agent_state.pending_injections[:-_MAX_PENDING_INJECTIONS]


def _process_transcript(req: AgentTranscriptRequest, generation: int) -> None:
    config = load_config()
    if str(config.get('agent_enabled') or 'on') == 'off':
        return
    with agent_state.lock:
        if generation != agent_state.generation:
            return
        agent_state.status = 'working'
        previous = previous_with_injections(agent_state.last_report, agent_state.pending_injections)
        agent_state.pending_injections.clear()
    push_agent_event({'type': 'agent_status', 'status': 'working', 'priority': 'low', 'message': 'Korina Agent received transcript update.'}, generation=generation)
    try:
        state_req = AgentStateRequest(
            transcript=req.transcript,
            previous_report=previous,
            model=str(config.get('agent_model') or config.get('lm_model') or LMSTUDIO_MODEL),
            max_tokens=int(config.get('agent_max_tokens') or 512),
        )
        context = build_agent_context(state_req, config)
        agent_input = {
            'delivery_mode': req.delivery_mode,
            'reason': req.reason,
            'turn_count': req.turn_count,
            'previous_report': context['previous_report'],
            'transcript': context['recent_transcript'],
            'model': (state_req.model or '').strip(),
        }
        def is_current() -> bool:
            enabled = str(load_config().get('agent_enabled') or 'on') != 'off'
            with agent_state.lock:
                return enabled and generation == agent_state.generation

        report = sanitize_agent_report(generate_agent_state_report(state_req, is_current=is_current, config=config, context=context))
        if not report:
            push_agent_event({'type': 'agent_status', 'status': 'empty_report_suppressed', 'priority': 'low', 'message': 'Empty/internal Korina Agent report suppressed.', 'agent_input': agent_input}, generation=generation)
            return
        # Hidden reasoning must not promote the visible report's priority.
        priority = classify_agent_priority(report)
        report_hash = hashlib.sha256(report.encode('utf-8')).hexdigest()
        now = time.time()
        with agent_state.lock:
            if generation != agent_state.generation:
                return
            duplicate_recent = report_hash == agent_state.last_emitted_report_hash and (now - agent_state.last_emitted_report_at) < 60 and priority != 'critical'
            agent_state.last_emitted_report_hash = report_hash
            agent_state.last_emitted_report_at = now
            agent_state.last_report = report
            agent_state.last_error = None
        event_type = 'permission_request' if priority == 'critical' and PERMISSION_REQUEST.search(report) else 'state_report'
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
            }, generation=generation)
        else:
            push_agent_event({'type': 'agent_status', 'status': 'duplicate_report_suppressed', 'priority': 'low', 'message': 'Duplicate Korina Agent report suppressed.', 'agent_input': agent_input}, generation=generation)
    except RequestCancelledError:
        return
    except Exception as exc:
        with agent_state.lock:
            if generation != agent_state.generation:
                return
            agent_state.last_error = str(exc)
        push_agent_event({'type': 'agent_error', 'priority': 'important', 'message': str(exc)}, generation=generation)


def _drain_transcript_jobs(req: AgentTranscriptRequest, generation: int, epoch: int) -> None:
    global _worker_running, _queued_prompt
    while True:
        with agent_state.lock:
            if epoch != _worker_epoch:
                return
        _process_transcript(req, generation)
        with agent_state.lock:
            if epoch != _worker_epoch:
                return
            if _queued_prompt is None:
                _worker_running = False
                agent_state.busy = False
                agent_state.status = 'idle'
                return
            req, generation = _queued_prompt
            _queued_prompt = None


def reset_agent_state() -> None:
    """Clear the session and invalidate output from any in-flight model call."""
    global _queued_prompt
    with agent_state.lock:
        agent_state.generation += 1
        _queued_prompt = None
        agent_state.events.clear()
        agent_state.event_sizes.clear()
        agent_state.event_bytes = 0
        # Event ids are monotonic within a session; reset restarts the cursor.
        agent_state.event_seq = 0
        agent_state.pending_injections.clear()
        agent_state.busy = False
        agent_state.status = 'idle'
        agent_state.last_report = ''
        agent_state.last_error = None
        agent_state.last_emitted_report_hash = ''
        agent_state.last_emitted_report_at = 0.0


def shutdown_agent_worker() -> None:
    """Drop queued work and suppress in-flight output; next submit can restart.

    Owned connected HTTP I/O stops on its next ownership check (about 50 ms).
    Shutdown does not wait or establish cancellation of remote model compute.
    """
    global _executor, _worker_running, _worker_epoch
    with agent_state.lock:
        reset_agent_state()
        executor, _executor = _executor, None
        _worker_running = False
        _worker_epoch += 1
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def submit_agent_transcript(req: AgentTranscriptRequest) -> dict:
    return _admit_agent_request(req)


def submit_agent_permission(req: AgentTranscriptRequest, event: dict) -> dict:
    """Admit the answer event and its follow-up transcript as one operation."""
    return _admit_agent_request(req, permission_event=event)


def _admit_agent_request(req: AgentTranscriptRequest, *, permission_event: dict | None = None) -> dict:
    global _executor, _worker_running, _worker_epoch, _queued_prompt
    admission_generation = expected_builtin_generation()
    if admission_generation is None:
        with agent_state.lock:
            admission_generation = agent_state.generation
    # Never acquire the config transaction lock while holding agent_state.lock:
    # a successful disable persists under that lock before resetting the agent.
    config = load_config()
    if str(config.get('agent_enabled') or 'on') == 'off':
        return {'ok': True, 'accepted': False, 'disabled': True, 'status': agent_snapshot()}
    with agent_state.lock:
        if admission_generation != agent_state.generation:
            return {'ok': True, 'accepted': False, 'stale': True, 'status': agent_snapshot()}
        notification = None
        if req.delivery_mode == 'injection':
            _queue_injection(req)
            notification = {'type': 'agent_status', 'status': 'injection_received', 'priority': 'low', 'message': 'Transcript injection queued for Korina Agent.'}
            queued_as = 'injection'
        elif _worker_running:
            # Full transcript snapshots supersede earlier queued prompts.
            # Preserve their bounded context as injections for the follow-up.
            if _queued_prompt is not None:
                _queue_injection(_queued_prompt[0])
            _queued_prompt = (req, agent_state.generation)
            agent_state.busy = True
            notification = {'type': 'agent_status', 'status': 'busy_queued_injection', 'priority': 'low', 'message': 'Korina Agent is busy; latest transcript queued.'}
            queued_as = 'injection'
        else:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='korina-agent')
            previous_busy, previous_status = agent_state.busy, agent_state.status
            _worker_running = True
            agent_state.busy = True
            agent_state.status = 'working'
            try:
                _executor.submit(_drain_transcript_jobs, req, agent_state.generation, _worker_epoch)
            except BaseException:
                # ThreadPoolExecutor can queue a WorkItem before starting its
                # thread fails. Retire it and invalidate that job before the
                # agent lock is released; a retry must use a fresh executor.
                failed, _executor = _executor, None
                _worker_epoch += 1
                _worker_running = False
                agent_state.busy, agent_state.status = previous_busy, previous_status
                try:
                    failed.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Failed to retire agent executor after submission failure')
                raise
            queued_as = 'prompt'
        if permission_event is not None:
            push_agent_event(permission_event, generation=admission_generation)
        if notification is not None:
            push_agent_event(notification, generation=admission_generation)
    return {'ok': True, 'accepted': True, 'queued_as': queued_as, 'status': agent_snapshot()}


# --- Phase 1.5 helpers moved from korina.config ---

def parse_model_ids(body: dict, *, pinned=()) -> list:
    from korina.model_ids import limit_model_ids
    data = body.get('data', []) if isinstance(body, dict) else []
    if not isinstance(data, list):
        data = []
    return limit_model_ids((item.get('id') if isinstance(item, dict) else item
                            for item in data), pinned=pinned)


def agent_model_choices(config: dict | None = None) -> list:
    from korina.config import agent_models_url, agent_auth_headers, load_config
    config = config if config is not None else load_config()
    body = request_json(agent_models_url(config), headers=agent_auth_headers(config), timeout=20)
    return parse_model_ids(body, pinned=[config.get('agent_model') or config.get('lm_model')])
