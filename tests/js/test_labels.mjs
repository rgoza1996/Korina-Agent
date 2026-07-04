
// Unit tests for labels.js provider-name helpers.
// Run with: node tests/js/test_labels.mjs
import { ttsProviderLabel, llmProviderLabel, prettyModelLabel } from '../../Korina/js/labels.js';

let failed = 0;
function eq(label, got, want) {
  if (got !== want) { console.error(`FAIL ${label}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); failed++; }
  else console.log(`ok   ${label}`);
}

eq('tts kokoro',          ttsProviderLabel('kokoro'), 'Kokoro');
eq('tts openai-compat',   ttsProviderLabel('openai-compatible'), 'OpenAI TTS');
eq('tts custom',          ttsProviderLabel('custom'), 'Custom TTS');
eq('tts empty',           ttsProviderLabel(''), 'TTS');
eq('tts null',            ttsProviderLabel(null), 'TTS');
eq('tts unknown',         ttsProviderLabel('something-else'), 'something-else');

eq('llm llama.cpp',       llmProviderLabel('llama.cpp'), 'llama.cpp');
eq('llm lmstudio',        llmProviderLabel('lmstudio'), 'LM Studio');
eq('llm ollama',          llmProviderLabel('ollama'), 'Ollama');
eq('llm openai-compat',   llmProviderLabel('openai-compatible'), 'OpenAI-compat');
eq('llm empty',           llmProviderLabel(''), 'LLM');
eq('llm null',            llmProviderLabel(null), 'LLM');
eq('llm unknown',         llmProviderLabel('something-else'), 'something-else');

eq('pretty passthrough',  prettyModelLabel('gpt-4o'), 'gpt-4o');
eq('pretty gguf path',    prettyModelLabel('a/b/foo.gguf'), 'foo.gguf — b');
eq('pretty gguf root',    prettyModelLabel('foo.gguf'), 'foo.gguf');
eq('pretty empty',        prettyModelLabel(''), '');

if (failed) { console.error(`${failed} tests failed`); process.exit(1); }
console.log('all tests passed');
