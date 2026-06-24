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
