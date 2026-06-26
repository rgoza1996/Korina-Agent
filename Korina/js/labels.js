// js/labels.js
export function prettyModelLabel(modelId) {
  const text = String(modelId || "").trim();
  if (!text) return "";
  if (text.includes("/") || text.endsWith(".gguf")) {
    const parts = text.split("/").filter(Boolean);
    const tail = parts[parts.length - 1] || text;
    const parent = parts.length > 1 ? parts[parts.length - 2] : "";
    return parent ? `${tail} — ${parent}` : tail;
  }
  return text;
}


// ttsProviderLabel — human-readable name for a TTS provider value.
// Used by the debug strip's catch-block so we don't say 'Kokoro offline' when the
// user actually has 'openai-compatible' or 'custom' selected.
export function ttsProviderLabel(p) {
  const v = String(p || '').trim();
  if (v === 'kokoro') return 'Kokoro';
  if (v === 'openai-compatible') return 'OpenAI TTS';
  if (v === 'custom') return 'Custom TTS';
  return v || 'TTS';
}

// llmProviderLabel — human-readable name for an LLM provider value.
// Used by the debug strip's hostInfo so the user can see which backend is wired.
export function llmProviderLabel(p) {
  const v = String(p || '').trim();
  if (v === 'llama.cpp') return 'llama.cpp';
  if (v === 'lmstudio') return 'LM Studio';
  if (v === 'ollama') return 'Ollama';
  if (v === 'openai-compatible') return 'OpenAI-compat';
  return v || 'LLM';
}
