// js/dom.js
import { state } from './state.js';

export const $ = (id) => document.getElementById(id);

export function status(el, msg, cls = '') {
  el.textContent = msg;
  el.className = 'status ' + cls;
}

export function setDot(id, state_) {
  $(id).className = 'dot ' + state_;
}

export function log(role, text) {
  const p = document.createElement('p');
  p.className = 'msg';
  p.innerHTML = `<b>${role}:</b> ${String(text).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]))}`;
  $('log').appendChild(p);
  $('log').scrollTop = $('log').scrollHeight;
}

export function setSectionHidden(id, hidden) {
  const el = $(id);
  if (el) el.classList.toggle('settingsHidden', !!hidden);
}

export function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}
