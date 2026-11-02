/* Agent mode: Saarthi listens, answers out loud, does what it is told, and
 * keeps the conversation open.
 *
 * Why this replaced what was here
 * -------------------------------
 * The first version typed the recognised words into the chat box and clicked
 * the Send button it found by looking for a button whose text was "Send". The
 * dashboard translates its own interface, so for a farmer using Hindi that
 * button says something else and the click never happened: the microphone
 * listened, the words were recognised, and nothing at all occurred. Driving a
 * user interface by pretending to be a user is brittle in exactly that way.
 *
 * This talks to the API directly. Nothing here depends on a page being
 * rendered, on a label being in English, or on a button existing.
 *
 * The loop
 * --------
 *     listen  ->  think  ->  (do)  ->  speak  ->  listen ...
 *
 * It stays open between turns, so "mandi bhav dikhao" can be followed by "aur
 * gehu ka?" without saying the name again - which is the difference between a
 * voice command and a conversation. It closes on "band karo" or on a tap.
 *
 * All of it is one icon
 * ---------------------
 * There was a panel here with a transcript in it, and it was wrong twice over.
 * Its colours were unreadable in dark mode - the reply bubbles used a token the
 * dark theme never defines, so pale text landed on a pale box and every answer
 * Saarthi gave was invisible. And it should not have existed: somebody talking
 * to their phone is not reading it, and a transcript of what you just said out
 * loud is the least useful thing to put in front of you.
 *
 * So: one icon in the corner. It glows while Saarthi has the floor, and the
 * glow says which part of the turn it is in. Nothing to read, nothing to close.
 */
'use strict';

(function () {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  /* How long a completely silent conversation stays open.
     It was eighteen seconds, which is why it "stopped listening after a few
     seconds": a farmer who asks something, listens to the answer and then
     thinks about it for twenty seconds found the microphone had given up. It is
     three minutes now - long enough that it closes because the conversation
     ended, not because the farmer paused - and the timer restarts on every turn,
     so an active conversation never hits it at all. */
  const SILENCE_MS = 180000;

  const state = {
    on: false,
    phase: 'idle',           // idle | listening | thinking | doing | speaking
    recognition: null,
    audio: null,
    idleTimer: null,
    heard: '',
    busy: false,
    deaf: false,         // the microphone was refused; the orb still works
    // Is the recogniser actually running? start() throws when it already is,
    // and the throw used to be swallowed - so nothing could tell the
    // difference between "running" and "failed to start".
    running: false,
    restart: null,       // the one pending restart, so there is never a second
    quiet: 0,            // silent windows in a row, for the backoff
  };

  /* When to listen again after a window where nobody spoke - the decision that
     was the "it listens and cancels and listens and cancels" loop on phones.
     It lives in listen-backoff.js so it can be run without a browser; see
     listen-backoff.test.js. The fallback is the same numbers inline, so a
     missing script costs the tests rather than the feature. */
  const BACKOFF = (window.SaarthiListen && window.SaarthiListen.nextDelay)
    ? window.SaarthiListen
    : { nextDelay: (q) => [300, 300, 600, 1200, 2000][Math.min(q, 4)],
        giveUp: (q) => q >= 12 };

  const $ = (id) => document.getElementById(id);

  /* Can this browser hold a spoken conversation at all?
   *
   * Either way of listening will do. `SaarthiVoiceTurn` records the audio and
   * lets Azure decide which of the fourteen languages it was - which is the
   * only way the language can follow the speaker, and it works in every
   * current browser. `SpeechRecognition` is Chrome's, ships the audio to
   * Google, and is told the language before it starts; it stays as the
   * fallback because a conversation in the wrong language beats none. */
  function recorderReady() {
    return Boolean(window.SaarthiVoiceTurn && window.SaarthiVoiceTurn.supported());
  }

  function supported() {
    return Boolean(SR) || recorderReady();
  }

  function language() {
    return (window.Saarthi && window.Saarthi.language) || 'hi';
  }

  /* The BCP-47 tag for whichever of the thirteen is in force. The browser's own
     recogniser and synthesiser take a tag, not our short code, and hard-coding
     "en-IN or hi-IN" meant a farmer on Tamil was recognised as a Hindi speaker
     and answered in a Hindi voice - the two places this is used were the only
     two that had never heard of the other eleven. */
  const LOCALES = {
    en: 'en-IN', hi: 'hi-IN', bn: 'bn-IN', ta: 'ta-IN', te: 'te-IN', mr: 'mr-IN',
    gu: 'gu-IN', kn: 'kn-IN', ml: 'ml-IN', pa: 'pa-IN', or: 'or-IN', as: 'as-IN',
    ur: 'ur-IN',
  };

  function locale() {
    return LOCALES[language()] || 'hi-IN';
  }

  // -------------------------------------------------------------------- the orb
  /* One button, bottom right, and it is the entire interface.
     The SVG is inline because an icon that depends on a font or a sprite sheet
     is an icon that is sometimes a box. */
  function orb() {
    let host = $('agentOrb');
    if (host) return host;
    host = document.createElement('button');
    host.id = 'agentOrb';
    host.type = 'button';
    host.className = 'agent-orb-btn';
    host.setAttribute('aria-label', 'Talk to Saarthi');
    host.innerHTML = `
        <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
          <path fill="currentColor" fill-rule="evenodd" d="M3.75 0C1.67893 0 0 1.67893 0 3.75v16.5C0 22.3211 1.67893 24 3.75 24h16.5c2.0711 0 3.75 -1.6789 3.75 -3.75V3.75C24 1.67893 22.3211 0 20.25 0H3.75Zm2.9 6.8c0.37771 -0.50361 0.97049 -0.8 1.6 -0.8h7.5c0.6295 0 1.2223 0.29639 1.6 0.8l3.45 4.6c0.2667 0.3556 0.2667 0.8444 0 1.2l-3.45 4.6c-0.3777 0.5036 -0.9705 0.8 -1.6 0.8h-7.5c-0.62951 0 -1.22229 -0.2964 -1.6 -0.8L3.2 12.6c-0.26667 -0.3556 -0.26667 -0.8444 0 -1.2l3.45 -4.6Zm-0.275 5.1998c0 -0.8283 0.67147 -1.4998 1.49976 -1.4998 0.82707 0 1.50024 0.6702 1.50024 1.4998 0 0.8295 -0.67317 1.4997 -1.50024 1.4997 -0.82829 0 -1.49976 -0.6714 -1.49976 -1.4997Zm5.6248 0.6877c-0.8283 0 -1.4998 0.6715 -1.4998 1.4998s0.6715 1.4997 1.4998 1.4997c0.827 0 1.5002 -0.6702 1.5002 -1.4997 0 -0.8296 -0.6732 -1.4998 -1.5002 -1.4998Zm2.6252 -0.6877c0 -0.8283 0.6715 -1.4998 1.4998 -1.4998 0.827 0 1.5002 0.6702 1.5002 1.4998 0 0.8295 -0.6732 1.4997 -1.5002 1.4997 -0.8283 0 -1.4998 -0.6714 -1.4998 -1.4997Z" clip-rule="evenodd"/>
        </svg>`;
    document.body.appendChild(host);
    host.addEventListener('click', () => {
      if (state.on) stop('tapped');
      else start('');
    });
    return host;
  }

  /* The phase is the glow, and the glow is the only thing that is said about
     it. aria-live is where the words went: a screen reader announces the state
     that everyone else can see, without anything being drawn. */
  /* Questions whose answer is in a shape rather than in the text. Narrow on
     purpose: "what is the wheat price" is answered from the page, and sending
     a picture for it would be slower, dearer and less accurate. */
  const LOOKS_LIKE_A_PICTURE = new RegExp([
    'what does (this|the) (chart|graph|picture|image|screen|map)',
    'look at (this|the) (chart|graph|screen|picture)',
    'describe (this|the) (chart|graph|screen|picture|page)',
    'read (this|the) (chart|graph|picture)',
    'chart (mein|me) kya', 'graph (mein|me) kya',
    'screen (par|pe) kya dikh', 'tasveer', 'chitra',
    'चार्ट', 'ग्राफ', 'तस्वीर', 'स्क्रीन पर क्या दिख',
  ].join('|'), 'i');

  /* Questions about the SCREEN rather than about the farm. Narrow, because the
     farm assistant is the better answer for almost everything: it has the
     sensor readings, the mandi data and the fifteen agents, and the page only
     has what happens to be rendered. */
  const ABOUT_THE_PAGE = new RegExp([
    'what (is|does) (this|the) (page|screen|tab)',
    'what can i (do|press|click) (here|on this)',
    'read (this|the) (page|screen)',
    'where is the .* (button|link|field)',
    'press (the )?.* button', 'click (the )?',
    'scroll (to|down|up)',
    'is page (par|pe) kya', 'yahan kya (hai|likha)',
    'kaunsa button', 'button kahan',
    'इस पेज', 'इस स्क्रीन', 'कौन सा बटन', 'बटन कहाँ',
  ].join('|'), 'i');

  const PHASE_WORDS = {
    listening: 'Listening',
    thinking: 'Thinking',
    doing: 'Doing it',
    speaking: 'Speaking',
    idle: 'Talk to Saarthi',
  };

  function phase(name) {
    state.phase = name;
    const host = orb();
    host.dataset.phase = name;
    host.setAttribute('aria-label', PHASE_WORDS[name] || PHASE_WORDS.idle);
  }

  /* Something went wrong and the farmer has to know. Not a transcript - a
     toast, the same way the rest of the dashboard says things. */
  function tell(message) {
    if (window.Saarthi && window.Saarthi.toast) window.Saarthi.toast(message);
  }

  // ------------------------------------------------------------------ listening
  /* One turn, recorded here and answered in one request.
   *
   * This is the path that makes "reply in the language I spoke" work. The clip
   * goes up, Azure says which of the fourteen it heard, the conversational
   * engine answers in that language and the reply comes back spoken. Measured
   * on this deployment: the words appear at about 1.2 s and the audio starts
   * at about 2.6 s, against eleven to fourteen seconds for the four round
   * trips this replaces.
   */
  async function recordTurn() {
    if (!state.on || state.busy || state.deaf) return;
    phase('listening');
    resetIdle();

    let clip = null;
    try {
      await window.SaarthiVoiceTurn.open();
      clip = await window.SaarthiVoiceTurn.listenOnce();
    } catch (err) {
      state.deaf = true;
      phase('idle');
      tell('Saarthi could not use the microphone. Allow it in the address bar.');
      return;
    }
    if (!state.on) return;
    if (!clip) {
      // Nobody said anything. Same judgement as the recogniser path: back off,
      // and eventually close rather than hold the microphone open for nothing.
      state.quiet += 1;
      if (BACKOFF.giveUp(state.quiet)) {
        tell('Stopped listening - nothing was said. Tap to start again.');
        stop('quiet');
        return;
      }
      state.restart = setTimeout(() => {
        state.restart = null;
        recordTurn();
      }, BACKOFF.nextDelay(state.quiet));
      return;
    }

    state.quiet = 0;
    state.busy = true;
    phase('thinking');

    /* Whose voice was that? Started NOW and not awaited: the check and the
       answer are independent, and running them one after the other put a whole
       round trip in front of every single turn. If it comes back a refusal the
       turn is abandoned before anything is spoken. */
    const gate = (window.SaarthiVoiceId && window.SaarthiVoiceId.supported())
      ? window.SaarthiVoiceId.mayAct(3, language()).catch(() => ({ allow: true }))
      : Promise.resolve({ allow: true });

    let turn;
    try {
      turn = await window.SaarthiVoiceTurn.ask(clip, {
        language: language(),
        sessionId: window.Saarthi.sessionId || undefined,
        page: (window.Saarthi && window.Saarthi.page) || undefined,
        channel: 'dashboard',
        speak: true,
        onText: (early) => { follow(early.language); },
      });
    } catch (err) {
      tell('Could not reach Saarthi just then.');
      state.busy = false;
      listen();
      return;
    }

    const allowed = await gate;
    if (allowed && allowed.allow === false) {
      state.busy = false;
      await speak(allowed.say || 'That was not the registered voice for this account.');
      stop('not the registered voice');
      return;
    }

    if (!turn.ok && !turn.reply) {
      // Heard nothing we could use. Not a failure the farmer needs told about.
      state.busy = false;
      listen();
      return;
    }

    if (turn.session_id) window.Saarthi.sessionId = turn.session_id;
    follow(turn.language);

    if (turn.ui) {
      phase('doing');
      if (window.SaarthiWake) window.SaarthiWake.carryOut(turn.ui);
      if (turn.ui.do === 'stop') {
        await speak(turn.reply, turn.audio_url);
        stop('told to');
        return;
      }
    }

    await speak(turn.reply || '', turn.audio_url);
    state.busy = false;
    if (state.on && !state.deaf) listen();
    else if (state.on) phase('idle');
  }

  /* The answer came back in a language. Follow it.
     Without this the orb asked in whatever the picker said for ever: a farmer
     who switched to Punjabi was answered in Punjabi once, and then the next
     turn went out as English again and was answered in English. The picker and
     the voice that reads the reply both come from here. */
  function follow(code) {
    if (!code || code === language()) return;
    if (window.Saarthi && window.Saarthi.followLanguage) {
      window.Saarthi.followLanguage(code);
    }
  }

  function listen() {
    if (!state.on || state.busy || state.deaf) return;
    if (recorderReady()) {
      if (state.restart) return;
      recordTurn();
      return;
    }
    // Already listening, or already about to. Without this, two callers both
    // "making sure" produced two recognisers fighting over one microphone.
    if (state.running || state.restart) return;
    resetIdle();
    if (!state.recognition) state.recognition = build();
    if (!state.recognition) return;
    try {
      state.recognition.start();
      state.running = true;
      phase('listening');
    } catch (err) {
      // It was already running after all; leave it be and let `end` come round.
      state.running = true;
    }
  }

  /** Listen again, once, after a pause that grows while nobody is speaking. */
  function listenAgain() {
    if (!state.on || state.busy || state.deaf) return;
    if (state.restart) return;
    if (BACKOFF.giveUp(state.quiet)) {
      // Nothing for a long time. Holding the microphone open at this point is
      // a drain and a privacy cost for no benefit; the orb reopens it with one
      // tap.
      phase('idle');
      tell('Stopped listening - nothing was said. Tap to start again.');
      stop('quiet');
      return;
    }
    const wait = BACKOFF.nextDelay(state.quiet);
    state.restart = setTimeout(() => {
      state.restart = null;
      listen();
    }, wait);
  }

  function build() {
    if (!supported()) return null;
    const recognition = new SR();
    // One utterance at a time. Continuous mode keeps a single result open and
    // makes the turn boundary impossible to find; a turn ends when they stop
    // talking, which is exactly what the non-continuous recogniser reports.
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = locale();

    recognition.addEventListener('result', (event) => {
      let text = '';
      let done = false;
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        text += event.results[i][0].transcript;
        if (event.results[i].isFinal) done = true;
      }
      state.heard = text.trim();
      // Somebody is talking. Whatever the backoff had got to, start it over.
      if (state.heard) state.quiet = 0;
      if (done && state.heard) {
        handle(state.heard);
        state.heard = '';
      }
    });

    /* ONE restart path, and this is it.
     *
     * `end` always fires, including after `error`, so an error handler that
     * also restarts gives two restarts for one failed attempt - two start()
     * calls, one of them throwing, and the microphone opening and closing
     * twice a second. That is what the loop on phones was. */
    recognition.addEventListener('end', () => {
      state.running = false;
      if (!state.heard) state.quiet += 1;
      state.heard = '';
      if (state.on && !state.busy && state.phase === 'listening') listenAgain();
    });

    recognition.addEventListener('error', (event) => {
      state.running = false;
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        // The panel STAYS. Closing it on a refused microphone left the farmer
        // with nothing at all and no way to find out why - and a turn that
        // arrived some other way, such as the words said after the wake word,
        // could still be answered perfectly well.
        state.deaf = true;
        phase('idle');
        tell('Saarthi could not use the microphone. Allow it in the address bar.');
        return;
      }
      if (event.error === 'audio-capture') {
        state.deaf = true;
        phase('idle');
        tell('No microphone was available. Close anything else using it.');
        return;
      }
      // "no-speech", "aborted" and "network" are ordinary. `end` restarts.
    });

    return recognition;
  }

  function hush() {
    // Any pending restart is cancelled too. Without this, a restart queued
    // before the turn began would reopen the microphone WHILE Saarthi was
    // speaking, and it would hear itself.
    if (state.restart) { clearTimeout(state.restart); state.restart = null; }
    if (window.SaarthiVoiceTurn) window.SaarthiVoiceTurn.abort();
    state.running = false;
    if (state.recognition) {
      try { state.recognition.abort(); } catch (err) { /* already stopped */ }
    }
  }

  // -------------------------------------------------------------------- a turn
  /**
   * One thing the farmer said, all the way through.
   *
   * The server decides whether it was an instruction or a question - the same
   * check the dashboard and the phone line use - so this does not have to
   * guess, and an instruction comes back in about a tenth of a second because
   * nothing is generated for it.
   */
  async function handle(said) {
    if (!state.on) return;
    state.busy = true;
    hush();
    phase('thinking');
    resetIdle();

    /* Whose voice was that?
     *
     * An account with a registered voice is not operated by whoever happens to
     * pick up the phone. The server decides - it holds the voiceprint - and it
     * says yes far more often than no: nobody enrolled, too short a clip, a
     * weak score all carry on. Only a clear mismatch stops the turn, and then
     * the turn is CANCELLED rather than carried out with a warning.
     */
    if (window.SaarthiVoiceId && window.SaarthiVoiceId.supported()) {
      const allowed = await window.SaarthiVoiceId.mayAct(3, language());
      if (allowed && allowed.allow === false) {
        state.busy = false;
        await speak(allowed.say || 'That was not the registered voice for this account.');
        tell(allowed.say || 'Stopped: that was not the registered voice.');
        stop('not the registered voice');
        return;
      }
    }

    /* A question about something that has to be SEEN goes to the picture path
       instead. Most questions about a page are answered better from its text -
       the figure is written there, and reading it beats judging it off the
       height of a bar - so this is deliberately narrow: it triggers on asking
       about the look of a thing, not on any mention of a chart. */
    if (window.SaarthiVision && LOOKS_LIKE_A_PICTURE.test(said)) {
      phase('thinking');
      try {
        const seen = await window.SaarthiVision.look(said);
        state.busy = false;
        await speak(seen.answer);
        if (state.on && !state.deaf) listen();
        return;
      } catch (err) {
        // Fall through and answer it as an ordinary question: the page text is
        // usually enough, and a refused screen share is not a dead end.
        tell(err.message);
      }
    }

    /* A question ABOUT THE PAGE goes to the page-aware turn, which is given a
       bounded snapshot of what is on screen and may propose actions. Anything
       else - a price, the weather, advice - goes to the farm assistant, which
       has the farm data and the agents behind it. Sending everything to one of
       them would mean either a page question answered from the farm or a farm
       question answered from a screenshot of it. */
    if (window.SaarthiPageContext && ABOUT_THE_PAGE.test(said)) {
      try {
        const turn = await window.Saarthi.api.post('/api/assistant/turn', {
          question: said,
          language: language(),
          pageContext: window.SaarthiPageContext.capture(),
          preferences: { allowLowRiskActions: true, speakResponses: true },
        });
        if (turn.actions && turn.actions.length && window.SaarthiActions) {
          phase('doing');
          await window.SaarthiActions.carryOut(turn.actions);
        }
        state.busy = false;
        await speak(turn.speechText || turn.answer);
        if (state.on && !state.deaf) listen();
        return;
      } catch (err) {
        // Fall through to the farm assistant: a page question it cannot answer
        // is better than no answer.
      }
    }

    let body;
    try {
      body = await window.Saarthi.api.post('/api/chat', {
        message: said,
        language: language(),
        session_id: window.Saarthi.sessionId || undefined,
        channel: 'dashboard',
        speak: true,
        /* Which page is open, so "what does this say" has a subject. Saarthi
           cannot see the screen; it reads the data the screen was drawn from,
           which is the same thing and does not depend on a rendering. */
        page: (window.Saarthi && window.Saarthi.page) || undefined,
        /* The phone line's engine, not the dashboard's.
         *
         * The dashboard chain writes a hundred words meant to be read, and a
         * hundred words read aloud is the better part of a minute - measured
         * here, one price question took 28 seconds end to end. The
         * conversational engine is the one built for speech: it answers in
         * about fourteen spoken words, in roughly 400 ms, and runs the
         * fifteen agents behind the reply so the NEXT turn has their findings.
         * Instructions are carried out the same way either way. */
        conversational: true,
      });
    } catch (err) {
      tell('Could not reach Saarthi just then.');
      state.busy = false;
      listen();
      return;
    }

    // Keep the thread. Without this every turn starts a new conversation and
    // "aur gehu ka?" is answered as though nothing had been asked before it.
    if (body.session_id) window.Saarthi.sessionId = body.session_id;

    // An instruction: carry it out before speaking, so the page has already
    // moved by the time the farmer hears "opening mandi prices".
    if (body.ui) {
      phase('doing');
      if (window.SaarthiWake) window.SaarthiWake.carryOut(body.ui);
      if (body.ui.do === 'stop') {
        await speak(body.reply, body.audio_url);
        stop('told to');
        return;
      }
    }

    await speak(body.reply || '', body.audio_url);

    state.busy = false;
    if (state.on && !state.deaf) listen();
    else if (state.on) phase('idle');
  }

  /**
   * Read a reply out loud and wait until it has finished.
   *
   * The server's own voice first - it is the Indian-language one the phone line
   * uses and it is cached, so the same sentence costs nothing twice. The
   * browser's built-in voice is the fallback, and silence is the fallback to
   * that: a reply nobody could speak is still on screen.
   */
  function speak(text, audioUrl) {
    return new Promise((resolve) => {
      if (!text) return resolve();
      phase('speaking');

      /* Whatever happens, this settles.
       *
       * The loop waits for speaking to finish before listening again, so a
       * promise that never resolves freezes the whole conversation - the
       * farmer talks and nothing answers, for ever. That is not hypothetical:
       * a browser that blocks autoplay fires neither 'ended' nor 'error' on
       * the audio element, and a speechSynthesis utterance for a voice the
       * device does not have can do the same.
       *
       * The cap is generous - roughly how long the words take to say, plus a
       * margin - so it only ever fires when something really has gone wrong.
       */
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(guard);
        resolve();
      };
      const words = String(text).split(/\s+/).length;
      const guard = setTimeout(done, Math.min(1800 + words * 420, 9000));

      if (audioUrl) {
        const audio = new Audio(audioUrl);
        state.audio = audio;
        audio.addEventListener('ended', done, { once: true });
        audio.addEventListener('error', () => browserVoice(text, done), { once: true });
        audio.play().catch(() => browserVoice(text, done));
        return;
      }
      browserVoice(text, done);
    });
  }

  function browserVoice(text, done) {
    if (!window.speechSynthesis || typeof SpeechSynthesisUtterance !== 'function') {
      return done();
    }
    try {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = locale();
      utterance.onend = done;
      utterance.onerror = done;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    } catch (err) {
      done();
    }
  }

  function quiet() {
    if (state.audio) {
      try { state.audio.pause(); } catch (err) { /* gone */ }
      state.audio = null;
    }
    if (window.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (err) { /* gone */ }
    }
  }

  // --------------------------------------------------------------- open / close
  function resetIdle() {
    if (state.idleTimer) clearTimeout(state.idleTimer);
    state.idleTimer = setTimeout(() => {
      if (state.on && !state.busy) stop('quiet');
    }, SILENCE_MS);
  }

  /**
   * Open agent mode. [opening] is whatever was said after the wake word, so
   * "Saarthi, mandi bhav dikhao" is one sentence and not a wake followed by a
   * pause.
   */
  function start(opening) {
    if (!supported()) {
      if (window.Saarthi) window.Saarthi.toast(
        'This browser cannot listen. Chrome on a phone or a computer can.');
      return false;
    }
    // The wake word stands down for as long as the orb has the floor. It does
    // this itself when IT opened agent mode; a tap on the orb is the other way
    // in, and nothing told it. `mic.wanted` is left alone, so `stop` hands the
    // microphone back at the end exactly as before.
    if (window.SaarthiWake && window.SaarthiWake.stop) {
      try { window.SaarthiWake.stop(); } catch (err) { /* not listening */ }
    }
    state.on = true;
    state.busy = false;
    state.deaf = false;
    // The recorder owns the microphone on this path, and two things listening
    // on one device fight. The voiceprint buffer is started only when the
    // browser recogniser is the one listening.
    // A fresh conversation starts with a fresh backoff, or a session that
    // ended in silence would open already half-way to giving up.
    state.quiet = 0;
    orb().classList.add('live');
    /* A parallel capture of the raw audio, which SpeechRecognition never hands
       over - it returns text and keeps the sound to itself. Without it there is
       nothing to compare a voice against, so the gate below would have no
       opinion on every single turn. */
    if (!recorderReady() && window.SaarthiVoiceId
        && window.SaarthiVoiceId.supported()) {
      window.SaarthiVoiceId.start();
    }
    resetIdle();

    if (opening && opening.trim().length > 2) {
      handle(opening.trim());
    } else {
      listen();
    }
    return true;
  }

  function stop(why) {
    state.on = false;
    state.busy = false;
    if (state.idleTimer) clearTimeout(state.idleTimer);
    hush();
    // The microphone goes back when the conversation ends, not when the tab is
    // closed. A recording nobody asked for is the one thing a listening page
    // must never leave running.
    if (window.SaarthiVoiceTurn) window.SaarthiVoiceTurn.close();
    quiet();
    phase('idle');
    orb().classList.remove('live');
    // The microphone goes back when the conversation ends. Holding it open
    // afterwards is a recording nobody asked for.
    if (window.SaarthiVoiceId && window.SaarthiVoiceId.supported()
        && !(window.SaarthiWake && window.SaarthiWake.mic
             && window.SaarthiWake.mic.wanted)) {
      window.SaarthiVoiceId.stop();
    }
    // Hand the microphone back to the wake word, which was paused while agent
    // mode had it - two recognisers on one device fight and both lose.
    if (window.SaarthiWake && window.SaarthiWake.mic
        && window.SaarthiWake.mic.wanted) {
      setTimeout(() => window.SaarthiWake.start(), 600);
    }
    return why;
  }

  function running() {
    return state.on;
  }

  /* The icon is drawn as soon as the page is, because it IS how agent mode is
     found. Nothing listens until it is tapped. */
  if (supported() || recorderReady()) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => phase('idle'), { once: true });
    } else {
      phase('idle');
    }
  }

  /* `speak` is exported so the change watcher can use the same voice and the
     same audio element. Two independent speakers on one page talk over each
     other, and the one that loses is whichever the farmer was listening to. */
  window.SaarthiAgent = { start, stop, running, supported, handle, speak };
})();
