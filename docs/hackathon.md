# Meta hackathon preparation and rehearsal

Updated 2026-10-23 for the [Meta Global AI Developer Hackathon](https://dev.meta.ai/events/global-hackathon), organized with [Cerebral Valley](https://cerebralvalley.ai/e/meta-ai-developer-hackathon). Preparation is tracked in [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23) and [PR #24](https://github.com/rgoza1996/Korina-Agent/pull/24).

[Watch the accepted pre-event recording at `4158cd2`](https://rgoza1996.github.io/Korina-Agent/Meta-Demo.html): a typed synthetic fact and generated-microphone question through actual local Whisper, Meta Model API and local Kokoro. The player includes actual browser audio and English captions, with waiting retained. This does not replace the presentation-headset rehearsal.

## Agreed prototype

Demonstrate one useful Korina voice interaction using **Meta Model API**, conversational context, and one remembered fact. Keep the scope small enough to show the full path: microphone → transcription → Meta response using supplied context → spoken answer. The existing report-producing agent is optional for this demonstration; tool execution and a complete long-term assistant are outside this prototype.

For this interaction, remembered information means a fact retained in the current conversation. Korina's existing history/report state does not establish durable memory across a restart. Meta Chat Completions is stateless: Korina must send the relevant context. Prompt caching reuses token prefixes; it is separate from remembering user information.

## Event and starting point

The organizer's fixed-EST calendar lists kickoff on **October 26 at 12 PM EST / 17:00 UTC**, submission on **November 4 at 9 PM EST / November 5 at 02:00 UTC**, and finals on November 9. The calendar uses fixed EST (UTC−5), including the October kickoff; do not substitute the local daylight-time offset. The public organizer header and calendar agree. Dates can change, and final Official Rules are still promised before October 26.

The [public waiver](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver) permits building upon existing open source with its licenses respected. It requires Meta AI products; the direct Model API interaction is this project's explicit commitment. Judging weights are impact 30%, Meta tooling 25%, technical execution 20%, and demo 25%. The final rules and submission portal still need checking for the complete artifact checklist and preparation restrictions.

**October 23 recheck:** Meta's [public event page](https://dev.meta.ai/events/global-hackathon) still says final Official Rules will be published before October 26 and must be accepted by selected participants. The earlier waiver details above were not reconfirmed in this check. Confirm final eligibility and artifact requirements when the rules are available.

Korina is an existing MIT-licensed project. The refactor, provider integration, tests, and documentation completed before kickoff are **pre-event preparation**, not event-built features. At kickoff, record the actual starting commit with `git rev-parse HEAD`; use the final verified preparation commit recorded in issue #23 as context, rather than treating the older `alpha` branch as the event baseline.

Maintain this ledger during the event:

| Item | Record |
| --- | --- |
| Event starting commit | Pending kickoff; full commit SHA |
| Existing functionality | Voice capture/STT/TTS, conversation history, bounded agent reports, provider infrastructure, preparation tests |
| Event additions | Feature, commit, date, and behavior added after the recorded start |
| Demonstration evidence | Actual Meta model ID, context supplied, observed result, timings, and video link |
| Third-party work | Existing code, model/services used, licenses, and attribution |

This ledger does not claim any future implementation or submission is complete.

## Configure the focused demo

1. Install from the [README](../README.md), including FFmpeg, Whisper, and Kokoro.
2. Select the explicit **Meta Model API** response provider. Its canonical API base is `https://api.meta.ai/v1`; authentication uses a Bearer key from `MODEL_API_KEY` by default. Configure that variable privately in the API process environment before starting/restarting it, or in the existing service environment file. The [README](../README.md#meta-model-api-rehearsal) includes a hidden foreground key prompt. The UI expects the variable name, not the key value. Explicit role credentials retain their normal scope.
3. Load models through authenticated discovery and select a model actually enabled for the team. Documentation examples do not prove account access. Keep Whisper for STT and Kokoro for TTS; the Meta text-model demonstration does not require Meta audio output.
4. Select **Meta response reasoning effort** deliberately. **Model default** leaves the field omitted and matches the original `637e43c` rehearsal; the latest accepted `4158cd2` recording deliberately used **minimal**. The independent agent control applies to its next saved report. These settings send Meta's documented `reasoning_effort`; the local on/off toggle is separate. Muse Spark does not accept `none`, and the rehearsed Contributor model does not support `max`. Rehearse effort and token budget together; lower effort is not a guaranteed quality/latency outcome.
5. Set **Response token budget** deliberately for the selected model. The default is 180; the supported range is 1–8192. Both the original model-default rehearsal and latest minimal-effort `muse-spark-1.3-contributor` recording used **2048** successfully. Leave **Agent and acknowledgements off** to match the focused `4158cd2` recording. Earlier optional-agent rehearsals used a separate 2048-token budget; the agent default remains 512. Meta reasoning shares the output budget, so rehearse a useful spoken reply before choosing the final limit. These are measured starting points, not automatic increases.
6. Warm Whisper/Kokoro, make a real Meta response request, then run `python scripts/check_stack.py`. Resolve failed readiness checks. Model access, useful output, and live API compatibility require this authenticated rehearsal.

See [API reference](https://dev.meta.ai/docs/api-reference), [Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions), and [available models](https://dev.meta.ai/docs/models). The API accepts the existing `max_tokens` alias. An empty answer with `finish_reason: "length"` is reported as an exhausted-budget error; it is not spoken or recorded as a successful assistant answer.

## One interaction to demonstrate

The [suggested practice outline](demo-script.md) helps rehearse the interaction and record actual evidence; its pacing is not an official submission requirement.

Choose the speech mode before practicing. **SSE streaming** starts playback as chunks arrive and can pause between chunks during CPU synthesis. **Buffered WAV** waits for the complete reply before playback, avoiding waits for synthesis during playback while increasing the initial wait. The [rehearsal runbook](demo-script.md) describes this choice; neither mode guarantees a faster complete turn. The public recording used SSE.

1. Start a clean conversation and provide a synthetic fact: “My project is called Cedar and its rehearsal is Friday at ten in the morning.” The accepted automated run entered its seed through the actual browser text/speech path; repeat by voice on the presentation headset.
2. Ask by voice: “What is my project called, and when is our practice?”
3. Verify the actual Meta request includes the earlier fact and current question once. Hear a useful answer that recalls the project and time.
4. Show the selected Meta model and explain which context Korina supplied. If demonstrating an agent report, show that it enters the next response once.
5. Stop Live and verify capture/playback release. Repeat once on the presentation headset without a page reload.

Record first/warm turn timings, model ID, browser, device, recognition errors, and failures in issue #23. Keep turns within the server's advertised recording budget. Physical audibility, human interruption, and presentation-model quality need rehearsal on the actual hardware.

## Caching and evidence

Meta documents automatic prefix caching. Keep stable instructions/context first and changing input last. Inspect the **Last response usage** panel for provider-reported cached input tokens; the backend reads `usage.prompt_tokens_details.cached_tokens`. Missing counts remain unavailable and zero remains a reported zero. The panel describes only the latest dispatched response, with its original request settings, and is pre-event preparation. Optional `prompt_cache_key` and broader cache experiments remain possible event additions. Do not infer a cache hit or latency saving from repeated text or one faster run. [Prompt caching](https://dev.meta.ai/docs/prompt-caching).

### Latest audiovisual interaction: 4158cd2

[Watch and hear the pre-event demo](https://rgoza1996.github.io/Korina-Agent/Meta-Demo.html). A new recording of the archived UI supplies a typed Cedar/Friday-at-ten seed and one generated-microphone question. Actual Whisper `base.en` CPU/int8, direct Meta `muse-spark-1.3-contributor` with minimal effort / 2048 tokens, and local Kokoro `af_heart` CPU complete the interaction. Agent and ACKs are off. Meta answered: “Your project is called Cedar. Your rehearsal is Friday at ten in the morning.” The prior fact and current question each appeared once in the supplied request context. This demonstrates current-session context, not durable memory.

One native browser capture recorded screen and actual tab audio together; the MP4 and WebM convert that same audiovisual file, rendering native audio timestamp gaps as silence for consistent browser timing. Both retain waiting, with three English captions for the actual spoken replies. The generated microphone feeds capture directly and is not played through the tab speakers; its recognized question appears on screen. Physical microphone, speaker and human interruption checks remain separate. The [ten-minute explainer](https://rgoza1996.github.io/Korina-Agent/Explainer.html) is an illustrated architecture overview, labelled separately from this actual interaction.

The voice panel matched **246 input / 88 output / 62 reasoning / 0 cached input tokens**. Live click through observed native playback completion took **47.710 seconds**, including capture, transcription, response, CPU synthesis and recording overhead. Stop released capture; readiness before/after and graceful shutdown passed, with zero page errors. One partial request failed/cancelled before successful full transcription. This is an instrumented observation, not a controlled timing comparison or cache-saving claim. [Exact source, media hashes and limits](verification/meta-av-4158cd2.json).

The two new generations cost an estimated **$0.000080900**, bringing task testing through this recording to **43 calls / estimated $0.005781132** of $3. All priming and stopped attempts remain included. The earlier silent recording at the same source retains its own **41-call / $0.005700232** scope, different returned usage and separate PCM replay; the failed `5ef3c9d` attempt retains its **39-call / $0.005633806** scope. These are returned-usage estimates, not an invoice or account-enforced cap. [Recording history](verification.md#accepted-meta-voice-recording-at-4158cd2).

### Earlier accepted observations

The [earlier authenticated October 23 rehearsal](verification.md#authenticated-meta-effort-rehearsal-at-bc7c8c5) used a typed synthetic seed followed by one generated microphone question. Real Whisper base.en, direct Meta `muse-spark-1.3-contributor`, Kokoro and native playback recalled Cedar/Friday at ten; Stop released capture. Explicit minimal response effort and low agent effort used separate 2048-token budgets. The turn took 30.999 seconds on this CPU setup and the voice request reported 113 cached input tokens. An earlier separate prefix experiment observed 1777 on two follow-ups. These establish their stated synthetic interactions, without physical-headset proof, controlled timing comparisons or a cache-hit guarantee. Task spending through that voice run was 25 calls / estimated $0.003492270, including the original stopped voice attempt and a later failed native-event harness observation. [Earlier allowlisted evidence](verification/meta-bc7c8c5.json), [original model-default record](verification/meta-637e43c.json).

A later actual text/context request at `06026b1` verified the usage panel against Meta’s returned 230 input / 153 output / 127 reasoning / 0 cached tokens. Clear reset the panel; this did not repeat voice capture or speech. Aggregate testing through that observation was **26 calls / estimated $0.003545870** of $3, including both stopped attempts. [Usage-display evidence](verification/meta-usage-06026b1.json).

### Separate context-effort study

At `f6e655f`, 12 actual Contributor calls used identical synthetic history, a 2048-token budget and temperature 0.2: three priming calls followed by nine measured calls in balanced effort order. All six corrected-fact/calculation/unknown-location fields were correct in **12/12** replies. With three measured observations each, omitted/minimal/low effort had median instrumented transport wall times of **8.801 / 2.317 / 3.518 seconds**. Every call reported 761 input tokens; each measured group had two 753-token cache observations and one zero. This includes fresh connection/TLS work, not voice timing or proof that effort/caching caused the differences; defaults remain unchanged. [Ranges, raw usage and limitations](verification.md#authenticated-meta-context-and-effort-study-at-f6e655f).

The study's estimated $0.002049636 brings task testing through this observation to **38 generation calls / estimated $0.005595506** of $3, including priming and earlier stopped attempts. These are usage estimates, not an invoice or account cap. [Allowlisted study record](verification/meta-context-f6e655f.json).

Use only appropriate demonstration data with Contributor models: their content may train Meta models. [Pricing and mode details](https://dev.meta.ai/docs/pricing-rate-limits). Keep the pre-event baseline and event additions visible in the final demonstration.

Submit through the [organizer portal](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit) only after checking its current requirements and final rules. The current public material does not establish a required video length or a mandatory public-repository format.
