// js/app.js
//
// Frontend entrypoint. Loaded by index.html via <script type="module">.
// Imports the consolidated state object and any modules that wire
// themselves on load. By the end of Phase 3, this file imports every
// other module and calls initApp().

import { state } from './state.js';

console.log('[korina] app.js loaded; state keys:', Object.keys(state).length);
