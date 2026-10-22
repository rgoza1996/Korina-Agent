// Sanitize conversation history and transcript labels before model or agent delivery.

import { state } from "./state.js";
import { log } from "./dom.js";

// Transcript label stripping

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

// History sanitization for model input
//

export function cleanHistoryForModel(){
  return state.history
    .filter(m=>!(m.role==="assistant" && /^\s*\[\s*(Ack Phrase|Korina Agent Interrupt)\s*\]/i.test(m.content||"")))
    .map(m=>({role:m.role,content:stripTranscriptLabels(m.content)}))
    .filter(m=>m.content);
}

// Transcript entry recording
//

export function addTranscriptEntry(role,text,{includeInHistory=false}={}){
  if(includeInHistory) state.history.push({role:"assistant",content:`[${role}] ${text}`});
  log(role,text);
}

// Transcript fingerprint for agent dedup

export function transcriptHash(turns){
  return JSON.stringify(turns.map(m=>[m.role,m.content])).slice(-4000);
}
