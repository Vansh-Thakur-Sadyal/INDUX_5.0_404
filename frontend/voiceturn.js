/* Listening, in one place, without the browser's own recogniser.
 *
 * What was wrong with what this replaces
 * --------------------------------------
 * Agent mode listened through `webkitSpeechRecognition`, and that decided two
 * things it had no business deciding:
 *
 *   1. **The language, before anybody spoke.** The recogniser is handed one
 *      BCP-47 tag when it starts, and the tag came from whatever the language
 *      picker happened to be set to. A farmer with the page on English who
 *      spoke Tamil was transcribed by an English engine - so the words were
 *      nonsense, the server read that nonsense as English, and the answer came
 *      back in English. "Auto language change" could not work, because nothing
 *      in the chain ever listened to the sound.
 *   2. **Whether listening worked at all.** It is Chrome's, it ships the audio
 *      to Google, and on Firefox, on most in-app browsers and on a locked-down
 *      network it simply is not there. The orb glowed and nothing happened.
 *
 * So the audio is captured here and sent to our own server, where Azure
 * identifies which of the fourteen it is FROM THE SOUND and transcribes it in
 * the same request. The recorder is `MediaRecorder`, which every current
 * browser has, and the end of a turn is decided by listening to the volume
 * rather than by asking a cloud recogniser's opinion.
 *
 * Deciding when a turn ended
 * --------------------------
 * An `AnalyserNode` gives the RMS of the last frame roughly every 50 ms. The
 * noise floor is measured over the first half second rather than assumed: a
 * phone held in a field and a laptop in a room are nowhere near each other,
 * and a fixed threshold means one of them never triggers. Speech is anything
 * well above that floor; a turn ends after `TAIL_MS` below it, and never
 * before `MIN_TURN_MS`, so a breath mid-sentence is not a turn boundary.
 *
 * The whole turn in one request
 * -----------------------------
 * `/api/chat/voice` takes the clip and returns NDJSON: the transcript, the
 * language and the reply on the first line, the audio URL on the second. The
 * words are on screen while the reply is still being synthesised.
 */
'use strict';

(function () {
  const RATE_HINT = 16000;

  /* Roughly how loud, relative to the measured floor, counts as somebody
     talking. Multiplicative rather than absolute, so it travels between a quiet
     room and a tractor. */
  const SPEECH_OVER_FLOOR = 2.6;
  const FLOOR_FLOOR = 0.004;        // in a silent room the floor is ~0; do not divide into it

  const CALIBRATE_MS = 500;         // measuring the room before judging anything
  const MIN_TURN_MS = 700;          // below this it is a cough, not a turn
  const MAX_TURN_MS = 20000;        // a hard stop, so a stuck gate is not a recording
  const TAIL_MS = 900;              // quiet for this long and the turn is over
  const GIVE_UP_MS = 12000;         // nobody said anything at all

  const state = {
    stream: null,
    context: null,
    analyser: null,
    recorder: null,
    chunks: [],
    timer: null,
    watching: false,
    finish: null,
  };

  function supported() {
    return Boolean(navigator.mediaDevices
      && navigator.mediaDevices.getUserMedia
      && window.MediaRecorder
      && (window.AudioContext || window.webkitAudioContext));
  }

  /* The container MediaRecorder will actually produce. Safari gives mp4, Chrome
     and Firefox give webm/opus; Azure reads all of them, but the content type
     has to be the truth or the server guesses wrong. */
  function pickType() {
    const wanted = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
    for (const type of wanted) {
      if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    return '';
  }

  async function open() {
    if (state.stream) return state.stream;
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: RATE_HINT,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    const Context = window.AudioContext || window.webkitAudioContext;
    state.context = new Context();
    /* Resumed explicitly: a context created before the page has been touched
       starts suspended, and a suspended analyser reports silence for ever -
       which looks exactly like a farmer who never spoke. */
    if (state.context.state === 'suspended') {
      try { await state.context.resume(); } catch (err) { /* nothing to do */ }
    }
    const source = state.context.createMediaStreamSource(state.stream);
    state.analyser = state.context.createAnalyser();
    state.analyser.fftSize = 1024;
    source.connect(state.analyser);
    return state.stream;
  }

  function level(buffer) {
    state.analyser.getByteTimeDomainData(buffer);
    let sum = 0;
    for (let i = 0; i < buffer.length; i += 1) {
      const v = (buffer[i] - 128) / 128;
      sum += v * v;
    }
    return Math.sqrt(sum / buffer.length);
  }

  /**
   * Listen until they stop talking, then hand over the clip.
   *
   * Resolves with a Blob, or with null when nobody said anything - which is an
   * ordinary outcome and not an error: the caller listens again.
   */
  function listenOnce() {
    return new Promise((resolve) => {
      const type = pickType();
      const recorder = type ? new MediaRecorder(state.stream, { mimeType: type })
                            : new MediaRecorder(state.stream);
      state.recorder = recorder;
      state.chunks = [];
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) state.chunks.push(event.data);
      };

      /* Two ways this ends and they resolve differently.
         `finish()` with nothing means "the turn is over, hand over what was
         recorded" - so it stops the recorder and lets `onstop` resolve with the
         blob. Resolving here as well would settle the promise with `undefined`
         first and throw the recording away, which is a microphone that listens
         perfectly and sends nothing.
         `finish(null)` means "there is nothing to send": `onstop` is detached so
         it cannot resolve afterwards, and null goes back at once. */
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        state.watching = false;
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
        if (value === null && recorder.onstop) recorder.onstop = null;
        try { if (recorder.state !== 'inactive') recorder.stop(); } catch (err) { /* gone */ }
        if (value === null) resolve(null);
      };
      // So a tap on the orb mid-sentence settles the promise instead of leaving
      // the loop waiting for a turn that will never be handed over.
      state.finish = finish;

      recorder.onstop = () => {
        const blob = new Blob(state.chunks, { type: recorder.mimeType || type || 'audio/webm' });
        state.chunks = [];
        state.finish = null;
        // Under a couple of kilobytes there is nothing in it worth a round trip.
        resolve(spoke && blob.size > 2000 ? blob : null);
      };

      const buffer = new Uint8Array(state.analyser.fftSize);
      const started = Date.now();
      let floor = 0;
      let floorSamples = 0;
      let spoke = false;
      let lastLoud = 0;

      state.watching = true;
      recorder.start(200);

      state.timer = setInterval(() => {
        const now = Date.now();
        const elapsed = now - started;
        const rms = level(buffer);

        if (elapsed < CALIBRATE_MS) {
          floor = (floor * floorSamples + rms) / (floorSamples + 1);
          floorSamples += 1;
          return;
        }

        const bar = Math.max(floor, FLOOR_FLOOR) * SPEECH_OVER_FLOOR;
        if (rms > bar) {
          if (!spoke) spoke = true;
          lastLoud = now;
          return;
        }

        if (spoke && lastLoud && now - lastLoud > TAIL_MS
            && now - started > MIN_TURN_MS) {
          finish(undefined);               // onstop resolves with the blob
          return;
        }
        if (!spoke && elapsed > GIVE_UP_MS) {
          finish(null);
          return;
        }
        if (elapsed > MAX_TURN_MS) {
          finish(undefined);
        }
      }, 50);
    });
  }

  /** Stop listening now and throw away whatever was being recorded. */
  function abort() {
    // Through `finish` so a `listenOnce` that is still waiting settles with
    // null. Aborting the recorder behind its back leaves that promise pending
    // for ever, and the conversation loop is waiting on it.
    if (state.finish) {
      state.finish(null);
      state.finish = null;
    }
    state.watching = false;
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.chunks = [];
    if (state.recorder) {
      try {
        if (state.recorder.state !== 'inactive') {
          state.recorder.onstop = null;
          state.recorder.stop();
        }
      } catch (err) { /* already stopped */ }
      state.recorder = null;
    }
  }

  /** Give the microphone back. */
  function close() {
    abort();
    if (state.stream) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
    }
    if (state.context) {
      try { state.context.close(); } catch (err) { /* already closed */ }
      state.context = null;
      state.analyser = null;
    }
  }

  /**
   * One spoken turn: the clip up, the answer back.
   *
   * `onText` is called the moment the words exist - before the reply has been
   * synthesised - because that is the whole reason the endpoint streams. The
   * promise resolves once the audio URL has arrived too (or at once, when no
   * audio was asked for).
   */
  async function ask(blob, options) {
    const settings = options || {};
    const form = new FormData();
    const name = (blob.type || '').includes('mp4') ? 'turn.mp4' : 'turn.webm';
    form.append('audio', blob, name);
    form.append('language', settings.language || 'en');
    form.append('speak', settings.speak === false ? 'false' : 'true');
    form.append('channel', settings.channel || 'dashboard');
    if (settings.sessionId) form.append('session_id', settings.sessionId);
    if (settings.page) form.append('page', settings.page);
    if (settings.phone) form.append('phone', settings.phone);

    const response = await fetch('/api/chat/voice', {
      method: 'POST', body: form, credentials: 'include',
    });
    if (!response.ok) {
      throw new Error('The turn could not be sent (HTTP ' + response.status + ')');
    }

    const reader = response.body && response.body.getReader
      ? response.body.getReader() : null;
    // A browser with no streaming body still gets the whole thing, just not early.
    if (!reader) {
      const lines = (await response.text()).split('\n').filter(Boolean);
      const first = JSON.parse(lines[0] || '{}');
      const second = lines[1] ? JSON.parse(lines[1]) : {};
      if (settings.onText) settings.onText(first);
      return Object.assign(first, second);
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let turn = {};
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let piece;
        try { piece = JSON.parse(line); } catch (err) { continue; }
        const first = !turn.reply && piece.reply !== undefined;
        turn = Object.assign(turn, piece);
        if (first && settings.onText) settings.onText(turn);
      }
    }
    if (buffer.trim()) {
      try { turn = Object.assign(turn, JSON.parse(buffer)); } catch (err) { /* partial */ }
    }
    return turn;
  }

  window.SaarthiVoiceTurn = { supported, open, close, abort, listenOnce, ask };
})();
