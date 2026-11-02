/* Is that the farmer, or the radio?
 *
 * Chrome's speech recognition hands back text and never audio, so answering
 * "only wake for my voice" needs a second, parallel capture of the same
 * microphone. That is what this is: a rolling few seconds of raw PCM, encoded
 * as a WAV and sent to the server to compare against the enrolled voiceprint.
 *
 * Two deliberate choices.
 *
 * **Raw PCM, not MediaRecorder.** MediaRecorder gives webm/opus, and decoding
 * opus on the server would mean shipping ffmpeg. The Web Audio API gives the
 * samples directly, and a WAV header is forty-four bytes of JavaScript.
 *
 * **A rolling buffer, not a recording.** Nothing is stored. The last few
 * seconds sit in memory, are overwritten continuously, and a slice is taken
 * only at the moment the wake word fires. When listening stops the buffer is
 * dropped and the microphone track is ended, so the browser's own recording
 * indicator goes out.
 *
 * It fails open. A browser without Web Audio, a check that times out, a farmer
 * who never enrolled - all of those mean "carry on and wake", because a wake
 * word that stops working when the check cannot run is worse than one that
 * occasionally answers the television.
 */
'use strict';

(function () {
  const SECONDS = 4;                 // enough for "Saarthi, mandi bhav dikhao"
  const RATE = 16000;                // what the voiceprint expects

  const state = {
    context: null,
    stream: null,
    node: null,
    buffer: null,
    writeAt: 0,
    filled: false,
    running: false,
  };

  function supported() {
    return Boolean(
      (window.AudioContext || window.webkitAudioContext) &&
      navigator.mediaDevices && navigator.mediaDevices.getUserMedia,
    );
  }

  /* Start capturing. Shares the microphone with the recogniser - getUserMedia
     hands out the same device to both, and the browser shows one indicator. */
  async function start() {
    if (state.running || !supported()) return false;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      state.context = new Ctx({ sampleRate: RATE });
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      const source = state.context.createMediaStreamSource(state.stream);
      // ScriptProcessor is deprecated in favour of AudioWorklet, and is used
      // anyway: a worklet needs a separate module file served from the same
      // origin, and this runs in browsers that are often two years behind.
      const node = state.context.createScriptProcessor(4096, 1, 1);
      state.buffer = new Float32Array(RATE * SECONDS);
      state.writeAt = 0;
      state.filled = false;

      node.onaudioprocess = (event) => {
        const chunk = event.inputBuffer.getChannelData(0);
        for (let i = 0; i < chunk.length; i += 1) {
          state.buffer[state.writeAt] = chunk[i];
          state.writeAt += 1;
          if (state.writeAt >= state.buffer.length) {
            state.writeAt = 0;
            state.filled = true;
          }
        }
      };
      source.connect(node);
      // Connected to the destination with no gain: some browsers will not run
      // a ScriptProcessor that goes nowhere, and this must not be audible.
      const silence = state.context.createGain();
      silence.gain.value = 0;
      node.connect(silence);
      silence.connect(state.context.destination);

      state.node = node;
      state.running = true;
      return true;
    } catch (err) {
      stop();
      return false;
    }
  }

  function stop() {
    state.running = false;
    try { if (state.node) state.node.disconnect(); } catch (err) { /* gone */ }
    try {
      if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
    } catch (err) { /* gone */ }
    try { if (state.context) state.context.close(); } catch (err) { /* gone */ }
    state.node = null;
    state.stream = null;
    state.context = null;
    // Drop the samples. Nothing here outlives the listening.
    state.buffer = null;
    state.writeAt = 0;
    state.filled = false;
  }

  /** The last [seconds] of audio, oldest first, or null. */
  function recent(seconds) {
    if (!state.buffer) return null;
    const want = Math.min(Math.floor(RATE * seconds), state.buffer.length);
    const have = state.filled ? state.buffer.length : state.writeAt;
    if (have < RATE * 1.3) return null;          // too little to describe a voice
    const out = new Float32Array(want);
    for (let i = 0; i < want; i += 1) {
      let at = state.writeAt - want + i;
      while (at < 0) at += state.buffer.length;
      out[i] = state.buffer[at % state.buffer.length];
    }
    return out;
  }

  /** 16-bit PCM WAV, which is the only thing the server decodes. */
  function toWav(samples, rate) {
    const bytes = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(bytes);
    const text = (at, s) => {
      for (let i = 0; i < s.length; i += 1) view.setUint8(at + i, s.charCodeAt(i));
    };
    text(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    text(8, 'WAVE');
    text(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);                 // PCM
    view.setUint16(22, 1, true);                 // mono
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    text(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i += 1) {
      const clamped = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(44 + i * 2, clamped * 32767, true);
    }
    return new Blob([bytes], { type: 'audio/wav' });
  }

  /**
   * Who just spoke? Resolves to the server's verdict, or to "unknown".
   *
   * Never throws and never resolves to a refusal it is not sure of: every
   * failure here - no capture, no network, nobody enrolled - comes back as
   * "unknown", which every caller treats as "carry on".
   */
  async function whoSpoke(seconds) {
    const samples = recent(seconds || 3);
    if (!samples) return { match: 'unknown', score: 0, reason: 'no audio' };
    try {
      const form = new FormData();
      form.append('audio', toWav(samples, RATE), 'wake.wav');
      const response = await fetch('/api/voiceid/check', {
        method: 'POST', credentials: 'include', body: form,
      });
      if (!response.ok) return { match: 'unknown', score: 0, reason: 'http' };
      return await response.json();
    } catch (err) {
      return { match: 'unknown', score: 0, reason: 'offline' };
    }
  }

  /* May the voice that just spoke act on this account?
   *
   * Separate from `whoSpoke` because the two answer different questions.
   * `whoSpoke` reports a similarity and leaves the caller to decide; this one
   * carries the decision the server made, with the sentence to say when the
   * answer is no. The decision belongs on the server: it holds the enrolled
   * voiceprint, and a client that decided for itself could simply not ask.
   *
   * Anything that goes wrong here - offline, no audio, a bad response - allows
   * the turn. A microphone that stops working must not lock a farmer out of
   * their own helpline.
   */
  async function mayAct(seconds, language) {
    const samples = recent(seconds || 3);
    if (!samples) return { allow: true, reason: 'no audio to judge' };
    try {
      const form = new FormData();
      form.append('audio', toWav(samples, RATE), 'turn.wav');
      form.append('language', language || 'hi');
      const response = await fetch('/api/voiceid/gate', {
        method: 'POST', credentials: 'include', body: form,
      });
      if (!response.ok) return { allow: true, reason: 'could not check' };
      return await response.json();
    } catch (err) {
      return { allow: true, reason: 'offline' };
    }
  }

  window.SaarthiVoiceId = { start, stop, whoSpoke, mayAct, supported, recent, toWav };
})();
