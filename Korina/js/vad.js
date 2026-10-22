// Wave meter, audio level measurement, and adaptive voice activity detection.

import { state } from "./state.js";
import { $, clamp } from "./dom.js";

// VAD base thresholds. Kept local to
// this module since no other extracted module references them.
const VAD_BASE_SPEECH_THRESHOLD = 0.035;
const VAD_BASE_SILENCE_THRESHOLD = 0.022;

// Waveform meter
export function drawWave() {
  if (!state.analyser) return;
  const data = new Uint8Array(state.analyser.frequencyBinCount);
  state.analyser.getByteTimeDomainData(data);
  const wave = $('wave');
  const styles = getComputedStyle(wave);
  const inner = Math.max(80, wave.clientWidth - parseFloat(styles.paddingLeft) - parseFloat(styles.paddingRight));
  const barW = 3, gap = 2;
  const count = Math.max(24, Math.floor(inner / (barW + gap)));
  while (wave.children.length > count) wave.lastElementChild.remove();
  while (wave.children.length < count) {
    const bar = document.createElement('div');
    bar.className = 'bar';
    wave.appendChild(bar);
  }
  for (let i = 0; i < count; i++) {
    const idx = Math.min(data.length - 1, Math.floor(i * data.length / count));
    const v = Math.abs(data[idx] - 128) / 128;
    wave.children[i].style.height = (4 + v * 44) + 'px';
  }
  state.raf = requestAnimationFrame(drawWave);
}

// RMS level
export function rmsLevel() {
  if (!state.analyser) return 0;
  const data = new Uint8Array(state.analyser.fftSize);
  state.analyser.getByteTimeDomainData(data);
  let sum = 0;
  for (const x of data) {
    const v = (x - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / data.length);
}

// Adaptive VAD reset
export function resetAdaptiveVad() {
  state.vadNoiseFloor = 0.012;
  state.vadNoiseSamples = [];
  state.vadIdleNoiseSamples = [];
  state.vadCalibratingUntil = performance.now() + 900;
  state.vadLastIdleRecalibrationAt = performance.now();
  state.vadSpeechFrames = 0;
  state.vadSilenceFrames = 0;
  state.vadSpeechThreshold = VAD_BASE_SPEECH_THRESHOLD;
  state.vadSilenceThreshold = VAD_BASE_SILENCE_THRESHOLD;
}

// Adaptive VAD update
export function updateAdaptiveVad(level, now) {
  // Initial room/mic calibration runs when Live starts. After that, recalibrate at intervals only during true idle.
  if (!state.liveSpeechStart && now < state.vadCalibratingUntil) {
    state.vadNoiseSamples.push(level);
    const sorted = [...state.vadNoiseSamples].sort((a, b) => a - b);
    const p60 = sorted[Math.floor(sorted.length * 0.60)] || level;
    state.vadNoiseFloor = clamp(p60, 0.002, 0.035);
  } else {
    const audioIdle = !state.liveSpeechStart && !state.liveBusy && !state.ttsSpeaking && (!state.audioCtx || state.audioCtx.currentTime >= state.ackPlayingUntil) && performance.now() >= state.suppressAckUntil;
    const looksQuiet = level < Math.max(state.vadSilenceThreshold, state.vadNoiseFloor * 2.0 + 0.008);
    if (audioIdle && looksQuiet) {
      state.vadIdleNoiseSamples.push(level);
      if (state.vadIdleNoiseSamples.length > 240) state.vadIdleNoiseSamples.splice(0, state.vadIdleNoiseSamples.length - 240);
      if (now - state.vadLastIdleRecalibrationAt >= 10000 && state.vadIdleNoiseSamples.length >= 30) {
        const sorted = [...state.vadIdleNoiseSamples].sort((a, b) => a - b);
        const p60 = sorted[Math.floor(sorted.length * 0.60)] || level;
        const nextFloor = clamp(p60, 0.002, 0.035);
        state.vadNoiseFloor = clamp(state.vadNoiseFloor * 0.70 + nextFloor * 0.30, 0.002, 0.035);
        state.vadIdleNoiseSamples = [];
        state.vadLastIdleRecalibrationAt = now;
        $('transcriptInfo').textContent = `Idle noise recalibrated ${state.vadNoiseFloor.toFixed(3)} · speech>${state.vadSpeechThreshold.toFixed(3)} · silence<${state.vadSilenceThreshold.toFixed(3)}`;
      }
    } else if (level > state.vadSpeechThreshold || state.liveSpeechStart || state.ttsSpeaking || state.liveBusy) {
      state.vadIdleNoiseSamples = [];
      if (now - state.vadLastIdleRecalibrationAt > 30000) state.vadLastIdleRecalibrationAt = now;
    }
  }
  state.vadSilenceThreshold = clamp(state.vadNoiseFloor * 2.2 + 0.006, 0.012, 0.045);
  state.vadSpeechThreshold = clamp(Math.max(state.vadSilenceThreshold + 0.010, state.vadNoiseFloor * 3.4 + 0.012), 0.026, 0.085);
}
