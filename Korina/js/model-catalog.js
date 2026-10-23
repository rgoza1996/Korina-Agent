// Discovery limits also protect browsers connected to an older Korina server.
export const MAX_MODEL_IDS = 512;
export const MAX_MODEL_ID_BYTES = 256 * 1024;
export const MAX_MODEL_RESPONSE_BYTES = 8 * 1024 * 1024;
const encoder = new TextEncoder();

export function normalizeModelId(value) {
  if (typeof value !== 'string') return null;
  value = value.trim();
  if (!value || [...value].length > 512 || /[\uD800-\uDFFF]/u.test(value)) return null;
  return value;
}

export function boundedModelIds(values, pinned = [], maxIds = MAX_MODEL_IDS, maxBytes = MAX_MODEL_ID_BYTES) {
  const pins = new Set(pinned.map(normalizeModelId).filter(Boolean));
  const seen = new Set(), ids = [], sizes = new Map();
  let bytes = 0, invalid = 0, duplicates = 0, limited = 0;
  for (const raw of Array.isArray(values) ? values : []) {
    const id = normalizeModelId(raw);
    if (!id) { invalid++; continue; }
    if (seen.has(id)) { duplicates++; continue; }
    seen.add(id);
    const size = encoder.encode(JSON.stringify(id)).length;
    if (ids.length >= maxIds || bytes + size > maxBytes) {
      if (!pins.has(id)) { limited++; continue; }
      while (ids.length && (ids.length >= maxIds || bytes + size > maxBytes)) {
        const index = ids.findLastIndex(value => !pins.has(value));
        if (index < 0) break;
        const [removed] = ids.splice(index, 1); bytes -= sizes.get(removed); sizes.delete(removed); limited++;
      }
      if (ids.length >= maxIds || bytes + size > maxBytes) { limited++; continue; }
    }
    ids.push(id); sizes.set(id, size); bytes += size;
  }
  return { ids, info: { returned: ids.length, identifier_bytes: bytes, skipped_invalid: invalid,
    skipped_duplicates: duplicates, skipped_limit: limited, truncated: limited > 0,
    max_models: maxIds, max_identifier_bytes: maxBytes } };
}

const text = (value, length = 1024) => typeof value === 'string' ? value.slice(0, length) : '';
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function validateMetadata(payload) {
  for (const key of ['llm_models_capabilities', 'stt_llm_models_capabilities', 'llm_models_status', 'stt_llm_models_status', 'labels']) {
    if (!(key in payload)) continue;
    if (!record(payload[key])) throw new Error(`Invalid model metadata map: ${key}`);
    for (const value of Object.values(payload[key])) {
      if (key === 'labels') {
        if (typeof value !== 'string') throw new Error('Invalid model label');
        continue;
      }
      if (!record(value)) throw new Error(`Invalid model metadata value: ${key}`);
      for (const field of ['supports_audio_input', 'has_mmproj', 'mmproj_estimate_incomplete', 'usable']) {
        if (field in value && typeof value[field] !== 'boolean') throw new Error(`Invalid model metadata field: ${field}`);
      }
      for (const field of ['source', 'summary']) {
        if (field in value && typeof value[field] !== 'string') throw new Error(`Invalid model metadata field: ${field}`);
      }
      if ('reasons' in value && (!Array.isArray(value.reasons) || value.reasons.some(reason => typeof reason !== 'string'))) {
        throw new Error('Invalid model metadata reasons');
      }
      if ('approx_vram_gb' in value && value.approx_vram_gb !== null && !Number.isFinite(value.approx_vram_gb)) {
        throw new Error('Invalid model metadata VRAM estimate');
      }
    }
  }
  if ('catalog_info' in payload && (!record(payload.catalog_info) || Object.values(payload.catalog_info).some(value => !record(value)))) {
    throw new Error('Invalid model catalog information');
  }
}

const info = value => {
  const result = {};
  for (const key of ['returned', 'identifier_bytes', 'skipped_invalid', 'skipped_duplicates', 'skipped_limit',
    'max_models', 'max_identifier_bytes', 'visited_entries', 'max_entries', 'directories_scanned']) {
    if (Number.isFinite(value?.[key])) result[key] = Math.max(0, Math.min(1e9, Math.floor(value[key])));
  }
  if (value?.truncated) result.truncated = true;
  if (value?.incomplete) result.incomplete = true;
  if (Array.isArray(value?.errors)) result.errors = value.errors.slice(0, 8).map(value => text(value, 256));
  return result;
};

export function boundedModelPayload(payload, { response = '', stt = '' } = {}) {
  if (!payload || !Array.isArray(payload.llm_models) || !Array.isArray(payload.stt_llm_models)) {
    throw new Error('invalid model list response');
  }
  validateMetadata(payload);
  const result = {}, ownInfo = {};
  const pins = [response, stt, payload.llm_default, payload.stt_llm_default];
  const arrays = ['llm_models', 'stt_llm_models', 'llama_cpp_local_models', 'lmstudio_catalog_models'];
  for (const key of arrays) {
    if (!(key in payload)) continue;
    const bounded = boundedModelIds(payload[key], pins);
    result[key] = bounded.ids; ownInfo[key] = bounded.info;
  }
  const aggregate = boundedModelIds(arrays.flatMap(key => result[key] || []), pins, 1024, 2 * MAX_MODEL_ID_BYTES);
  const allowed = new Set(aggregate.ids);
  for (const key of arrays) if (result[key]) result[key] = result[key].filter(id => allowed.has(id));
  if (Array.isArray(payload.whisper_models)) result.whisper_models = boundedModelIds(payload.whisper_models).ids;
  for (const key of ['llm_default', 'stt_llm_default']) if (key in payload) result[key] = normalizeModelId(payload[key]) || '';
  for (const key of ['llm_base_url', 'stt_llm_base_url']) if (key in payload) result[key] = text(payload[key], 8192);
  for (const key of ['llm_error', 'stt_llm_error']) if (key in payload) result[key] = payload[key] ? text(payload[key]) : null;
  for (const key of ['llm_models_capabilities', 'stt_llm_models_capabilities', 'llm_models_status', 'stt_llm_models_status', 'labels']) {
    if (!(key in payload)) continue;
    result[key] = Object.fromEntries(aggregate.ids.filter(id => Object.hasOwn(payload[key] || {}, id)).map(id => {
      const value = payload[key][id];
      if (key === 'labels') return [id, text(value)];
      if (key.endsWith('_status')) return [id, { ...('usable' in value ? { usable: value.usable } : {}), summary: text(value.summary),
        reasons: Array.isArray(value?.reasons) ? value.reasons.slice(0, 8).map(value => text(value)) : [] }];
      const capability = {};
      for (const field of ['supports_audio_input', 'has_mmproj', 'mmproj_estimate_incomplete']) if (field in value) capability[field] = value[field];
      if ('source' in (value || {})) capability.source = text(value.source, 64);
      if ('approx_vram_gb' in (value || {})) capability.approx_vram_gb = Number.isFinite(value.approx_vram_gb) ? value.approx_vram_gb : null;
      return [id, capability];
    }));
  }
  const catalogInfo = {};
  for (const key of ['response', 'stt', 'local_gguf', 'lmstudio', 'aggregate', 'metadata']) {
    if (payload.catalog_info?.[key]) catalogInfo[key] = info(payload.catalog_info[key]);
  }
  for (const [key, value] of Object.entries({ ...ownInfo, browser_aggregate: aggregate.info })) {
    if (value.truncated || value.skipped_invalid) catalogInfo[key] = value;
  }
  if (Object.keys(catalogInfo).length) result.catalog_info = catalogInfo;
  return result;
}

export function catalogNotice(catalogInfo) {
  const details = Object.entries(catalogInfo || {}).filter(([key]) => key !== 'metadata').map(([, value]) => value);
  const parts = [];
  if (details.some(value => value?.truncated)) parts.push('Catalog limited; additional models omitted (512 per picker).');
  if (details.some(value => value?.skipped_invalid > 0)) parts.push('Invalid model IDs omitted.');
  if (details.some(value => value?.errors?.length)) parts.push('Some local catalog entries could not be read.');
  if (catalogInfo?.metadata?.incomplete) parts.push('Some mmproj capability estimates are incomplete (scan limit or filesystem error).');
  return parts.join(' ');
}
