// Channel discovery and explicit selection share one rendering generation.
import { $ } from './dom.js';
import { responseError } from './response-body.js';

let generation = 0;
let switching = false;

async function channelJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function populateChannelTab() {
  const select = $('channelSelect'), hint = $('channelSelectHint');
  if (!select) {
    if (hint) hint.textContent = 'Channel control missing from the served page. Refresh and verify the frontend deployment.';
    return;
  }
  // The pending selection owns these controls until its explicit save settles.
  if (switching) return;
  const current = ++generation;
  select.disabled = true;
  if (hint) hint.textContent = 'Loading registered channels…';
  try {
    const [list, selected] = await Promise.all([
      channelJson('/api/converse/channels'), channelJson('/api/converse/channel'),
    ]);
    if (current !== generation) return;
    const channels = list?.channels, active = selected?.channel;
    if (!Array.isArray(channels) || channels.some(name => typeof name !== 'string' || !name) ||
        (active != null && (typeof active !== 'string' || !channels.includes(active)))) {
      throw new Error('Invalid channel discovery response');
    }
    select.innerHTML = '';
    for (const name of [...new Set(channels)]) {
      const option = document.createElement('option');
      option.value = name; option.textContent = name;
      select.appendChild(option);
    }
    if (!channels.length) {
      const option = document.createElement('option');
      option.value = ''; option.textContent = '-'; select.appendChild(option);
      if (hint) hint.textContent = 'No channels registered.';
      return;
    }
    select.value = active || channels[0];
    select.disabled = false;
    if (hint) hint.textContent = active
      ? `Active: ${active}. Selection is saved for the next restart.`
      : `Default: ${channels[0]}. Switch the active channel by selecting another.`;
  } catch (error) {
    if (current !== generation) return;
    if (hint) hint.textContent = `Failed to load channels: ${error.message}. Click the Channel tab to retry.`;
  }
}

export async function saveChannelSelection() {
  const select = $('channelSelect'), hint = $('channelSelectHint');
  const selected = select?.value;
  if (!selected || switching) return;
  switching = true;
  ++generation; // A discovery begun before this selection cannot restore it.
  select.disabled = true;
  if (hint) hint.textContent = `Switching to: ${selected}…`;
  let saved = false;
  try {
    const result = await channelJson(`/api/converse/channel/${encodeURIComponent(selected)}`, { method: 'POST' });
    if (result?.ok !== true || result.channel !== selected) throw new Error('Channel selection was not confirmed');
    saved = true;
  } catch (error) {
    if (hint) hint.textContent = `Switch failed: ${error.message}. Click the Channel tab to reload and retry.`;
  } finally {
    switching = false;
    select.disabled = false;
  }
  if (saved) await populateChannelTab();
}
