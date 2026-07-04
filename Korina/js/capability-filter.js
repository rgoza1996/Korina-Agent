// js/capability-filter.js
//
// Pure helpers for filtering model lists by capability. No DOM, no fetch.
// Imported by providers-ui.js to gate the multimodal STT dropdown.

import { state } from './state.js';

/**
 * Return the subset of model ids that match the capability requirement.
 *
 * @param {string[]} modelIds      -- all available model ids
 * @param {Object<string,Object>} capabilitiesDict  -- {modelId: {supports_audio_input: bool, ...}}
 * @param {Object} requirement
 *   { kind: "any" }                  -- no filter
 *   { kind: "audio_input" }          -- requires supports_audio_input === true
 * @returns {string[]} filtered subset
 */
export function filterModelsByCapability(modelIds, capabilitiesDict, requirement) {
  if (!Array.isArray(modelIds)) return [];
  if (!requirement || requirement.kind === 'any') return modelIds.slice();
  if (requirement.kind === 'audio_input') {
    return modelIds.filter((mid) => {
      const cap = capabilitiesDict && capabilitiesDict[mid];
      return cap && cap.supports_audio_input === true;
    });
  }
  // Unknown requirement kind: fail safe (no filter).
  return modelIds.slice();
}

/**
 * Toggle the "All models" override. Persisted in state.capabilityFilterOverride
 * for the session lifetime (no backend change).
 *
 * @param {boolean} enabled  -- true = show all models regardless of capability
 */
export function setCapabilityFilterOverride(enabled) {
  state.capabilityFilterOverride = !!enabled;
}

/**
 * Return the requirement object to pass to filterModelsByCapability for the
 * multimodal STT dropdown, honoring the user's "All models" override.
 */
export function sttLlmModelRequirement() {
  if (state.capabilityFilterOverride) return { kind: 'any' };
  return { kind: 'audio_input' };
}