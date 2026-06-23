# Phase 3 — Frontend Modularization

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Each task = one commit on `beta`. All work is behavior-preserving; no new runtime dependencies, no new globals (the global names move into a `state` object but the names are preserved).

**Goal:** turn the inline `<script>` block in `Korina/index.html` into discrete ES modules under `Korina/js/` and move the inline `<style>` block to `Korina/styles.css`. Live conversation behavior is unchanged.

**Architecture:** native ES modules, no build step (Option A from blueprint §3.1). One entrypoint (`Korina/js/app.js`) loaded with `<script type="module" src="./js/app.js">`. Existing global names (`live`, `ttsSpeaking`, `history`, `vadNoiseFloor`, etc.) are collected into a single exported `state` object that modules import. The blueprint's per-file layout is followed (16 modules). Per-module commits; each commit leaves the page working (the page reloads as before; module resolution is browser-native).

**Tech Stack:** vanilla JS (ES modules), no build, no bundler, no package.json.

**Target branch:** `beta` (per `docs/refactor/PROGRESS.md` HEAD `1f21a67`; refactor series lives on `beta`).

---

## Verified preconditions (2026-06-23)

- `beta` HEAD: `1f21a67` (docs-only; latest code-touching commit is `d3d6932`).
- Phase 0, 1, 2: complete. Backend modularization done in Phase 1; provider capabilities contract in Phase 2.
- `Korina/index.html`: **1,305 lines total**.
  - Lines 1-13: `<head>` (lines 7-12 = inline `<style>` block, ~4 wrapped lines of CSS).
  - Lines 14-174: `<body>` markup with all the `id=` attributes the JS references.
  - Lines 175-1303: a single `<script>` block with **~1,129 lines of JS** containing **~130 named function/let/const declarations** at module top-level.
- Boot sequence at line 743-755: `async function initApp()` followed by `initApp();` (line 755).
- Bottom-of-script event wiring: `$('clearSessionBtn').onclick=...` (line 771), `$('liveBtn').onclick=...` (line 1302).
- One `<script>` tag, no `type="module"`. Conversion target: `<script type="module" src="./js/app.js">`.
- Existing globals to preserve (these become properties of `state`, but external callers see no change because `app.js` re-exports the names on `window` for any inline `onclick` handlers):
  - `mode`, `controller`, `audioCtx`, `gain`, `nextPlayTime` (TTS/playback state, line 313)
  - `activeSources`, `ttsSpeaking`, `currentKorinaText`, `currentTtsStartedAt`, `currentTtsEstimatedEnd`, `interruptContext` (line 314)
  - `mediaRecorder`, `recChunks`, `analyser`, `raf`, `stream`, `meterAudioCtx` (line 315)
  - `bargeRecorder`, `bargeChunks`, `bargeLoopId`, `bargeSpeechStart`, `bargeSilenceStart`, `handlingBarge` (line 316)
  - `live`, `liveBusy`, `liveSilenceStart`, `liveSpeechStart`, `liveLastVoice`, `liveLoopId`, `history` (line 317)
  - `vadNoiseFloor`, `vadNoiseSamples`, `vadIdleNoiseSamples`, `vadCalibratingUntil`, `vadLastIdleRecalibrationAt`, `vadSpeechFrames`, `vadSilenceFrames`, `vadSpeechThreshold`, `vadSilenceThreshold` (line 318)
  - `partialTimer`, `partialInFlight`, `partialSeq`, `partialController`, `latestPartialText`, `latestPartialAt` (line 319)
  - `partialRecorder`, `partialChunks`, `partialWindowMs`, `partialWindowIndex`, `partialQueue`, `partialQueueProcessing` (line 320)
  - `ackFiles`, `ackBuffers`, `ackBuffersByTag`, `ackSource`, `ackPlayingUntil`, `lastActivityAt`, `idleAckCount`, `saveConfigTimer` (line 321)
  - `appConfig`, `agentStateReport`, `agentLastTranscriptHash`, `pendingAgentStateReport`, `pendingImportantAgentMessage`, `awaitingAgentPermission` (lines 322-323)
  - `agentEventCursor`, `agentTurnCount`, `agentLastDeliveredTurn`, `agentFirstDelivered`, `agentStartedAt`, `agentLastDeliveryAt`, `suppressAckUntil`, `agentInterruptCooldownUntil`, `deferredAgentInterrupt`, `agentInterruptInProgress` (line 324)
  - `endSilenceMs` (line 326)
  - `EventBus` const at line 179
  - `lastModelsQuery`, `lastModelsLoadedAt`, `lastModelsPayload` (lines 630-632; model-list cache)
  - `_capabilitiesCache` (line 359; the Phase 2 capabilities cache)
- DOM helper `const $=id=>document.getElementById(id);` at line 153.
- 16 modules planned (matches blueprint §3.2 layout exactly): `app, state, dom, labels, api, settings-ui, providers-ui, acks, vad, recorder, partial-queue, live, barge-in, speech, history, agent-ui`.

---

## Design decisions baked into this plan (override at any task)

| Decision | Choice | Override cost |
|---|---|---|
| Module strategy | Option A — native ES modules, no build (blueprint §3.1 default; user confirmed 2026-06-23) | high — would require a Vite/esbuild migration of every subsequent phase |
| Global migration | Globals move into `state` object exported from `js/state.js`; `app.js` re-exports the old names on `window` so HTML `onclick=` attributes still work | low — pure rename, no behavior change |
| CSS strategy | Inline `<style>` (lines 7-12, ~4 wrapped lines) → `Korina/styles.css`, linked via `<link rel="stylesheet" href="./styles.css">` | low |
| Module entrypoint | `Korina/js/app.js` loads with `<script type="module" src="./js/app.js">`; `app.js` runs `initApp()` on import | low |
| Per-module commits | One commit per extracted module (16 commits across 3.2); prelude 3.1 and styles 3.3 are their own commits | n/a |
| Model-list fetch on open | Step 3.4 in its own commit; gate on regression passing through 3.3 first | low |
| Verification | Phase 3.5: regression 38/38 + live conversation (start, speak, get reply, interrupt with barge-in, play ack, idle ack cadence, clear session) | n/a |
| Push pattern | commit per task to `beta` only; do **not** touch `alpha` or `master` | n/a |
| TypeScript / JSX / TSX | not added; stay vanilla JS | high — would invalidate the whole plan |
| Backward-compat shim | `window.live`, `window.ttsSpeaking`, etc. remain set by `app.js` after module load, so any future `onclick=` HTML attribute still works | low |

---

## Phase 3 overview

| # | Step | LOC touched | Risk |
|---|---|---|---|
| 3.1 | Add module skeleton (`js/app.js` entrypoint, `js/state.js` globals object) | ~50 | low |
| 3.2 | Extract modules — 14 files, one commit per module | ~1100 | medium (UI state plumbing) |
| 3.3 | Extract stylesheet (`Korina/styles.css`) | ~10 | low |
| 3.4 | Stop re-querying model list on focus | ~30 | low |
| 3.5 | Phase 3 verification: regression + live conversation | n/a | n/a |

Each step ends with a working tree, a green smoke run, and a commit on `beta`.

---

## Step 3.1 — Module skeleton (`app.js` + `state.js`)

**Goal:** establish the module loading pattern and the global state object. Page still works after this commit — `initApp()` is called from `app.js`, and the inline script in `index.html` is replaced by a single `<script type="module" src="./js/app.js">` line.

**Files:**
- Create: `Korina/js/app.js`
- Create: `Korina/js/state.js`
- Modify: `Korina/index.html` (replace `<script>` opening tag, remove the entire inline script, add the module entrypoint)

### Task 3.1.1 — Create `js/state.js`

**Files:** Create `Korina/js/state.js`

**Step 1:** Create the file with the consolidated state object. This file holds the live module-level globals as properties of a single exported `state` object. Properties preserve their existing default values exactly so no behavior changes.

```js
// js/state.js
//
// Single source of truth for module-level state previously held in
// inline top-level `let`/`const` declarations in index.html.
//
// Modules import { state } from './state.js' and read/write properties.
// The shape and default values mirror the original globals 1:1.

export const state = {
  // --- TTS / playback ---
  mode: 'sse',
  controller: null,
  audioCtx: null,
  gain: null,
  nextPlayTime: 0,
  activeSources: [],
  ttsSpeaking: false,
  currentKorinaText: '',
  currentTtsStartedAt: 0,
  currentTtsEstimatedEnd: 0,
  interruptContext: '',

  // --- main recorder / meter ---
  mediaRecorder: null,
  recChunks: [],
  analyser: null,
  raf: null,
  stream: null,
  meterAudioCtx: null,

  // --- barge-in recorder ---
  bargeRecorder: null,
  bargeChunks: [],
  bargeLoopId: null,
  bargeSpeechStart: 0,
  bargeSilenceStart: 0,
  handlingBarge: false,

  // --- live conversation ---
  live: false,
  liveBusy: false,
  liveSilenceStart: 0,
  liveSpeechStart: 0,
  liveLastVoice: 0,
  liveLoopId: null,
  history: [],

  // --- adaptive VAD ---
  vadNoiseFloor: 0.012,
  vadNoiseSamples: [],
  vadIdleNoiseSamples: [],
  vadCalibratingUntil: 0,
  vadLastIdleRecalibrationAt: 0,
  vadSpeechFrames: 0,
  vadSilenceFrames: 0,
  vadSpeechThreshold: 0.035,
  vadSilenceThreshold: 0.022,

  // --- partial transcription ---
  partialTimer: null,
  partialInFlight: false,
  partialSeq: 0,
  partialController: null,
  latestPartialText: '',
  latestPartialAt: 0,
  partialRecorder: null,
  partialChunks: [],
  partialWindowMs: 1800,
  partialWindowIndex: 0,
  partialQueue: [],
  partialQueueProcessing: false,

  // --- ack playback ---
  ackFiles: [],
  ackBuffers: [],
  ackBuffersByTag: {},
  ackSource: null,
  ackPlayingUntil: 0,
  lastActivityAt: performance.now(),
  idleAckCount: 0,
  saveConfigTimer: null,

  // --- app config + agent state ---
  appConfig: { idle_ack_initial_ms: 5000, idle_ack_step_ms: 5000 },
  agentStateReport: '',
  agentLastTranscriptHash: '',
  pendingAgentStateReport: '',
  pendingImportantAgentMessage: '',
  awaitingAgentPermission: null,

  agentEventCursor: 0,
  agentTurnCount: 0,
  agentLastDeliveredTurn: 0,
  agentFirstDelivered: false,
  agentStartedAt: performance.now(),
  agentLastDeliveryAt: 0,
  suppressAckUntil: 0,
  agentInterruptCooldownUntil: 0,
  deferredAgentInterrupt: null,
  agentInterruptInProgress: false,

  endSilenceMs: 3200,

  // --- model-list cache ---
  lastModelsQuery: '',
  lastModelsLoadedAt: 0,
  lastModelsPayload: null,

  // --- Phase 2 capabilities cache ---
  _capabilitiesCache: null,
};

export const EventBus = {
  firstDeliveryTimer: null,
  // ... copy the existing EventBus literal (Korina/index.html:179) here verbatim.
};
```

> **Important:** copy the existing `EventBus` literal from `Korina/index.html:179-310` exactly as-is into `state.js`. The plan does not reproduce it here to avoid drift if it has been edited since the plan was written. Verify with `git show HEAD:Korina/index.html | sed -n '179,310p'` before pasting.

**Step 2:** Verify the file parses:
```bash
node --check Korina/js/state.js   # expect: no output, exit 0
```

**Step 3:** Commit:
```bash
git add Korina/js/state.js
git commit -m "refactor(frontend): add state.js with consolidated globals (3.1.1)"
```

### Task 3.1.2 — Create `js/app.js` entrypoint (stub)

**Files:** Create `Korina/js/app.js`

**Step 1:** Create the file with a stub that just imports `state` and logs to the console — confirms module loading works without touching any behavior.

```js
// js/app.js
//
// Frontend entrypoint. Loaded by index.html via <script type="module">.
// Imports the consolidated state object and any modules that wire
// themselves on load. By the end of Phase 3, this file imports every
// other module and calls initApp().

import { state } from './state.js';

console.log('[korina] app.js loaded; state keys:', Object.keys(state).length);
```

**Step 2:** Verify the file parses:
```bash
node --check Korina/js/app.js
```

**Step 3:** Commit:
```bash
git add Korina/js/app.js
git commit -m "refactor(frontend): add app.js entrypoint stub (3.1.2)"
```

### Task 3.1.3 — Replace the inline `<script>` with the module entrypoint

**Files:** Modify `Korina/index.html`

> ⚠️ **This task temporarily breaks the page** — the inline script is removed but no module yet implements its behavior. The page will load, but no init runs and no event handlers fire. Verify before/after with a curl check, and revert if anything goes wrong before committing.

**Step 1:** Make a backup first.
```bash
cp Korina/index.html /tmp/index.html.bak
```

**Step 2:** Remove the inline `<script>` block (lines 175-1303 of the current file). Replace the opening `<script>` tag at line 175 and the closing `</script>` tag at line 1303 with a single module entrypoint.

Before:
```html
<script>
... 1,129 lines ...
</script>
```

After:
```html
<script type="module" src="./js/app.js"></script>
```

Use a precise edit so the surrounding `</body>` markup is preserved. The simplest mechanical edit: replace `^<script>$` at line 175 with `<script type="module" src="./js/app.js"></script>`, and delete the matching `^</script>$` (and everything between).

**Step 3:** Verify file size dropped:
```bash
wc -l Korina/index.html   # expect: ~175 lines (was 1305)
```

**Step 4:** Verify the page still loads (HTTP 200, no JS errors in console when manually opened in browser):
```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8123/Korina/index.html   # expect: 200
curl -sS http://127.0.0.1:8123/Korina/js/app.js | head -3   # expect: the console.log line
curl -sS http://127.0.0.1:8123/Korina/js/state.js | head -3   # expect: comment + export
```

**Step 5:** Confirm live regression still passes:
```bash
python tests/regression_smoke.py   # expect: 38/38 PASS
```

> The regression script tests backend routes, not frontend JS execution, so it should still pass — but the live conversation UI is now non-functional until 3.2 modules land. **Do not run a live conversation test** until 3.1.4 completes.

**Step 6:** Commit:
```bash
git add Korina/index.html
git commit -m "refactor(frontend): replace inline script with ES module entrypoint (3.1.3)"
```

### Task 3.1.4 — Move `initApp()` to `app.js` and verify the page boots

**Files:** Modify `Korina/js/app.js`

**Step 1:** Read the current `initApp()` body from git history:
```bash
git show HEAD~3:Korina/index.html | sed -n '/^async function initApp/,/^}/p'
```

**Step 2:** Move the `initApp()` function (and its `initApp();` call) into `app.js`. Update `app.js` to import nothing extra and define `initApp` locally. The `initApp()` function depends on many globals (loadConfig, loadAgentModelOptions, loadAcks, health, markActivity, maybeDeliverTranscriptToAgent, pollAgentEvents, maybeReleaseDeferredAgentInterrupt) — these will be imported in 3.2 tasks. For now, move the function verbatim and the call will fail in the browser until those imports land.

Wait — to keep the page working, do **not** call `initApp()` from `app.js` yet. Leave it as a stub that logs "waiting for module extraction".

```js
// js/app.js
import { state } from './state.js';

// initApp() and module wiring land in subsequent 3.2 tasks.
// Until then, this stub keeps the page loadable and logs that
// extraction is in progress.

console.log('[korina] app.js loaded; state keys:', Object.keys(state).length);
console.log('[korina] Phase 3 extraction in progress; UI not yet wired.');
```

**Step 3:** Verify:
```bash
curl -sS http://127.0.0.1:8123/Korina/js/app.js | head -5
```

**Step 4:** Commit:
```bash
git add Korina/js/app.js
git commit -m "refactor(frontend): stub app.js during module extraction (3.1.4)"
```

---

## Step 3.2 — Extract modules

**Goal:** one module per concern. Each task moves a contiguous block of code from the original inline script (preserved in git history at `HEAD~3:Korina/index.html`) into the named module. The page boots and runs at the end of 3.2.

**Files (all under `Korina/js/`):**
- `dom.js`, `labels.js`, `api.js`, `settings-ui.js`, `providers-ui.js`, `acks.js`, `vad.js`, `recorder.js`, `partial-queue.js`, `live.js`, `barge-in.js`, `speech.js`, `history.js`, `agent-ui.js`

**Convention for every module:**
1. Add `import { state } from './state.js';` at the top.
2. Move the relevant functions verbatim — no behavior changes.
3. Replace bare references to the old globals (`live`, `ttsSpeaking`, `history`, etc.) with `state.live`, `state.ttsSpeaking`, `state.history`, etc.
4. Do **not** export the functions unless something else needs to import them. Use top-level `function` declarations inside the module — they're module-scoped, not global.
5. The DOM helper `const $=id=>document.getElementById(id);` lives in `dom.js` and is imported by modules that need it.

### Task 3.2.1 — Extract `dom.js`

**Files:** Create `Korina/js/dom.js`

```js
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
```

> Functions moved from `Korina/index.html:153` (`$`), `329` (`status`), `412` (`setDot`), `413` (`log`), `606` (`setSectionHidden`), `962` (`clamp`).

**Step:** Move, commit:
```bash
git add Korina/js/dom.js
git commit -m "refactor(frontend): extract dom.js (3.2.1)"
```

### Task 3.2.2 — Extract `labels.js`

**Files:** Create `Korina/js/labels.js`

```js
// js/labels.js
export function prettyModelLabel(modelId) {
  const text = String(modelId || '').trim();
  if (!text) return '';
  if (text.includes('/') || text.endsWith('.gguf')) {
    const parts = text.split('/').filter(Boolean);
    const tail = parts[parts.length - 1] || text;
    const parent = parts.length > 1 ? parts[parts.length - 2] : '';
    return parent ? `${tail} — ${parent}` : tail;
  }
  return text;
}
```

> Function moved from `Korina/index.html:392`.

**Step:** Commit:
```bash
git add Korina/js/labels.js
git commit -m "refactor(frontend): extract labels.js (3.2.2)"
```

### Task 3.2.3 — Extract `api.js`

**Files:** Create `Korina/js/api.js`

Move from `Korina/index.html`:
- Line 411: `activateSelectedProvider`
- Line 583: `loadConfig`
- Lines 587-591: `saveConfigNow`, `saveConfigSoon`
- Line 624: `health`
- Lines 743-755: `initApp`

`api.js` imports `applyConfig` from `./settings-ui.js` (which lands in 3.2.4). Use a forward-reference comment to document the dependency:

```js
// js/api.js
import { state } from './state.js';
import { $ } from './dom.js';
import { applyConfig } from './settings-ui.js';   // lands in 3.2.4
import { loadAgentModelOptions } from './providers-ui.js';   // lands in 3.2.6

export async function activateSelectedProvider(provider, model = '') {
  const r = await fetch('/api/llm/provider/activate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, model: model || '' }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || JSON.stringify(j));
  if (j.saved) applyConfig(j.saved);
  return j;
}

export async function loadConfig() {
  // ... verbatim from index.html:583-586
}

export function saveConfigNow() {
  // ... verbatim from index.html:587-590
}

export function saveConfigSoon() {
  clearTimeout(state.saveConfigTimer);
  state.saveConfigTimer = setTimeout(saveConfigNow, 250);
}

export async function health() {
  // ... verbatim from index.html:624-628
}

export async function initApp() {
  await loadConfig();
  await loadAgentModelOptions();
  await loadConfig();
  await (await import('./acks.js')).loadAcks('global');
  health();
  (await import('./live.js')).markActivity('init');
  setInterval(() => (await import('./agent-ui.js')).maybeDeliverTranscriptToAgent('timer'), 3000);
  setInterval(() => (await import('./agent-ui.js')).pollAgentEvents(), 2000);
  setInterval(() => (await import('./agent-ui.js')).maybeReleaseDeferredAgentInterrupt(), 500);
}
```

> Note on `initApp()`: the dynamic `import()` calls keep the file loadable before all its dependencies land. Replace with static imports once all 3.2 tasks are done (Task 3.2.15 — cleanup).

**Step:** Commit:
```bash
git add Korina/js/api.js
git commit -m "refactor(frontend): extract api.js (3.2.3)"
```

### Task 3.2.4 — Extract `settings-ui.js`

**Files:** Create `Korina/js/settings-ui.js`

Move from `Korina/index.html`:
- Lines 435-505: `collectConfig`
- Lines 506-582: `applyConfig`
- Lines 587-591: `markActivity` (state-mutating), `recordIdleAck`, `nextIdleDelayMs`
- Lines 607-612: `syncReasoningHints`
- Lines 613-623: `syncConverseSettingsUI`

Plus the local config getters at lines 311-354 (`ttsPort`, `ttsBaseUrl`, `sttDevice`, `ttsDevice`, `ttsProvider`, `ttsModel`, `llmProvider`, `llmBaseUrl`, `sttModel`, `sttBackend`, `lmModel`, `sttLlmProvider`, `sttLlmBaseUrl`, `sttLlmApiKeyEnv`, `sttLlmModel`, `llmReasoningEnabled`, `sttLlmReasoningEnabled`, `effectiveSttLlmProvider`, `effectiveSttLlmBaseUrl`, `effectiveSttLlmApiKeyEnv`, `effectiveSttLlmModel`, `minSpeechMs`, `partialWindowMsSetting`, `agentEnabled`, `agentModel`, `finalSttMode`, `endpointMode`).

```js
// js/settings-ui.js
import { state } from './state.js';
import { $ } from './dom.js';

export function ttsPort() { return parseInt($('ttsPort')?.value || '8880', 10) || 8880; }
export function ttsBaseUrl() { /* ... */ }
// ... rest verbatim, replacing `$('id')` patterns unchanged

export function collectConfig() { /* ... */ }
export function applyConfig(c = {}) { /* ... */ }
export function markActivity(reason = 'activity') { /* ... */ }
export function recordIdleAck() { /* ... */ }
export function nextIdleDelayMs() { /* ... */ }
export function syncReasoningHints() { /* ... */ }
export function syncConverseSettingsUI() { /* ... */ }
```

**Step:** Commit:
```bash
git add Korina/js/settings-ui.js
git commit -m "refactor(frontend): extract settings-ui.js (3.2.4)"
```

### Task 3.2.5 — Extract `history.js`

**Files:** Create `Korina/js/history.js`

Move from `Korina/index.html`:
- Line 414: `stripTranscriptLabels`
- Lines 426-431: `cleanHistoryForModel`
- Line 432: `addTranscriptEntry`
- Line 818: `transcriptHash`

```js
// js/history.js
import { state } from './state.js';
import { log } from './dom.js';

export function stripTranscriptLabels(text) { /* ... */ }
export function cleanHistoryForModel() { /* ... */ }
export function addTranscriptEntry(role, text, { includeInHistory = false } = {}) { /* ... */ }
export function transcriptHash(turns) { /* ... */ }
```

**Step:** Commit:
```bash
git add Korina/js/history.js
git commit -m "refactor(frontend): extract history.js (3.2.5)"
```

### Task 3.2.6 — Extract `providers-ui.js`

**Files:** Create `Korina/js/providers-ui.js`

Move from `Korina/index.html`:
- Lines 359-391: `_capabilitiesCache` (move into `state`), `loadCapabilities`, `getResponseLlmProviderCaps`, `getAgentProviderCaps`, `providerPresetBaseUrl`, `maybeApplyProviderPreset`
- Lines 393-410: `setBaseUrlEditability`
- Lines 630-696: `lastModelsQuery`, `lastModelsLoadedAt`, `lastModelsPayload`, `loadModelOptions`, `loadAgentModelOptions`

The model-list cache globals (`lastModelsQuery`, etc.) are already in `state` from Task 3.1.1 — just reference `state.lastModelsQuery` etc.

```js
// js/providers-ui.js
import { state } from './state.js';
import { $ } from './dom.js';

export async function loadCapabilities(force = false) { /* ... */ }
export function getResponseLlmProviderCaps(provider) { /* ... */ }
export function getAgentProviderCaps(provider) { /* ... */ }
export function providerPresetBaseUrl(provider) { /* ... */ }
export function maybeApplyProviderPreset(...) { /* ... */ }
export async function setBaseUrlEditability() { /* ... */ }
export async function loadModelOptions(force = false) { /* ... */ }
export async function loadAgentModelOptions() { /* ... */ }
```

**Step:** Commit:
```bash
git add Korina/js/providers-ui.js
git commit -m "refactor(frontend): extract providers-ui.js (3.2.6)"
```

### Task 3.2.7 — Extract `acks.js`

**Files:** Create `Korina/js/acks.js`

Move from `Korina/index.html`:
- Lines 697-742: `loadAcks`
- Lines 776-803: `preloadAckBuffers`, `waitForScheduledAudio`
- Lines 934-959: `playRandomAck`

**Step:** Commit:
```bash
git add Korina/js/acks.js
git commit -m "refactor(frontend): extract acks.js (3.2.7)"
```

### Task 3.2.8 — Extract `speech.js`

**Files:** Create `Korina/js/speech.js`

Move from `Korina/index.html`:
- Line 775: `ensureAudio`
- Lines 795-797: `waitForScheduledAudio` (move here if not in acks; pick one — recommend `acks.js`)
- Line 796: `stopTtsNow`
- Line 804: `interruptedSpeechContext`
- Line 814: `schedulePcm`
- Lines 815-817: `speakSSE`, `speakBuffered`, `speakText`

**Step:** Commit:
```bash
git add Korina/js/speech.js
git commit -m "refactor(frontend): extract speech.js (3.2.8)"
```

### Task 3.2.9 — Extract `vad.js`

**Files:** Create `Korina/js/vad.js`

Move from `Korina/index.html`:
- Line 960: `drawWave`
- Line 961: `rmsLevel`
- Line 962: `clamp` (move to `dom.js` instead — already done in 3.2.1)
- Lines 963-973: `resetAdaptiveVad`
- Lines 974-1003: `updateAdaptiveVad`

```js
// js/vad.js
import { state } from './state.js';
import { $ } from './dom.js';

export function drawWave() { /* ... */ }
export function rmsLevel() { /* ... */ }
export function resetAdaptiveVad() { /* ... */ }
export function updateAdaptiveVad(level, now) { /* ... */ }
```

**Step:** Commit:
```bash
git add Korina/js/vad.js
git commit -m "refactor(frontend): extract vad.js (3.2.9)"
```

### Task 3.2.10 — Extract `recorder.js`

**Files:** Create `Korina/js/recorder.js`

Move from `Korina/index.html`:
- Line 1004: `setupMic`
- Line 1005: `makeRecorder`
- Lines 1006-1045: `transcribeBlob`
- Lines 1046-1066: `transcribePartialBlob`

**Step:** Commit:
```bash
git add Korina/js/recorder.js
git commit -m "refactor(frontend): extract recorder.js (3.2.10)"
```

### Task 3.2.11 — Extract `partial-queue.js`

**Files:** Create `Korina/js/partial-queue.js`

Move from `Korina/index.html`:
- Lines 1067-1073: `startPartialTranscriptionLoop`
- Lines 1074-1101: `startPartialWindowRecorder`
- Lines 1102-1118: `processPartialQueue`
- Lines 1119-1123: `waitForPartialQueueIdle`
- Lines 1124-1136: `stopPartialTranscriptionLoop`

**Step:** Commit:
```bash
git add Korina/js/partial-queue.js
git commit -m "refactor(frontend): extract partial-queue.js (3.2.11)"
```

### Task 3.2.12 — Extract `barge-in.js`

**Files:** Create `Korina/js/barge-in.js`

Move from `Korina/index.html`:
- Lines 1137-1149: `startBargeInCapture`
- Lines 1150-1168: `monitorBargeIn`
- Lines 1169-1182: `monitorBargeSpeechEnd`
- Lines 1183-1219: `handleBargeRecording`

**Step:** Commit:
```bash
git add Korina/js/barge-in.js
git commit -m "refactor(frontend): extract barge-in.js (3.2.12)"
```

### Task 3.2.13 — Extract `live.js`

**Files:** Create `Korina/js/live.js`

Move from `Korina/index.html`:
- Line 1220: `startLive`
- Line 1221: `stopLive`
- Line 1222: `startLiveRecorder`
- Line 1223-1276: `liveMonitor`
- Lines 1277-1302: `handleLiveTurn`

**Step:** Commit:
```bash
git add Korina/js/live.js
git commit -m "refactor(frontend): extract live.js (3.2.13)"
```

### Task 3.2.14 — Extract `agent-ui.js`

**Files:** Create `Korina/js/agent-ui.js`

Move from `Korina/index.html`:
- Lines 819-833: `priorityRank`, `priorityAtLeast`, `agentInterruptPaddingSec`, `agentInterruptAllowed`, `setAgentInterruptCooldown`, `deferAgentInterrupt`, `maybeReleaseDeferredAgentInterrupt`, `agentTranscriptSlice`, `deliverTranscriptToAgent`, `maybeDeliverTranscriptToAgent`
- Lines 853-859: `pollAgentEvents`
- Line 860: `conciseAgentMessage`
- Lines 861-886: `handleAgentEvent`
- Lines 887-898: `interruptConverse`
- Lines 899-912: `handlePermissionAnswerIfAny`
- Lines 913-922: `askLM`
- Lines 923-933: `askAndSpeak`
- Line 433: `agentDebug`

**Step:** Commit:
```bash
git add Korina/js/agent-ui.js
git commit -m "refactor(frontend): extract agent-ui.js (3.2.14)"
```

### Task 3.2.15 — Wire `app.js` to import everything and call `initApp()`

**Files:** Modify `Korina/js/app.js`

**Step 1:** Replace `app.js` content with imports of every module and the `initApp()` call. Use `window.X = X` re-exports so any future HTML `onclick=` attribute continues to find the names.

```js
// js/app.js
//
// Frontend entrypoint. Imports every module and runs initApp().
// Re-exports the most-used globals on window so HTML onclick handlers
// (e.g. <button onclick="startLive()">) continue to work after
// extraction.

import { state, EventBus } from './state.js';
import { $, status, setDot, log, setSectionHidden, clamp } from './dom.js';
import { prettyModelLabel } from './labels.js';
import {
  loadConfig, saveConfigNow, saveConfigSoon, health,
  activateSelectedProvider, initApp,
} from './api.js';
import {
  collectConfig, applyConfig, markActivity, syncConverseSettingsUI,
  // ... all exports from settings-ui.js
} from './settings-ui.js';
import {
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  setBaseUrlEditability, maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
} from './providers-ui.js';
import { loadAcks, preloadAckBuffers, playRandomAck } from './acks.js';
import { drawWave, rmsLevel, resetAdaptiveVad, updateAdaptiveVad } from './vad.js';
import { setupMic, makeRecorder, transcribeBlob, transcribePartialBlob } from './recorder.js';
import {
  startPartialTranscriptionLoop, stopPartialTranscriptionLoop,
  processPartialQueue, waitForPartialQueueIdle,
} from './partial-queue.js';
import {
  ensureAudio, stopTtsNow, schedulePcm, speakSSE, speakBuffered, speakText,
} from './speech.js';
import {
  startBargeInCapture, monitorBargeIn, handleBargeRecording,
} from './barge-in.js';
import {
  startLive, stopLive, liveMonitor, handleLiveTurn, startLiveRecorder,
} from './live.js';
import {
  stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry,
} from './history.js';
import {
  deliverTranscriptToAgent, pollAgentEvents, handleAgentEvent,
  interruptConverse, askAndSpeak, askLM, agentDebug,
} from './agent-ui.js';

// Re-export the most common globals on window so HTML inline event
// handlers and DevTools debugging keep working.
Object.assign(window, {
  state, EventBus, $, status, setDot, log, setSectionHidden, clamp,
  prettyModelLabel,
  loadConfig, saveConfigNow, saveConfigSoon, health, activateSelectedProvider,
  collectConfig, applyConfig, markActivity, syncConverseSettingsUI,
  loadCapabilities, getResponseLlmProviderCaps, getAgentProviderCaps,
  setBaseUrlEditability, maybeApplyProviderPreset,
  loadModelOptions, loadAgentModelOptions,
  loadAcks, preloadAckBuffers, playRandomAck,
  drawWave, rmsLevel, resetAdaptiveVad, updateAdaptiveVad,
  setupMic, makeRecorder, transcribeBlob, transcribePartialBlob,
  startPartialTranscriptionLoop, stopPartialTranscriptionLoop,
  processPartialQueue, waitForPartialQueueIdle,
  ensureAudio, stopTtsNow, schedulePcm, speakSSE, speakBuffered, speakText,
  startBargeInCapture, monitorBargeIn, handleBargeRecording,
  startLive, stopLive, liveMonitor, handleLiveTurn, startLiveRecorder,
  stripTranscriptLabels, cleanHistoryForModel, addTranscriptEntry,
  deliverTranscriptToAgent, pollAgentEvents, handleAgentEvent,
  interruptConverse, askAndSpeak, askLM, agentDebug,
});

// Now boot.
await loadCapabilities().then(() => setBaseUrlEditability());
initApp();
```

**Step 2:** Verify everything parses:
```bash
for f in Korina/js/*.js; do node --check "$f" || echo "FAIL: $f"; done
```

**Step 3:** Verify the page now boots and the live conversation works:
- Open http://127.0.0.1:8123/Korina/ in a browser
- DevTools console: confirm no module load errors
- Click "Live Conversation" — speak, get a reply, interrupt with barge-in, observe ack playback
- Click "Clear Session" — confirm log/transcript reset

**Step 4:** Run regression:
```bash
python tests/regression_smoke.py   # expect: 38/38 PASS
```

**Step 5:** Commit:
```bash
git add Korina/js/app.js
git commit -m "refactor(frontend): wire app.js to all modules and call initApp (3.2.15)"
```

---

## Step 3.3 — Extract stylesheet to `Korina/styles.css`

**Goal:** move the inline `<style>` block (lines 7-12 of `Korina/index.html`, ~4 wrapped lines of CSS) to `Korina/styles.css` and link it.

### Task 3.3.1 — Move CSS to external stylesheet

**Files:**
- Create: `Korina/styles.css`
- Modify: `Korina/index.html` (replace `<style>...</style>` with `<link>`)

**Step 1:** Read the current inline CSS:
```bash
sed -n '7,12p' Korina/index.html
```

**Step 2:** Create `Korina/styles.css` with that content verbatim.

**Step 3:** In `Korina/index.html`, replace the entire `<style>...</style>` block (lines 7-12) with:
```html
<link rel="stylesheet" href="./styles.css">
```

**Step 4:** Verify:
```bash
curl -sS http://127.0.0.1:8123/Korina/styles.css | head -3   # expect: CSS content
curl -sS http://127.0.0.1:8123/Korina/index.html | grep -c 'styles.css'   # expect: 1
```

**Step 5:** Visually confirm the page renders identically (no styling changes) in a browser.

**Step 6:** Commit:
```bash
git add Korina/styles.css Korina/index.html
git commit -m "refactor(frontend): extract styles.css from inline <style> (3.3)"
```

---

## Step 3.4 — Stop re-querying model list on focus

**Goal:** the model dropdown currently reloads models on every focus/pointerdown, gated only by a 4-second TTL. Move to fetch-on-open-only.

### Task 3.4.1 — Replace focus-triggered reload with click-triggered fetch

**Files:** Modify `Korina/js/providers-ui.js` (where `loadModelOptions` lives per 3.2.6)

**Step 1:** Find the focus/pointerdown handler in `Korina/index.html` original (pre-3.2 inline script):
```bash
git show HEAD~16:Korina/index.html | grep -nE "focus|pointerdown|loadModelOptions"
```

**Step 2:** Replace the focus/pointerdown handler with a `click`/`mousedown` handler that calls `loadModelOptions()` exactly once per dropdown open. Remove the 4-second TTL hack.

**Step 3:** Manual test in browser:
- Open settings modal, navigate to provider/model tab
- Open the model dropdown repeatedly — confirm models are fetched once and cached after the first open
- Force-refresh (clear cache) and confirm the next open fetches again

**Step 4:** Run regression:
```bash
python tests/regression_smoke.py   # expect: 38/38 PASS
```

**Step 5:** Commit:
```bash
git add Korina/js/providers-ui.js
git commit -m "refactor(frontend): fetch model list on dropdown open only (3.4)"
```

---

## Step 3.5 — Phase 3 verification

**Goal:** confirm the modularized frontend preserves every live-conversation behavior end-to-end.

### Task 3.5.1 — Live regression

**Step 1:** Run the regression suite:
```bash
python tests/regression_smoke.py
```
**Expected:** 38/38 PASS (unchanged from Phase 2 close-out).

### Task 3.5.2 — Live conversation test

In a browser at `http://127.0.0.1:8123/Korina/`, run through:

- [ ] Click "Live Conversation" — mic arms, ack phrases preload, status shows "armed"
- [ ] Speak — partial transcription chunks appear in the transcript box
- [ ] Wait for endpoint — full turn is transcribed, LM replies, TTS plays through speakers
- [ ] Interrupt with barge-in — TTS stops mid-utterance, mic captures your speech, conversation flow resumes
- [ ] Idle ack cadence — after the configured idle delay, an ack plays (e.g. "Still here.")
- [ ] Click "Clear Session" — log, transcript, and agent debug reset
- [ ] Settings modal — every tab opens, every dropdown populates, every toggle reflects saved state

### Task 3.5.3 — Diff sanity check

```bash
# Confirm only js/, styles.css, and index.html changed in Phase 3.
git diff --stat $(git rev-list --max-parents=0 HEAD)..HEAD -- '*.py' '*.md' 'korina/' 'tests/'
# Expected: zero changes to backend code or tests (Phase 3 is frontend-only).

# Confirm Phase 3 commit count.
git log --oneline e828647..HEAD | wc -l
# Expected: ~19 commits (3.1: 4, 3.2: 15, 3.3: 1, 3.4: 1 = 21; subtract 1 if no async loadConfig tweak).

# Confirm the index.html is now small (just the markup + module tag + link).
wc -l Korina/index.html
# Expected: ~20 lines.
```

### Task 3.5.4 — Update PROGRESS.md and commit

Mark Phase 3 complete in `docs/refactor/PROGRESS.md`:

```markdown
## Phase 3 — Frontend modularization

- [✓] 3.1 module strategy — Option A (native ES modules, no build) confirmed.
- [✓] 3.2 extract modules — 15 modules extracted under `Korina/js/`, one commit per module.
- [✓] 3.3 extract styles — `Korina/styles.css` linked from `index.html`.
- [✓] 3.4 model fetch on open — focus-triggered reload replaced with open-only fetch, TTL hack removed.
- [✓] 3.5 verify — regression 38/38; live conversation test passed.
```

Commit:
```bash
git add docs/refactor/PROGRESS.md
git commit -m "docs: mark Phase 3 complete in PROGRESS.md (3.5.4)"
git push origin beta
```

---

## Execution handoff

Plan complete. **~21 commits on `beta`**, all behavior-preserving.

**Branch:** `beta` (matches live state and Phase 1/2 pattern; do not touch `alpha` or `master`).

**Execution approach:** dispatch a fresh subagent per task via the `subagent-driven-development` skill, with two-stage review (spec compliance, then code quality). Each task is a single commit on `beta`. The page should remain loadable at every step (module stubs import only what's been extracted so far; the dynamic `import()` calls in `api.js:initApp` keep the file loadable before all dependencies land).

**Two questions to confirm before I execute:**

1. Branch — plan says `beta`. Confirm `beta` (matches Phase 1/2)? Or do you want Phase 3 on a separate branch like `frontend-modularization`?
2. Commit cadence — execute all ~21 tasks in one subagent-driven batch, or pause after each step (3.1, 3.2, 3.3, 3.4, 3.5) so you can review incremental progress?