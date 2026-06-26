// js/agent-ui.js
//
// Frontend agent-driver UI: priority/interrupt helpers, transcript
// delivery to /api/agent/transcript, /api/agent/events poll loop,
// event handlers for state reports / permission requests / interrupts,
// and the LM round-trip — extracted verbatim from the original inline
// <script> in Korina/index.html (lines 819–933 plus agentDebug at
// line 433, at commit bdda3cc).
//
// Bare top-level globals (agentStateReport, agentLastTranscriptHash,
// pendingAgentStateReport, pendingImportantAgentMessage,
// awaitingAgentPermission, agentEventCursor, agentTurnCount,
// agentLastDeliveredTurn, agentFirstDelivered, agentStartedAt,
// agentLastDeliveryAt, suppressAckUntil, agentInterruptCooldownUntil,
// deferredAgentInterrupt, agentInterruptInProgress,
// currentTtsEstimatedEnd, audioCtx, ackSource, ackPlayingUntil,
// ttsSpeaking, history) have been migrated to the consolidated `state`
// object (see state.js). `priorityRank` is a top-level const used by
// the priority helpers; redeclared as a module-private constant since
// it's purely an internal lookup table and not externally meaningful.
// `EventBus` is imported from state.js (3.1.1, already merged).
// `stripTranscriptLabels`, `cleanHistoryForModel`, `addTranscriptEntry`
// are from history.js (3.2.5, already merged). `agentEnabled`,
// `lmModel`, `llmReasoningEnabled` are from settings-ui.js (3.2.4,
// already merged). `speakText`, `stopTtsNow` are from speech.js
// (3.2.8, already merged). `$`, `status`, `log` are from dom.js
// (3.2.1, already merged).
//
// Consumers import the named exports below.

import { state, EventBus } from "./state.js";
import { $, status, log } from "./dom.js";
import { stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry } from "./history.js";
import { agentEnabled, lmModel, llmReasoningEnabled } from "./settings-ui.js";
import { speakText, stopTtsNow } from "./speech.js";

// Priority lookup table (verbatim from index.html:819).
const priorityRank = { low: 0, normal: 1, important: 2, critical: 3 };

// --- agentDebug (verbatim from index.html:433) ---
export function agentDebug(label, obj) {
  const el = $('agentDebug'); if (!el) return;
  const stamp = new Date().toLocaleTimeString();
  const body = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  el.textContent += `\n\n[${stamp}] ${label}\n${body}`;
  el.scrollTop = el.scrollHeight;
}

// --- Priority helpers (verbatim from index.html:820) ---
export function priorityAtLeast(p, min) {
  return (priorityRank[p || 'normal'] ?? 1) >= (priorityRank[min || 'important'] ?? 2);
}

// --- Interrupt cooldown (verbatim from index.html:821–825) ---
export function agentInterruptPaddingSec() {
  return (parseInt($('agentInterruptCooldownPaddingMs')?.value || '3000', 10) || 3000) / 1000;
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
    padding_ms: parseInt($('agentInterruptCooldownPaddingMs')?.value || '3000', 10) || 3000
  });
}

export function deferAgentInterrupt(message, ev) {
  state.deferredAgentInterrupt = { message, ev, createdAt: performance.now() };
  state.pendingAgentStateReport = stripTranscriptLabels(ev?.report || ev?.message || message);
  agentDebug('Agent interrupt deferred by cooldown', {
    cooldown_until_audio_time: state.agentInterruptCooldownUntil,
    current_audio_time: state.audioCtx?.currentTime || 0,
    message, ev
  });
}

export function maybeReleaseDeferredAgentInterrupt() {
  if (!state.deferredAgentInterrupt || !agentInterruptAllowed() || state.ttsSpeaking) return;
  const item = state.deferredAgentInterrupt;
  state.deferredAgentInterrupt = null;
  interruptConverse(item.message, false);
}

// --- Transcript slice + delivery (verbatim from index.html:826–847) ---
export function agentTranscriptSlice() {
  const max = parseInt($('agentMaxTurns')?.value || '16', 10) || 16;
  return cleanHistoryForModel().slice(-max);
}

export async function deliverTranscriptToAgent(reason = 'turn') {
  if (agentEnabled() !== 'on') return;
  const statusResp = await fetch('/api/agent/status').then(r => r.json()).catch(() => ({ busy: false }));
  const mode = statusResp.busy ? ($('agentBusyDeliveryMode')?.value || 'injection') : ($('agentIdleDeliveryMode')?.value || 'prompt');
  const payload = { transcript: agentTranscriptSlice(), delivery_mode: mode, reason, turn_count: state.agentTurnCount };
  agentDebug(`Transcript delivery (${mode})`, payload);
  await fetch('/api/agent/transcript', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  }).catch(e => console.warn('agent transcript delivery failed', e));
  state.agentLastDeliveredTurn = state.agentTurnCount;
  state.agentLastDeliveryAt = performance.now();
  if (!state.agentFirstDelivered) state.agentFirstDelivered = true;
}

export function maybeDeliverTranscriptToAgent(reason = 'timer') {
  if (agentEnabled() !== 'on' || !state.history.length) return;
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

// --- Event polling (verbatim from index.html:853–858) ---
export async function pollAgentEvents() {
  if (agentEnabled() !== 'on') return;
  try {
    const j = await (await fetch(`/api/agent/events?after=${state.agentEventCursor}`)).json();
    for (const ev of (j.events || [])) {
      state.agentEventCursor = Math.max(state.agentEventCursor, ev.id || 0);
      agentDebug(`Agent event ${ev.type || ''} #${ev.id || ''}`, ev);
      handleAgentEvent(ev);
    }
  } catch (e) { console.warn('agent event poll failed', e); }
}

// --- Concise message helper (verbatim from index.html:860) ---
export function conciseAgentMessage(ev) {
  const msg = stripTranscriptLabels(ev.report || ev.message || '').replace(/\s+/g, ' ').trim();
  return msg.length > 520 ? msg.slice(0, 520) + '…' : msg;
}

// --- Event dispatcher (verbatim from index.html:861–886) ---
export function handleAgentEvent(ev) {
  if (ev.type === 'agent_status') return;
  if (ev.type === 'agent_error') { state.pendingAgentStateReport = `Korina Agent error: ${ev.message}`; return; }
  if (ev.type === 'permission_request') {
    if (($('agentPermissionInterrupts')?.value || 'on') === 'off') { state.pendingAgentStateReport = conciseAgentMessage(ev); return; }
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
      state.pendingAgentStateReport = state.agentStateReport;
      $('transcriptInfo').textContent = 'Korina Agent report stored for next reply.';
    }
  }
}

// --- Interrupt TTS and inject important message (verbatim from index.html:887–898) ---
export function interruptConverse(text, hard = false) {
  state.pendingImportantAgentMessage = text;
  state.suppressAckUntil = performance.now() + 12000;
  if (state.ackSource) { try { state.ackSource.stop(); } catch {} state.ackSource = null; state.ackPlayingUntil = 0; }
  if (hard && state.ttsSpeaking) stopTtsNow();
  if (!state.ttsSpeaking || hard) {
    const msg = state.pendingImportantAgentMessage; state.pendingImportantAgentMessage = '';
    state.agentInterruptInProgress = true;
    addTranscriptEntry('Korina Agent Interrupt', msg, { includeInHistory: false });
    speakText(msg, true).then(() => setAgentInterruptCooldown()).catch(e => console.warn('agent interrupt speech failed', e)).finally(() => { state.agentInterruptInProgress = false; });
  }
}

// --- Permission answer detection (verbatim from index.html:899–912) ---
export async function handlePermissionAnswerIfAny(message) {
  if (!state.awaitingAgentPermission) return false;
  const m = message.trim().toLowerCase();
  const yes = /^(yes|yeah|yep|approve|allow|go ahead|do it)\b/.test(m);
  const no = /^(no|nope|deny|stop|do not|don't)\b/.test(m);
  if (!yes && !no) return false;
  const answer = yes ? 'yes' : 'no';
  const req = state.awaitingAgentPermission; state.awaitingAgentPermission = null;
  state.history.push({ role: 'user', content: `Permission answer to Korina Agent: ${answer}` });
  await fetch('/api/agent/permission-answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: String(req.id || ''), answer, transcript: agentTranscriptSlice() })
  }).catch(e => console.warn('permission answer failed', e));
  const reply = answer === 'yes' ? 'Okay. I told Korina Agent it has permission.' : 'Okay. I told Korina Agent not to proceed.';
  $('text').value = reply; log('Korina', reply); state.history.push({ role: 'assistant', content: reply });
  state.agentTurnCount++;
  await speakText(reply, true);
  maybeDeliverTranscriptToAgent('permission-answer');
  return true;
}

// --- LLM call (verbatim from index.html:913–922) ---
export async function askLM(message) {
  let injection = '';
  if (state.pendingAgentStateReport) {
    injection = `Injection: Korina Agent state report for this next reply:\n${state.pendingAgentStateReport}\n\n`;
    state.pendingAgentStateReport = '';
  }
  status($('sttStatus'), 'Sending transcript to configured LLM…', 'warn');
  const r = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: stripTranscriptLabels(injection + message),
      history: cleanHistoryForModel(),
      max_tokens: 180,
      temperature: 0.7,
      model: lmModel(),
      reasoning: llmReasoningEnabled()
    })
  });
  const j = await r.json(); if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
  return j.reply || '';
}

// --- LM round-trip + speak + post-speak interrupt drain (verbatim from index.html:923–933) ---
export async function askAndSpeak(message, context = '') {
  log('You', message);
  if (await handlePermissionAnswerIfAny(message)) return;
  state.history.push({ role: 'user', content: stripTranscriptLabels(message) });
  // /steer is the kept LLM-prompt contract (renamed variable above; the literal
  // sent to the LLM is /steer for stability with the existing prompt convention).
  const systemExtra = window._pendingInjection ? "\n/steer " + window._pendingInjection : "";
  window._pendingInjection = "";
  const lmMessage = context
    ? `${stripTranscriptLabels(context)}${systemExtra}\n\nThe user now says: ${stripTranscriptLabels(message)}`
    : stripTranscriptLabels(message);
  EventBus.emit("ConversationTurnComplete", { role: "user", text: message, ts: Date.now() });

  const reply = stripTranscriptLabels(await askLM(lmMessage));
  let displayReply = reply;
  if (window._pendingSentenceInterrupt) { displayReply = (window._pendingSentenceInterrupt.message || "") + " " + reply; window._pendingSentenceInterrupt = null; }
  $('text').value = displayReply; log('Korina', displayReply);
  state.history.push({ role: 'assistant', content: displayReply });
  state.history = state.history.slice(-20);
  state.agentTurnCount++;
  EventBus.lastConverseActivity = Date.now();
  status($('sttStatus'), `LLM replied ${reply ? 'ok' : 'empty'}; speaking…`, 'good');
  EventBus.lastConverseActivity = Date.now();
  await speakText(reply, true);
  if (state.pendingImportantAgentMessage && !state.ttsSpeaking) {
    const msg = state.pendingImportantAgentMessage; state.pendingImportantAgentMessage = '';
    addTranscriptEntry('Korina Agent Interrupt', msg, { includeInHistory: false });
    state.agentInterruptInProgress = true;
    await speakText(msg, true).catch(() => {});
    setAgentInterruptCooldown();
    state.agentInterruptInProgress = false;
  }
  maybeDeliverTranscriptToAgent('turn');
  // Phase 5/visibility fix: let callers (live loop, manual transcript)
  // introspect what the LLM actually said so they can surface empty replies
  // instead of going silent after the ack plays.
  return displayReply;
}
