# Suggested two-minute practice

This is a physical rehearsal template, not an official video-length requirement or completed submission. The [automated synthetic Meta voice run](verification.md#authenticated-meta-effort-rehearsal-at-bc7c8c5) passed with a fake microphone; it does not replace the presentation-headset check. Adjust the pacing to actual inference time; record delays and failures honestly.

Before practicing, record the source SHA with `git rev-parse HEAD`. Korina is an existing MIT-licensed project; completed refactoring and integration are pre-event preparation. At kickoff, record the actual event-start SHA separately and track later features, commits, dates, and third-party attribution in the [event ledger](hackathon.md). A practice SHA does not establish the kickoff baseline.

Use the [setup guide](hackathon.md) to prepare authenticated model access, a deliberate response token budget (2048 was measured for the Contributor rehearsal), and warmed local Whisper/Kokoro services. Leave the optional agent off for this focused interaction.

1. **0:00–0:20 — Show the setup.** Select **Meta Model API**, show the actual account-enabled response model ID and canonical API base, `https://api.meta.ai/v1`. Identify local Whisper transcription and Kokoro speech. Show the selected Meta reasoning effort; Model default matches the original rehearsal’s reasoning setting. Keep credentials out of the recording. A selected provider alone is not proof of a successful API call.
2. **0:20–0:45 — Supply one fact.** Start a clean conversation and say: “My project is called Cedar and its rehearsal is Friday at ten in the morning.” Show the actual transcript and let the real model respond. Correct a recognition error openly before continuing.
3. **0:45–1:25 — Ask a useful question.** Say: “What is my project called, and when is our practice?” Let the actual, unscripted model reply play through the physical headset. Assess whether it recalls the project and time without restating them in the question. Do not substitute a prepared answer or describe an irrelevant reply as successful.
4. **1:25–1:40 — Explain the context.** Identify the actual Meta API/model used. Check redacted request evidence for the earlier fact and current question appearing once. Explain that Korina supplies conversation context to the stateless API; this demonstrates current-session retention, not durable memory or tool execution. If the call or context cannot be verified, mark that evidence pending.
5. **1:40–2:00 — Stop cleanly.** Press Stop Live. Confirm recording and playback stop, the microphone indicator clears, and no late reply starts. Repeat a warm turn separately without reloading.

Record this evidence:

- Source SHA, actual kickoff SHA when available, and event-feature ledger entries.
- Actual Meta model ID and redacted API request/response evidence.
- First and warm turn timings, with their start/end boundaries.
- Browser, physical headset, recognition errors, audible result, and Stop behavior.
- Cache usage only if measured from `usage.prompt_tokens_details.cached_tokens`; the response UI does not expose it. A faster repeat does not prove a cache hit.

Compare results with the [verification boundaries](verification.md); local CPU or mocked-provider checks are not authenticated Meta proof.
