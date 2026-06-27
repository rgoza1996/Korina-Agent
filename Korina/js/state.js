// js/state.js
//
// Single source of truth for module-level state previously held in
// inline top-level `let`/`const` declarations in index.html.
// Modules import { state, EventBus } from './state.js' and read/write
// properties. The shape and default values mirror the original globals 1:1.

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

export const EventBus={
  pendingDeltas:[],pendingReports:[],pendingInterrupts:[],permissionQueue:[],
  transcriptHistory:[],agentTaskState:null,agentIdle:true,
  lastConverseActivity:0,lastAgentActivity:0,turnsSinceLastDelivery:0,
  lastPeriodicDeliveryAt:0,firstDeliveryDone:false,firstDeliveryTimer:null,
  config:{
    agent_enabled:'off',agent_first_delivery_mode:'first_turn_or_timer',
    agent_first_delivery_seconds:20,agent_periodic_delivery_turns:2,
    agent_periodic_delivery_seconds:45,agent_busy_delivery_mode:'injection',
    agent_idle_delivery_mode:'prompt',agent_interrupts_enabled:'on',
    agent_interrupt_min_priority:'important',agent_hard_interrupt_min_priority:'critical',
    agent_permission_interrupts:'on',agent_report_injection_mode:'next_reply',
  },
  emit(type,data){
    const event={type,data,ts:Date.now()};
    switch(type){
      case'TranscriptDelta':this.pendingDeltas.push(event);break;
      case'ConversationTurnComplete':
        this.transcriptHistory.push(event.data);
        this.turnsSinceLastDelivery++;
        this.lastConverseActivity=Date.now();
        this.pendingDeltas.push(event);
        this._checkPeriodicDelivery();
        this._checkFirstDelivery();
        break;
      case'AgentStateReport':
        this.agentTaskState=event.data;
        this.lastAgentActivity=Date.now();
        this.agentIdle=event.data.state==='idle';
        this._processPendingReports();
        break;
      case'AgentInterrupt':
        this.pendingInterrupts.push(event);
        this._processInterrupts();
        break;
      case'AgentPermissionRequest':
        this.permissionQueue.push(event);
        this._processPermissions();
        break;
      case'UserPermissionAnswer':
        this._routePermissionAnswer(event.data);
        break;
      case'AgentTaskStatus':
        this.agentIdle=event.data.state==='idle';
        break;
    }
  },
  _checkFirstDelivery(){
    if(this.firstDeliveryDone)return;
    const mode=this.config.agent_first_delivery_mode;
    if(mode==='first_turn'){this._deliverDeltas('first-turn');this.firstDeliveryDone=true;}
  },
  _startFirstDeliveryTimer(){
    if(this.firstDeliveryTimer)clearTimeout(this.firstDeliveryTimer);
    const secs=parseInt(this.config.agent_first_delivery_seconds)*1000;
    this.firstDeliveryTimer=setTimeout(()=>{
      if(!this.firstDeliveryDone){this._deliverDeltas('first-timer');this.firstDeliveryDone=true;}
    },secs);
  },
  _checkPeriodicDelivery(){
    if(!this.firstDeliveryDone)return;
    const now=Date.now();
    const turnsOk=this.turnsSinceLastDelivery>=parseInt(this.config.agent_periodic_delivery_turns);
    const timeOk=(now-this.lastPeriodicDeliveryAt)>=parseInt(this.config.agent_periodic_delivery_seconds)*1000;
    if(turnsOk||timeOk){this._deliverDeltas('periodic');this.turnsSinceLastDelivery=0;this.lastPeriodicDeliveryAt=now;}
  },
  _deliverDeltas(trigger){
    if(this.pendingDeltas.length===0)return;
    const payload={turns:this.transcriptHistory,trigger,ts:Date.now()};
    this.pendingDeltas=[];
    this._callAgent(payload);
  },
  async _callAgent(payload){
    if(this.config.agent_enabled!=='on')return;
    try{
      const resp=await fetch('/api/agent/state-report',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
      });
      const result=await resp.json();
      if(result.state_report)this.emit('AgentStateReport',result.state_report);
      if(result.interrupt)this.emit('AgentInterrupt',result.interrupt);
      if(result.permission_request)this.emit('AgentPermissionRequest',result.permission_request);
    }catch(e){console.error('EventBus agent call failed',e);}
  },
  _processPendingReports(){
    if(!this.agentTaskState)return;
    window._pendingInjection=this.agentTaskState.text||'';
    window._pendingInjectionPriority=this.agentTaskState.priority||'normal';
    this.pendingReports=[];
  },
  _processInterrupts(){
    if(this.config.agent_interrupts_enabled!=='on')return;
    const minPrio=this.config.agent_interrupt_min_priority;
    const hardPrio=this.config.agent_hard_interrupt_min_priority;
    while(this.pendingInterrupts.length>0){
      const intr=this.pendingInterrupts.shift();
      const p=intr.data.priority||'normal';
      if(this._priorityLevel(p)>=this._priorityLevel(hardPrio))this._hardInterrupt(intr.data);
      else if(this._priorityLevel(p)>=this._priorityLevel(minPrio))this._sentenceBoundaryInterrupt(intr.data);
      else this._injectIntoNextReply(intr.data);
    }
  },
  _processPermissions(){
    if(this.config.agent_permission_interrupts!=='on')return;
    while(this.permissionQueue.length>0)this._askUserPermission(this.permissionQueue.shift().data);
  },
  _askUserPermission(data){
    if(typeof speakText==='function')speakText(data.question||'Korina Agent needs your permission.',{interrupt:true});
    window._pendingPermissionAnswer={request_id:data.request_id,choices:data.choices||['yes','no']};
  },
  _routePermissionAnswer(data){
    fetch('/api/agent/permission-answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).catch(e=>console.error('permission answer failed',e));
    window._pendingPermissionAnswer=null;
  },
  _sentenceBoundaryInterrupt(data){window._pendingSentenceInterrupt=data;},
  _hardInterrupt(data){
    if(typeof stopSpeaking==='function')stopSpeaking();
    if(typeof speakText==='function')speakText(data.message||'Important update from Korina Agent.',{interrupt:true});
  },
  _injectIntoNextReply(data){window._pendingInjection=(window._pendingInjection||'')+' '+(data.message||'');},
  _priorityLevel(p){return{low:0,normal:1,important:2,critical:3}[p]||1;},
  resetDelivery(){this.turnsSinceLastDelivery=0;this.lastPeriodicDeliveryAt=Date.now();},
  reset(){
    this.pendingDeltas=[];this.pendingReports=[];this.pendingInterrupts=[];this.permissionQueue=[];
    this.transcriptHistory=[];this.agentTaskState=null;this.turnsSinceLastDelivery=0;this.firstDeliveryDone=false;
    if(this.firstDeliveryTimer)clearTimeout(this.firstDeliveryTimer);
    window._pendingInjection='';window._pendingSentenceInterrupt=null;window._pendingPermissionAnswer=null;
  },
  updateConfig(cfg){Object.assign(this.config,cfg);}
};

// Module-level periodic delivery check (verbatim from index.html line 309).
setInterval(()=>{EventBus._checkPeriodicDelivery();},5000);
