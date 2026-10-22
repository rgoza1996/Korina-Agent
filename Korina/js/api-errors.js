// Format known API diagnostics without dumping request inputs or arbitrary data.

export const MAX_API_ERROR_CHARS = 2048;

function text(value, limit = MAX_API_ERROR_CHARS) {
  if (typeof value !== 'string') return '';
  const trimmed = value.trim();
  if (trimmed.length <= limit) return trimmed;
  let shortened = trimmed.slice(0, limit - 1);
  if (/[\uD800-\uDBFF]$/.test(shortened)) shortened = shortened.slice(0, -1);
  return shortened + '…';
}

function validationMessage(error) {
  const message = text(error?.msg, 512);
  if (!message) return '';
  const location = Array.isArray(error?.loc) ? error.loc.slice(0, 8) : [];
  if (['body', 'query', 'path', 'header', 'cookie'].includes(location[0])) location.shift();
  const field = location
    .filter(part => typeof part === 'string' || Number.isSafeInteger(part) && part >= 0)
    .map(part => typeof part === 'string' ? text(part, 64) : String(part))
    .filter(Boolean).join('.');
  return field ? `${field}: ${message}` : message;
}

function diagnostic(value) {
  if (typeof value === 'string') return text(value);
  if (Array.isArray(value)) return text(value.slice(0, 3).map(validationMessage).filter(Boolean).join('; '));
  if (value && typeof value === 'object') {
    for (const key of ['reason', 'message', 'msg', 'error']) {
      const message = text(value[key]);
      if (message) return message;
    }
  }
  return '';
}

export function apiErrorMessage(payload, fallback = 'Request failed') {
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    return diagnostic(payload.detail) || diagnostic(payload.error) || diagnostic(payload) || text(fallback);
  }
  return diagnostic(payload) || text(fallback);
}
