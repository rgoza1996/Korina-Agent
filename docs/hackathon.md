# Meta hackathon preparation and rehearsal

Updated 2026-10-23 for the [Meta Global AI Developer Hackathon](https://dev.meta.ai/events/global-hackathon), organized with [Cerebral Valley](https://cerebralvalley.ai/e/meta-ai-developer-hackathon). Preparation is tracked in [issue #23](https://github.com/rgoza1996/Korina-Agent/issues/23) and [PR #24](https://github.com/rgoza1996/Korina-Agent/pull/24).

## Agreed prototype

Demonstrate one useful Korina voice interaction using **Meta Model API**, conversational context, and one remembered fact. Keep the scope small enough to show the full path: microphone → transcription → Meta response using supplied context → spoken answer. The existing report-producing agent is optional for this demonstration; tool execution and a complete long-term assistant are outside this prototype.

For the first rehearsal, remembered information means a fact retained in the current conversation. Korina's existing history/report state does not establish durable memory across a restart. Meta Chat Completions is stateless: Korina must send the relevant context. Prompt caching reuses token prefixes; it is separate from remembering user information.

## Event and starting point

The organizer's fixed-EST calendar lists kickoff on **October 26 at 12 PM EST / 17:00 UTC**, submission on **November 4 at 9 PM EST / November 5 at 02:00 UTC**, and finals on November 9. The calendar uses fixed EST (UTC−5), including the October kickoff; do not substitute the local daylight-time offset. The public organizer header and calendar agree. Dates can change, and final Official Rules are still promised before October 26.

The [public waiver](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver) permits building upon existing open source with its licenses respected. It requires Meta AI products; the direct Model API interaction is this project's explicit commitment. Judging weights are impact 30%, Meta tooling 25%, technical execution 20%, and demo 25%. The final rules and submission portal still need checking for the complete artifact checklist and preparation restrictions.

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
4. Select **Meta response reasoning effort** deliberately, or leave **Model default** to match the original rehearsal’s reasoning setting. The independent agent control applies to its next saved report. These settings send Meta's documented `reasoning_effort`; the local on/off toggle is separate. Muse Spark does not accept `none`, and the rehearsed Contributor model does not support `max`. Rehearse effort and token budget together; lower effort is not a guaranteed quality/latency outcome.
5. Set **Response token budget** deliberately for the selected model. The default is 180; the supported range is 1–8192. Meta reasoning can consume this budget before a visible answer, so rehearse a useful spoken reply before choosing the final limit. The October 23 synthetic `muse-spark-1.3-contributor` rehearsal used **2048** successfully with model-default reasoning. The optional agent used its separate 2048-token budget; its default remains 512. These are measured starting points, not automatic increases.
6. Warm Whisper/Kokoro, make a real Meta response request, then run `python scripts/check_stack.py`. Resolve failed readiness checks. Model access, useful output, and live API compatibility require this authenticated rehearsal.

See [API reference](https://dev.meta.ai/docs/api-reference), [Chat Completions](https://dev.meta.ai/docs/protocols/chat-completions), and [available models](https://dev.meta.ai/docs/models). The API accepts the existing `max_tokens` alias. An empty answer with `finish_reason: "length"` is reported as an exhausted-budget error; it is not spoken or recorded as a successful assistant answer.

## One interaction to demonstrate

The [suggested practice outline](demo-script.md) helps rehearse the interaction and record actual evidence; its pacing is not an official submission requirement.

1. Start a clean conversation and provide a synthetic fact: “My project is called Cedar and its rehearsal is Friday at ten in the morning.” The accepted automated run entered its seed through the actual browser text/speech path; repeat by voice on the presentation headset.
2. Ask by voice: “What is my project called, and when is our practice?”
3. Verify the actual Meta request includes the earlier fact and current question once. Hear a useful answer that recalls the project and time.
4. Show the selected Meta model and explain which context Korina supplied. If demonstrating an agent report, show that it enters the next response once.
5. Stop Live and verify capture/playback release. Repeat once on the presentation headset without a page reload.

Record first/warm turn timings, model ID, browser, device, recognition errors, and failures in issue #23. Keep turns within the server's advertised recording budget. Physical audibility, human interruption, and presentation-model quality need rehearsal on the actual hardware.

## Caching and evidence

Meta documents automatic prefix caching. Keep stable instructions/context first and changing input last. Inspect the **Last response usage** panel for provider-reported cached input tokens; the backend reads `usage.prompt_tokens_details.cached_tokens`. Missing counts remain unavailable and zero remains a reported zero. The panel describes only the latest dispatched response, with its original request settings, and is pre-event preparation. Optional `prompt_cache_key` and broader cache experiments remain possible event additions. Do not infer a cache hit or latency saving from repeated text or one faster run. [Prompt caching](https://dev.meta.ai/docs/prompt-caching).

The [latest authenticated October 23 rehearsal](verification.md#authenticated-meta-effort-rehearsal-at-bc7c8c5) used a typed synthetic seed followed by one generated microphone question. Real Whisper base.en, direct Meta `muse-spark-1.3-contributor`, Kokoro and native playback recalled Cedar/Friday at ten; Stop released capture. Explicit minimal response effort and low agent effort used separate 2048-token budgets. The turn took 30.999 seconds on this CPU setup and the voice request reported 113 cached input tokens. An earlier separate prefix experiment observed 1777 on two follow-ups. These establish their stated synthetic interactions, without physical-headset proof, controlled timing comparisons or a cache-hit guarantee. Task spending through that voice run was 25 calls / estimated $0.003492270, including the original stopped voice attempt and a later failed native-event harness observation. [Latest allowlisted evidence](verification/meta-bc7c8c5.json), [earlier model-default record](verification/meta-637e43c.json).

A later actual text/context request at `06026b1` verified the usage panel against Meta’s returned 230 input / 153 output / 127 reasoning / 0 cached tokens. Clear reset the panel; this did not repeat voice capture or speech. Aggregate testing is now **26 calls / estimated $0.003545870** of $3, including both stopped attempts. [Usage-display evidence](verification/meta-usage-06026b1.json).

Use only appropriate demonstration data with Contributor models: their content may train Meta models. [Pricing and mode details](https://dev.meta.ai/docs/pricing-rate-limits). Keep the pre-event baseline and event additions visible in the final demonstration.

Submit through the [organizer portal](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit) only after checking its current requirements and final rules. The current public material does not establish a required video length or a mandatory public-repository format.
