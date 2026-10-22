// Shared conversation, recording, playback, settings, and agent state.

export const state = {
  // Form ownership while saved settings load or save asynchronously.
  settingsBootstrap: null,
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
  liveSession: 0,
  liveBusy: false,
  liveTranscriptionController: null,
  // Server-resolved recording budgets; kept outside persisted settings.
  audioLimits: null,
  audioLimitsError: '',
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
  partialRecorderDone: null,
  partialCaptureActive: false,
  partialNeedsFullPass: false,
  partialFallbackReason: '',
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
  // Snapshot of the config that was last loaded from /api/config or that
  // last completed a /api/llm/provider/activate. Used by closeSettings()
  // to detect whether provider-affecting fields have changed since the
  // modal opened (or since the last save). Populated by applyConfig().
  appConfigSnapshot: null,
  // True after the user activates a provider until /api/health confirms
  // the new model is loaded. Drives the providerReady pill in the debug strip.
  providerPending: false,
  agentStateReport: '',
  agentLastTranscriptHash: '',
  pendingAgentStateReport: '',
  pendingAgentReportVersion: 0,
  agentSession: 0,
  agentActivityEpoch: 0,
  agentActivityBlocked: false,
  agentControlPending: false,
  agentResetBlocked: false,
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

  // --- multimodal STT capability filter ---
  // Default OFF (filter ON): only show models with supports_audio_input=true
  // in the #sttLlmModel dropdown. Users can opt back into the full list
  // via the 'All models' override checkbox in Settings.
  capabilityFilterOverride: false,
};
