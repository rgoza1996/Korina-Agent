// js/history.js
//
// Frontend conversation-history helpers — transcript label stripping,
// history sanitization for model input, transcript entry recording,
// and transcript fingerprinting for agent dedup — extracted verbatim
// from the original inline <script> in Korina/index.html (lines 414,
// 426–431, 432, 818 at commit bdda3cc). The bare top-level `history`
// global from the inline script has been migrated to the consolidated
// `state.history` array (see state.js). `addTranscriptEntry` continues
// to call `log()` for DOM append, which now lives in dom.js.
//
// Consumers import the named exports below.

import { state } from "./state.js";
import { log } from "./dom.js";

// --- Transcript label stripping (verbatim from index.html:414) ---

export function stripTranscriptLabels(text){
  return String(text||"")
    .replace(/<think>[\s\S]*?<\/think>/gi,"")
    .replace(/<think>[\s\S]*/gi,"")
    .replace(/\[\s*Ack Phrase\s*\]\s*[^.?!]*(?:[.?!]\s*)?/gi,"")
    .replace(/\bAck Phrase\b\s*/gi,"")
    .replace(/\[\s*Korina Agent Interrupt\s*\]\s*[^\n]*/gi,"")
    .replace(/\bKorina Agent Interrupt\s*:\s*[^\n]*/gi,"")
    .replace(/^\s*Priority\s*:\s*(low|normal|important|critical)\s*$/gim,"")
    .replace(/\s{2,}/g," ")
    .trim();
}

// --- History sanitization for model input (verbatim from index.html:426-431) ---
//
// Bare global `history` -> `state.history`.

export function cleanHistoryForModel(){
  return state.history
    .filter(m=>!(m.role==="assistant" && /^\s*\[\s*(Ack Phrase|Korina Agent Interrupt)\s*\]/i.test(m.content||"")))
    .map(m=>({role:m.role,content:stripTranscriptLabels(m.content)}))
    .filter(m=>m.content);
}

// --- Transcript entry recording (verbatim from index.html:432) ---
//
// Bare global `history` -> `state.history`.

export function addTranscriptEntry(role,text,{includeInHistory=false}={}){
  if(includeInHistory) state.history.push({role:"assistant",content:`[${role}] ${text}`});
  log(role,text);
}

// --- Transcript fingerprint for agent dedup (verbatim from index.html:818) ---

export function transcriptHash(turns){
  return JSON.stringify(turns.map(m=>[m.role,m.content])).slice(-4000);
}
