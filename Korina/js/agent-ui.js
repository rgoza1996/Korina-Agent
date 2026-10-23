// Agent transcript delivery, event polling, interruptions, and response orchestration.

import { state } from "./state.js";
import { apiErrorMessage } from './api-errors.js';
import { $, status, log } from "./dom.js";
import { stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry } from "./history.js";
import { agentEnabled, agentInterruptCooldownPaddingMs, lmModel, llmProvider, llmReasoningEnabled, llmReasoningEffort, llmMaxTokens } from "./settings-ui.js";
import { speakText, stopTtsNow } from "./speech.js";
import { saveAgentEnabled } from "./api.js";

// Priority lookup table.
// Keep this ceiling aligned with ChatRequest.message on the backend.
const MAX_CHAT_MESSAGE_CHARS = 8192;
const MAX_MODEL_HISTORY_TURNS = 12;
const priorityRank = { low: 0, normal: 1, important: 2, critical: 3 };
let deliveryInFlight = null;
let eventPollInFlight = null;
let activeInterruptSpeech = null;
let permissionAnswerInFlight = null;
const ownedRequests = new Set();
const chatRequests = new Set();
let responseUsageOwner = null;

function renderResponseUsage(message, observation = null) {
  const write = (id, value) => { const element = $(id); if (element) element.textContent = value; };
  write('responseUsageStatus', message);
  const settings = observation?.settings;
  write('responseUsageSettings', settings
    ? `Provider: ${settings.provider} · Model: ${settings.model} · Output budget: ${settings.max_tokens} · ${settings.provider === 'meta'
      ? `Reasoning effort: ${settings.reasoning_effort || 'model default'}` : `Reasoning: ${settings.reasoning}`}`
    : 'Request settings unavailable.');
  for (const [field, id] of Object.entries({
    prompt_tokens: 'responsePromptTokens', completion_tokens: 'responseCompletionTokens',
    reasoning_tokens: 'responseReasoningTokens', cached_tokens: 'responseCachedTokens',
  })) write(id, observation?.usage?.[field] == null ? 'Unavailable' : String(observation.usage[field]));
}

function responseUsageObservation(result) {
  const record = value => value && typeof value === 'object' && !Array.isArray(value);
  const bounded = (value, limit) => typeof value === 'string' && value.length <= limit * 2 && [...value].length <= limit;
  const settings = result?.request_settings;
  if (!record(settings) || !bounded(settings.provider, 64) || !settings.provider.trim()
      || !bounded(settings.model, 512) || !settings.model.trim()
      || !Number.isSafeInteger(settings.max_tokens) || settings.max_tokens < 1 || settings.max_tokens > 8192) return null;
  if (settings.provider === 'meta') {
    if (settings.reasoning !== null || !['', 'minimal', 'low', 'medium', 'high', 'xhigh'].includes(settings.reasoning_effort)) return null;
  } else if (settings.reasoning_effort !== null || !bounded(settings.reasoning, 64)) return null;
  let usage = result.usage;
  const fields = ['prompt_tokens', 'completion_tokens', 'reasoning_tokens', 'cached_tokens'];
  if (!record(usage) || fields.some(field => usage[field] !== null && (!Number.isSafeInteger(usage[field]) || usage[field] < 0))
      || (usage.cached_tokens !== null && usage.prompt_tokens !== null && usage.cached_tokens > usage.prompt_tokens)
      || (usage.reasoning_tokens !== null && usage.completion_tokens !== null && usage.reasoning_tokens > usage.completion_tokens)) usage = null;
  else usage = Object.fromEntries(fields.map(field => [field, usage[field]]));
  return { settings, usage };
}

export function clearResponseUsage() {
  responseUsageOwner = null;
  renderResponseUsage('No response yet.');
}

// Clear retires all existing conversation requests; later requests own new controllers.
export function cancelChatRequests() {
  const retiring = [...chatRequests];
  chatRequests.clear();
  for (const controller of retiring) controller.abort();
}

function agentActive() {
  return agentEnabled() === 'on' && !state.agentResetBlocked && !state.agentActivityBlocked;
}

function agentOwnership(isCurrent = () => true) {
  const session = state.agentSession;
  const epoch = state.agentActivityEpoch;
  return () => agentActive() && session === state.agentSession && epoch === state.agentActivityEpoch && isCurrent();
}

function beginAgentRequest() {
  const controller = new AbortController();
  ownedRequests.add(controller);
  return controller;
}

function retireAgentActivity() {
  state.agentActivityEpoch++;
  for (const controller of ownedRequests) controller.abort();
  ownedRequests.clear();
  deliveryInFlight = eventPollInFlight = permissionAnswerInFlight = null;
  if (activeInterruptSpeech && activeInterruptSpeech.controller === state.controller) stopTtsNow();
  activeInterruptSpeech = null;
  state.agentInterruptInProgress = false;
  state.agentStateReport = state.pendingAgentStateReport = state.pendingImportantAgentMessage = '';
  state.pendingAgentReportVersion++;
  state.awaitingAgentPermission = state.deferredAgentInterrupt = null;
  state.agentInterruptCooldownUntil = 0;
  state.agentFirstDelivered = false;
  state.agentLastDeliveredTurn = 0;
  state.agentLastDeliveryAt = 0;
  state.agentStartedAt = performance.now();
}

export async function setAgentEnabledFromUi() {
  const enabled = agentEnabled() === 'on';
  retireAgentActivity();
  const epoch = state.agentActivityEpoch;
  const session = state.agentSession;
  state.agentControlPending = state.agentActivityBlocked = true;
  try {
    await saveAgentEnabled(enabled);
    if (epoch !== state.agentActivityEpoch) return false;
    if (enabled && session === state.agentSession) {
      const controller = beginAgentRequest();
      try {
        const response = await fetch('/api/agent/events?after=0', { signal: controller.signal });
        const result = await response.json();
        if (epoch !== state.agentActivityEpoch) return false;
        if (!response.ok || !Number.isSafeInteger(result?.last_event_id) || result.last_event_id < 0) {
          throw new Error('Agent event cursor was not confirmed');
        }
        // Custom adapters keep their memory; skip their pre-enable event backlog.
        if (session === state.agentSession) state.agentEventCursor = result.last_event_id;
      } finally { ownedRequests.delete(controller); }
    }
    if (epoch !== state.agentActivityEpoch) return false;
    state.agentControlPending = state.agentActivityBlocked = false;
    $('settingsActionStatus').textContent = enabled ? 'Korina Agent enabled with fresh delivery context.' : 'Korina Agent disabled. Conversation can continue.';
    return true;
  } catch (error) {
    if (epoch !== state.agentActivityEpoch) return false;
    $('settingsActionStatus').textContent = `Agent setting failed: ${error.message}. Toggle again to retry; agent delivery remains paused.`;
    return false;
  }
}

function queueAgentReport(report) {
  state.pendingAgentStateReport = report;
  state.pendingAgentReportVersion++;
}

// agentDebug
export function agentDebug(label, obj) {
  const el = $('agentDebug'); if (!el) return;
  const stamp = new Date().toLocaleTimeString();
  const body = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  el.textContent += `\n\n[${stamp}] ${label}\n${body}`;
  el.scrollTop = el.scrollHeight;
}

// Priority helpers
export function priorityAtLeast(p, min) {
  return (priorityRank[p || 'normal'] ?? 1) >= (priorityRank[min || 'important'] ?? 2);
}

// Interrupt cooldown
export function agentInterruptPaddingSec() {
  return agentInterruptCooldownPaddingMs() / 1000;
}

export function agentInterruptAllowed() {
  return !state.agentInterruptInProgress && (!state.audioCtx || state.audioCtx.currentTime >= state.agentInterruptCooldownUntil);
}

export function setAgentInterruptCooldown() {
  if (!state.audioCtx) return;
  state.agentInterruptCooldownUntil = Math.max(state.agentInterruptCooldownUntil, state.currentTtsEstimatedEnd + agentInterruptPaddingSec());
  agentDebug('Agent interrupt cooldown set', {
    until_audio_time: state.agentInterruptCooldownUntil,
    current_audio_time: state.audioCtx.currentTime,
    padding_ms: agentInterruptCooldownPaddingMs()
  });
}

export function deferAgentInterrupt(message, ev) {
  state.deferredAgentInterrupt = { message, ev, createdAt: performance.now() };
  queueAgentReport(stripTranscriptLabels(ev?.report || ev?.message || message));
  agentDebug('Agent interrupt deferred by cooldown', {
    cooldown_until_audio_time: state.agentInterruptCooldownUntil,
    current_audio_time: state.audioCtx?.currentTime || 0,
    message, ev
  });
}

export function maybeReleaseDeferredAgentInterrupt() {
  if (!agentActive() || !state.deferredAgentInterrupt || !agentInterruptAllowed() || state.ttsSpeaking) return;
  const item = state.deferredAgentInterrupt;
  state.deferredAgentInterrupt = null;
  interruptConverse(item.message, false);
}

// Transcript slice + delivery
export function agentTranscriptSlice() {
  const max = parseInt($('agentMaxTurns')?.value || '16', 10) || 16;
  return cleanHistoryForModel().slice(-max);
}

export async function deliverTranscriptToAgent(reason = 'turn') {
  if (!agentActive() || deliveryInFlight) return false;
  const current = agentOwnership();
  const controller = beginAgentRequest();
  deliveryInFlight = controller;
  try {
    const statusResp = await fetch('/api/agent/status', { signal: controller.signal })
      .then(r => r.ok ? r.json() : { busy: false }).catch(() => ({ busy: false }));
    if (!current()) return false;
    const mode = statusResp.busy ? ($('agentBusyDeliveryMode')?.value || 'injection') : ($('agentIdleDeliveryMode')?.value || 'prompt');
    const payload = { transcript: agentTranscriptSlice(), delivery_mode: mode, reason, turn_count: state.agentTurnCount };
    agentDebug(`Transcript delivery (${mode})`, payload);
    const response = await fetch('/api/agent/transcript', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok || result?.ok === false || result?.accepted !== true) {
      throw new Error(apiErrorMessage(result, `Agent did not accept transcript (HTTP ${response.status}).`));
    }
    if (!current()) return false;
    state.agentLastDeliveredTurn = payload.turn_count;
    state.agentLastDeliveryAt = performance.now();
    state.agentFirstDelivered = true;
    return true;
  } catch (error) {
    if (!current()) return false;
    agentDebug('Transcript delivery failed; will retry', error.message || String(error));
    console.warn('agent transcript delivery failed', error);
    return false;
  } finally {
    ownedRequests.delete(controller);
    if (deliveryInFlight === controller) deliveryInFlight = null;
  }
}

export function maybeDeliverTranscriptToAgent(reason = 'timer') {
  if (!agentActive() || !state.history.length) return;
  const now = performance.now();
  const firstMode = $('agentFirstDeliveryMode')?.value || 'first_turn_or_timer';
  const firstSeconds = (parseInt($('agentFirstDeliverySeconds')?.value || '20', 10) || 20) * 1000;
  if (!state.agentFirstDelivered) {
    const hasTurn = state.agentTurnCount >= 1;
    const timerDue = now - state.agentStartedAt >= firstSeconds;
    if ((firstMode === 'first_turn' && hasTurn) || (firstMode === 'timer' && timerDue) || (firstMode === 'first_turn_or_timer' && (hasTurn || timerDue))) deliverTranscriptToAgent('first:' + reason);
    return;
  }
  const turnsDue = state.agentTurnCount - state.agentLastDeliveredTurn >= (parseInt($('agentPeriodicDeliveryTurns')?.value || '2', 10) || 2);
  const secondsDue = now - state.agentLastDeliveryAt >= ((parseInt($('agentPeriodicDeliverySeconds')?.value || '45', 10) || 45) * 1000);
  if (turnsDue || secondsDue) deliverTranscriptToAgent(reason);
}

// Event polling
function clearPreviousAgentSession() {
  // A server reset/restart invalidates reports and work accepted by that session.
  // Keep the conversation so the next delivery can establish the new agent context.
  state.agentSession++;
  state.pendingAgentReportVersion++;
  state.agentStateReport = '';
  state.pendingAgentStateReport = '';
  state.pendingImportantAgentMessage = '';
  state.awaitingAgentPermission = null;
  state.deferredAgentInterrupt = null;
  state.agentInterruptCooldownUntil = 0;
  state.agentFirstDelivered = false;
  state.agentLastDeliveredTurn = 0;
  state.agentLastDeliveryAt = 0;
  state.agentStartedAt = performance.now();
}

export async function pollAgentEvents() {
  if (!agentActive() || eventPollInFlight) return;
  const current = agentOwnership();
  const controller = beginAgentRequest();
  eventPollInFlight = controller;
  try {
    const response = await fetch(`/api/agent/events?after=${state.agentEventCursor}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`Agent events HTTP ${response.status}`);
    const result = await response.json();
    if (!current()) return;
    const cursor = result.last_event_id;
    const hasCursor = Number.isSafeInteger(cursor) && cursor >= 0;
    if (hasCursor && cursor < state.agentEventCursor) clearPreviousAgentSession();
    for (const event of (result.events || [])) {
      state.agentEventCursor = Math.max(state.agentEventCursor, event.id || 0);
      agentDebug(`Agent event ${event.type || ''} #${event.id || ''}`, event);
      handleAgentEvent(event);
    }
    if (hasCursor) state.agentEventCursor = cursor;
  } catch (error) {
    if (current()) console.warn('agent event poll failed', error);
  } finally {
    ownedRequests.delete(controller);
    if (eventPollInFlight === controller) eventPollInFlight = null;
  }
}

// Concise message helper
export function conciseAgentMessage(ev) {
  const msg = stripTranscriptLabels(ev.report || ev.message || '').replace(/\s+/g, ' ').trim();
  return msg.length > 520 ? msg.slice(0, 520) + '…' : msg;
}

// Event dispatcher
export function handleAgentEvent(ev) {
  if (!agentActive()) return;
  if (ev.type === 'agent_status') return;
  if (ev.type === 'agent_error') { queueAgentReport(`Korina Agent error: ${ev.message}`); return; }
  if (ev.type === 'permission_request') {
    if (($('agentPermissionInterrupts')?.value || 'on') === 'off') { queueAgentReport(conciseAgentMessage(ev)); return; }
    state.awaitingAgentPermission = ev;
    const msg = `Korina Agent needs your permission. ${conciseAgentMessage(ev)} Should I allow it?`;
    if (!agentInterruptAllowed()) { deferAgentInterrupt(msg, ev); return; }
    interruptConverse(msg, true);
    return;
  }
  if (ev.type === 'state_report') {
    state.agentStateReport = stripTranscriptLabels(ev.report || ev.message || '');
    if (!state.agentStateReport) return;
    const priority = ev.priority || 'normal';
    if (($('agentInterruptsEnabled')?.value || 'on') === 'on' && priorityAtLeast(priority, $('agentInterruptMinPriority')?.value || 'important')) {
      const hard = priorityAtLeast(priority, $('agentHardInterruptMinPriority')?.value || 'critical');
      const msg = `I just found something you should know about. ${conciseAgentMessage(ev)}`;
      if (!agentInterruptAllowed()) { deferAgentInterrupt(msg, ev); return; }
      interruptConverse(msg, hard);
    } else if (($('agentReportInjectionMode')?.value || 'next_reply') === 'next_reply') {
      queueAgentReport(state.agentStateReport);
      $('transcriptInfo').textContent = 'Korina Agent report stored for next reply.';
    }
  }
}

// Interrupt TTS and inject important message
async function speakAgentInterrupt(message, isCurrent = () => true, cooldown = true) {
  const current = agentOwnership(isCurrent);
  if (!current()) return;
  const session = state.agentSession;
  const speech = {};
  activeInterruptSpeech = speech;
  state.agentInterruptInProgress = true;
  try {
    const playing = speakText(message, true);
    speech.controller = state.controller;
    await playing;
    if (activeInterruptSpeech === speech && current() && cooldown) setAgentInterruptCooldown();
  } catch (error) {
    if (current()) throw error;
  } finally {
    if (activeInterruptSpeech === speech) {
      activeInterruptSpeech = null;
      if (session === state.agentSession) state.agentInterruptInProgress = false;
    }
  }
}

export function interruptConverse(text, hard = false) {
  if (!agentActive()) return;
  state.pendingImportantAgentMessage = text;
  state.suppressAckUntil = performance.now() + 12000;
  if (state.ackSource) { try { state.ackSource.stop(); } catch {} state.ackSource = null; state.ackPlayingUntil = 0; }
  if (hard && state.ttsSpeaking) stopTtsNow();
  if (!state.ttsSpeaking || hard) {
    const msg = state.pendingImportantAgentMessage; state.pendingImportantAgentMessage = '';
    addTranscriptEntry('Korina Agent Interrupt', msg, { includeInHistory: false });
    speakAgentInterrupt(msg).catch(e => console.warn('agent interrupt speech failed', e));
  }
}

// Permission answer detection
export function isPermissionAnswerPending() {
  return Boolean(agentActive() && permissionAnswerInFlight?.session === state.agentSession
    && !permissionAnswerInFlight.controller.signal.aborted);
}

export async function handlePermissionAnswerIfAny(message, { isCurrent = () => true } = {}) {
  const session = state.agentSession;
  const current = agentOwnership(isCurrent);
  if (!current()) return false;
  if (!state.awaitingAgentPermission) return false;
  const m = message.trim().toLowerCase();
  const yes = /^(yes|yeah|yep|approve|allow|go ahead|do it)\b/.test(m);
  const no = /^(no|nope|deny|stop|do not|don't)\b/.test(m);
  if (!yes && !no) return false;
  if (permissionAnswerInFlight?.session === session) return true;
  const answer = yes ? 'yes' : 'no';
  const req = state.awaitingAgentPermission;
  const controller = beginAgentRequest();
  const delivery = { session, request: req, controller };
  permissionAnswerInFlight = delivery;
  const turnCount = state.agentTurnCount + 1;
  const answerTurn = { role: 'user', content: `Permission answer to Korina Agent: ${answer}` };
  const maxTurns = Math.min(64, Math.max(1, parseInt($('agentMaxTurns')?.value || '16', 10) || 16));
  const transcript = [...cleanHistoryForModel(), answerTurn].slice(-maxTurns);
  status($('sttStatus'), 'Sending permission answer to Korina Agent…', 'warn');
  try {
    const response = await fetch('/api/agent/permission-answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: String(req.id ?? ''), answer, transcript }),
      signal: controller.signal,
    });
    if (!current()) return true;
    let result;
    try { result = await response.json(); }
    catch {
      throw new Error(response.ok ? 'Agent returned an invalid JSON confirmation' : `Agent permission HTTP ${response.status}`);
    }
    if (!current()) return true;
    if (!response.ok || result?.ok === false || result?.accepted !== true) {
      throw new Error(apiErrorMessage(result, result?.disabled ? 'Korina Agent is disabled' : 'Agent did not accept the permission answer'));
    }
  } catch (error) {
    if (!current()) return true;
    if (state.awaitingAgentPermission !== req) return true;
    const message = `Permission answer failed: ${error.message}. The request is still pending; try yes or no again.`;
    status($('sttStatus'), message, 'bad');
    throw new Error(message);
  } finally {
    ownedRequests.delete(controller);
    if (permissionAnswerInFlight === delivery) permissionAnswerInFlight = null;
  }
  if (!current()) return true;
  log('You', message);
  state.history.push(answerTurn);
  state.agentTurnCount++;
  // Both permission operations own their follow-up work. This successful
  // delivery must not trigger a second immediate transcript submission.
  state.agentLastDeliveredTurn = Math.max(state.agentLastDeliveredTurn, turnCount);
  state.agentLastDeliveryAt = performance.now();
  state.agentFirstDelivered = true;
  if (state.awaitingAgentPermission !== req) {
    status($('sttStatus'), state.awaitingAgentPermission
      ? 'Permission answer accepted; a newer permission request is still waiting.'
      : 'Korina Agent accepted the permission answer.', 'good');
    return true;
  }
  state.awaitingAgentPermission = null;
  status($('sttStatus'), 'Korina Agent accepted the permission answer.', 'good');
  const reply = answer === 'yes' ? 'Okay. I told Korina Agent it has permission.' : 'Okay. I told Korina Agent not to proceed.';
  $('text').value = reply; log('Korina', reply); state.history.push({ role: 'assistant', content: reply });
  await speakAgentInterrupt(reply, current, false);
  return true;
}

// ChatRequest.history contains prior turns only; the backend appends message.
// Explicit history lets callers record the visible current turn before awaiting I/O.
export async function askLM(message, priorHistory = cleanHistoryForModel(), { isCurrent = () => true, signal } = {}) {
  const session = state.agentSession;
  const controller = new AbortController();
  const current = () => session === state.agentSession && !controller.signal.aborted && !signal?.aborted && isCurrent();
  if (!current()) return null;
  const report = agentActive() ? state.pendingAgentStateReport : '';
  const activityEpoch = state.agentActivityEpoch;
  const reportVersion = state.pendingAgentReportVersion;
  const userMessage = stripTranscriptLabels(message);
  const userLength = [...userMessage].length;
  if (userLength > MAX_CHAT_MESSAGE_CHARS) throw new Error('Your message is too long. Split it into shorter turns.');
  const prefix = 'Injection: Korina Agent state report for this next reply:\n';
  const reportBudget = Math.max(0, MAX_CHAT_MESSAGE_CHARS - userLength - prefix.length - 2);
  const includedReport = [...stripTranscriptLabels(report)].slice(0, reportBudget).join('');
  const injection = includedReport ? `${prefix}${includedReport}\n\n` : '';
  status($('sttStatus'), 'Sending transcript to configured LLM…', 'warn');
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  chatRequests.add(controller);
  const usageOwner = { pending: true };
  responseUsageOwner = usageOwner;
  renderResponseUsage('Waiting for response…');
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: injection + userMessage,
        history: priorHistory.slice(-MAX_MODEL_HISTORY_TURNS),
        max_tokens: llmMaxTokens(),
        temperature: 0.7,
        model: lmModel(),
        reasoning: llmReasoningEnabled(),
        ...(llmProvider() === 'meta' ? { reasoning_effort: llmReasoningEffort() } : {}),
      }),
      signal: controller.signal,
    });
    if (!current()) return null;
    const j = await r.json();
    if (!current()) return null;
    if (includedReport && (!agentActive() || activityEpoch !== state.agentActivityEpoch)) {
      status($('sttStatus'), 'Agent context changed. Ask again for a fresh reply.', 'warn');
      return null;
    }
    if (!r.ok) throw new Error(apiErrorMessage(j, `HTTP ${r.status}`));
    if (typeof j?.reply !== 'string') throw new Error('The response LLM returned an invalid reply.');
    if (j.reply.trim() && includedReport && state.pendingAgentReportVersion === reportVersion && state.pendingAgentStateReport === report) {
      state.pendingAgentStateReport = '';
    }
    if (responseUsageOwner === usageOwner) {
      const observation = responseUsageObservation(j);
      const reported = observation?.usage && Object.values(observation.usage).some(value => value !== null);
      renderResponseUsage(reported ? 'Provider-reported usage received.' : 'Usage unavailable for this response.', observation);
      usageOwner.pending = false;
    }
    return j.reply;
  } catch (error) {
    if (!current()) return null;
    if (responseUsageOwner === usageOwner) {
      renderResponseUsage('Response failed; usage unavailable.');
      usageOwner.pending = false;
    }
    throw error;
  } finally {
    signal?.removeEventListener('abort', abort);
    chatRequests.delete(controller);
    if (responseUsageOwner === usageOwner && usageOwner.pending && session === state.agentSession) {
      renderResponseUsage('Response cancelled; usage unavailable.');
      usageOwner.pending = false;
    }
  }
}

// LM round-trip + speak + post-speak interrupt drain
export async function askAndSpeak(message, context = '', { isCurrent = () => true, signal } = {}) {
  const session = state.agentSession;
  const current = () => session === state.agentSession && !signal?.aborted && isCurrent();
  if (!current()) return null;
  if (await handlePermissionAnswerIfAny(message, { isCurrent: current })) return null;
  if (!current()) return null;
  log('You', message);
  const priorHistory = cleanHistoryForModel();
  state.history.push({ role: 'user', content: stripTranscriptLabels(message) });
  const lmMessage = context
    ? `${stripTranscriptLabels(context)}\n\nThe user now says: ${stripTranscriptLabels(message)}`
    : stripTranscriptLabels(message);

  const rawReply = await askLM(lmMessage, priorHistory, { isCurrent: current, signal });
  if (!current() || rawReply === null) return null;
  const reply = stripTranscriptLabels(rawReply);
  $('text').value = reply; log('Korina', reply);
  state.history.push({ role: 'assistant', content: reply });
  state.history = state.history.slice(-20);
  state.agentTurnCount++;
  status($('sttStatus'), `LLM replied ${reply ? 'ok' : 'empty'}; speaking…`, 'good');
  await speakText(reply, true);
  if (!current()) return null;
  if (agentActive() && state.pendingImportantAgentMessage && !state.ttsSpeaking) {
    const msg = state.pendingImportantAgentMessage; state.pendingImportantAgentMessage = '';
    addTranscriptEntry('Korina Agent Interrupt', msg, { includeInHistory: false });
    await speakAgentInterrupt(msg, current).catch(() => {});
    if (!current()) return null;
  }
  maybeDeliverTranscriptToAgent('turn');
  // Phase 5/visibility fix: let callers (live loop, manual transcript)
  // introspect what the LLM actually said so they can surface empty replies
  // instead of going silent after the ack plays.
  return reply;
}
