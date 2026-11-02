/* "Hey Saarthi" in the browser, and the instructions that come back.
 *
 * Two jobs that belong together because they are the two halves of talking to
 * the dashboard rather than typing at it:
 *
 *   WakeWord   listens for the name and opens the assistant, listening
 *   Commands   polls for instructions a spoken turn produced - including one
 *              spoken into a phone call, with this page open on a laptop
 *
 * What this is honest about
 * -------------------------
 * The browser's speech recognition is Chrome's, and on desktop Chrome it sends
 * audio to Google to be transcribed. That is a real privacy cost and it is the
 * farmer's to accept, so listening is OFF until switched on, the switch is
 * remembered per browser, and there is a visible indicator the whole time it is
 * on. Firefox and most in-app browsers have no SpeechRecognition at all, and
 * the control says so rather than appearing broken.
 *
 * It also stops when the tab is hidden. A page listening to a room nobody is
 * looking at is not a feature.
 */
'use strict';

(function () {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const STORAGE_KEY = 'saarthi.wake';
  const POLL_IDLE = 4000;
  const POLL_ACTIVE = 1500;

  /* The name, as a microphone mishears it. "Saarthi" comes back as "sarathi",
     "sarthi", "saathi" and "sorry the" often enough to be worth listing; a
     wake word nobody can trigger is worse than one that occasionally over-
     triggers, because the cost of a false wake is a visible panel the farmer
     closes. */
  const WAKE = [
    'saarthi', 'sarthi', 'sarathi', 'saarathi', 'sarati', 'saathi', 'sathi',
    'hey saarthi', 'hey sarthi', 'he saarthi', 'arre saarthi',
    'सारथी', 'सारथि', 'हे सारथी',
  ];

  const mic = {
    recognition: null,
    listening: false,
    wanted: false,
    speaking: false,       // Saarthi is talking: do not wake on our own voice
    pollTimer: null,
    lastHeard: '',
  };

  const $ = (id) => document.getElementById(id);

  function supported() {
    return Boolean(SR);
  }

  function heardTheName(text) {
    const body = ' ' + String(text || '').toLowerCase().replace(/[.,!?]/g, '') + ' ';
    return WAKE.some((name) => body.includes(' ' + name + ' ')
      || body.startsWith(name + ' ') || body.trim() === name);
  }

  /* What was said AFTER the name, so "saarthi mandi bhav dikhao" is one
     sentence and not a wake followed by silence. */
  function commandAfterName(text) {
    let body = String(text || '').toLowerCase().replace(/[.,!?]/g, ' ');
    for (const name of WAKE.slice().sort((a, b) => b.length - a.length)) {
      const at = body.indexOf(name);
      if (at >= 0) {
        body = body.slice(at + name.length);
        break;
      }
    }
    return body.trim();
  }

  // ------------------------------------------------------------- the listener
  function build() {
    if (!supported()) return null;
    const recognition = new SR();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.maxAlternatives = 2;
    // The farmer's language, so "सारथी" is heard as a word rather than as noise.
    // app.js keeps its internals in a closure and names what it exports on
    // window.Saarthi. Guarded because wake.js may load before boot() finishes.
    const chosen = (window.Saarthi && window.Saarthi.language) || 'hi';
    const LOCALES = {
      en: 'en-IN', hi: 'hi-IN', bn: 'bn-IN', ta: 'ta-IN', te: 'te-IN', mr: 'mr-IN',
      gu: 'gu-IN', kn: 'kn-IN', ml: 'ml-IN', pa: 'pa-IN', or: 'or-IN', as: 'as-IN',
      ur: 'ur-IN',
    };
    recognition.lang = LOCALES[chosen] || 'hi-IN';

    recognition.addEventListener('result', (event) => {
      if (mic.speaking) return;
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        for (let a = 0; a < result.length; a += 1) {
          const text = result[a].transcript || '';
          if (!heardTheName(text)) continue;
          mic.lastHeard = text.trim();
          // wake() is async now - it may ask the server whose voice that
          // was. Nothing here waits for it; the listener must return at once
          // or the recogniser's own event queue backs up.
          wake(commandAfterName(text)).catch(() => { mic.speaking = false; });
          return;
        }
      }
    });

    recognition.addEventListener('end', () => {
      // Chrome stops the recogniser on its own every minute or so, and after
      // every result. Restarting is how "always listening" is actually built.
      mic.listening = false;
      if (mic.wanted && !document.hidden) {
        setTimeout(start, 400);
      }
      paint();
    });

    recognition.addEventListener('error', (event) => {
      mic.listening = false;
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        mic.wanted = false;
        remember(false);
        note('Saarthi needs permission to use the microphone. Allow it in the '
             + 'address bar, then switch listening on again.');
      } else if (event.error === 'no-speech' || event.error === 'aborted') {
        // Ordinary. The end handler restarts.
      } else {
        note('Listening stopped: ' + event.error);
      }
      paint();
    });

    return recognition;
  }

  function start() {
    if (!mic.wanted || mic.listening || document.hidden) return;
    // Agent mode is using the microphone. It calls back here when it closes.
    if (window.SaarthiAgent && window.SaarthiAgent.running()) return;
    if (!mic.recognition) mic.recognition = build();
    if (!mic.recognition) return;
    // The parallel capture, so the wake word can be checked against the
    // registered voice. Shares the one microphone the browser hands out.
    if (mic.voice && mic.voice.registered && window.SaarthiVoiceId) {
      window.SaarthiVoiceId.start();
    }
    try {
      mic.recognition.start();
      mic.listening = true;
    } catch (err) {
      // start() throws if it is already running. Nothing to do.
      mic.listening = true;
    }
    paint();
  }

  function stop() {
    mic.listening = false;
    if (mic.recognition) {
      try { mic.recognition.stop(); } catch (err) { /* already stopped */ }
    }
    if (window.SaarthiVoiceId) window.SaarthiVoiceId.stop();
    paint();
  }

  function remember(on) {
    try { localStorage.setItem(STORAGE_KEY, on ? '1' : '0'); } catch (err) { /* private */ }
  }

  function remembered() {
    try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch (err) { return false; }
  }

  function note(message) {
    if (window.Saarthi && window.Saarthi.toast) window.Saarthi.toast(message);
    else console.info('[wake]', message);
  }

  /* Whether this account has registered a voice, and therefore whether the
     wake word should be fussy about whose it is. Re-read when the farmer
     enrols, so turning it on does not need a reload. */
  async function loadVoiceStatus() {
    if (!window.Saarthi) return;
    try {
      mic.voice = await window.Saarthi.api.get('/api/voiceid/status');
    } catch (err) {
      mic.voice = null;
    }
    paint();
  }

  // --------------------------------------------------------------- waking up
  async function wake(spokenCommand) {
    // Stop listening while the assistant has the floor, or the reply read aloud
    // wakes it again and the two talk to each other.
    mic.speaking = true;
    stop();
    paint();

    /* Was that the farmer? Only asked when they have registered a voice - and
       only a clear "no" stops the wake. "unknown" means the check could not
       tell, which happens constantly (short clip, noise, nobody enrolled), and
       treating it as a refusal would make the feature stop working for the
       people who set it up most carefully. */
    if (mic.voice && mic.voice.registered && window.SaarthiVoiceId) {
      const who = await window.SaarthiVoiceId.whoSpoke(3);
      if (who.match === 'no') {
        note('That did not sound like you, so I stayed quiet.');
        mic.speaking = false;
        if (mic.wanted) start();
        return;
      }
    }

    /* Hand over to agent mode, which talks to the API directly.
     *
     * This used to type the words into the chat box and click the button whose
     * text was "Send". The dashboard translates its own interface, so for a
     * farmer using Hindi that button is called something else, the click never
     * landed, and the microphone appeared to listen and do nothing at all.
     * Nothing here now depends on a label, a page or a button.
     *
     * The wake word stays quiet while the agent has the floor: two recognisers
     * on one microphone fight, and both lose. Agent mode hands it back when it
     * closes. */
    if (window.SaarthiAgent && window.SaarthiAgent.supported()) {
      const opened = window.SaarthiAgent.start(spokenCommand);
      if (opened) return;                 // mic.speaking stays true until it closes
    }

    // No agent mode in this browser. Fall back to the assistant page, where
    // the farmer can at least see what was heard and press send themselves.
    if (window.Saarthi) window.Saarthi.go('assistant');
    const input = $('chatInput');
    if (input) {
      input.focus();
      if (spokenCommand && spokenCommand.length > 2) input.value = spokenCommand;
    }
    note(spokenCommand ? 'Heard: ' + spokenCommand : 'Haan ji, boliye.');
    setTimeout(() => {
      mic.speaking = false;
      if (mic.wanted) start();
    }, spokenCommand ? 6000 : 2500);
  }

  // ----------------------------------------------------- instructions arriving
  async function poll() {
    let found = 0;
    // The dashboard may still be booting. Wait for its bridge rather than
    // giving up - an early return here used to end the poll loop permanently,
    // so a command queued a minute later was never collected.
    if (!window.Saarthi || !window.Saarthi.api) {
      mic.pollTimer = setTimeout(poll, 1000);
      return;
    }
    try {
      const body = await window.Saarthi.api.get('/api/voice/commands');
      (body.commands || []).forEach((command) => {
        found += 1;
        carryOut(command);
      });
    } catch (err) {
      // Signed out, or offline. Either way there is nothing to do and nothing
      // worth saying - the next poll will find out.
    }
    mic.pollTimer = setTimeout(poll, found ? POLL_ACTIVE : POLL_IDLE);
  }

  /* Only the instructions this page knows. A command it does not recognise is
     ignored rather than guessed at - the server validates too, and two checks
     on something that moves a farmer's screen is the right number. */
  function carryOut(command) {
    switch (command.do) {
      case 'navigate':
        if (window.Saarthi && command.page) {
          window.Saarthi.go(command.page);
          note(command.spoken || ('Opened ' + (command.label || command.page) + '.'));
        }
        break;
      case 'run_pipeline': {
        const button = $('runPipelineTop') || $('runPipelineBtn')
          || document.querySelector('[data-run-pipeline]');
        if (button) { button.click(); note('Running the full check.'); }
        else if (window.Saarthi) window.Saarthi.go('pipeline');
        break;
      }
      case 'fetch_reading':
        if (window.Saarthi) window.Saarthi.go('devices');
        note('Open the sensor and press Fetch to take a reading.');
        break;
      case 'make_report':
        if (window.Saarthi) window.Saarthi.go('reports');
        break;
      case 'stop':
        stop();
        note('Stopped.');
        break;
      default:
        break;
    }
  }

  // ------------------------------------------------------------------- the UI
  function paint() {
    const button = $('wakeBtn');
    if (!button) return;
    if (!supported()) {
      button.hidden = true;
      return;
    }
    /* Retired from the top bar. The bot in the corner is the one control for
       talking to Saarthi; a second microphone up here only raised the question
       of which of the two to press. The element stays so the wake word keeps
       its state and its title, and so the switch on the Ask page can still
       drive it - it simply is not drawn in the header any more. */
    if (button.hasAttribute('data-retired')) {
      button.hidden = true;
    } else {
      button.hidden = false;
    }
    button.classList.toggle('on', mic.wanted);
    button.setAttribute('aria-pressed', mic.wanted ? 'true' : 'false');
    button.title = mic.wanted
      ? 'Listening for "Saarthi". Click to stop.'
      : 'Click to let Saarthi listen for its name.';
    const dot = button.querySelector('.wake-dot');
    if (dot) dot.classList.toggle('live', mic.wanted && mic.listening);
  }

  function toggle() {
    if (!supported()) {
      note('This browser cannot listen. Chrome on Android or on a computer can.');
      return;
    }
    mic.wanted = !mic.wanted;
    remember(mic.wanted);
    if (mic.wanted) {
      start();
      note('Listening. Say "Saarthi" and then what you want.');
    } else {
      stop();
      note('Stopped listening.');
    }
    paint();
  }

  /* The spoken instructions, as chips the farmer can also tap. Built from the
     server's own list so the help and the parser cannot disagree: if a phrase
     is shown here it is one the parser recognises, because the server builds
     both from the same registry. */
  async function paintSayList() {
    const host = $('sayList');
    if (!host || !window.Saarthi) return;
    let body;
    try {
      body = await window.Saarthi.api.get('/api/voice/actions');
    } catch (err) {
      return;                        // signed out, or offline
    }
    const examples = (body.actions || [])
      .filter((a) => (a.examples || []).length)
      .map((a) => a.examples[0])
      .slice(0, 6);
    if (!examples.length) return;

    host.innerHTML = '';
    const label = document.createElement('b');
    label.textContent = 'You can also say:';
    host.appendChild(label);
    examples.forEach((phrase) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'say-chip';
      chip.textContent = phrase;
      chip.addEventListener('click', () => {
        const input = $('chatInput');
        if (!input) return;
        input.value = phrase;
        const send = [...document.querySelectorAll('#page-assistant button')]
          .find((b) => b.textContent.trim() === 'Send');
        if (send) send.click();
      });
      host.appendChild(chip);
    });
    host.hidden = false;
  }


  /* ------------------------------------------------------------ the voice card
   *
   * Three clips, each a couple of seconds, recorded one at a time with the
   * farmer watching. Consent is asked once, in a plain sentence, BEFORE the
   * microphone is touched - and saying no also deletes anything already held,
   * so changing your mind is one button and not a hunt.
   */
  /* Registering a voice moved to the profile page (voiceenrol.js), where there
     is room to prompt for three sentences, check each one, and offer a test of
     the result. Two places to register one voice meant two flows to keep in
     step, and the one here was the worse of them.

     What stays in this file is the wake word, which is a different job: it
     decides whether Saarthi WAKES, and uses the registered voice through
     `whoSpoke` to decide whether it should. */

  function init() {
    const button = $('wakeBtn');
    if (button) button.addEventListener('click', toggle);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop();
      else if (mic.wanted) start();
    });
    mic.wanted = remembered();
    paint();
    loadVoiceStatus().then(() => { if (mic.wanted) start(); });
    poll();
    /* Agent mode draws its own control - one glowing icon in the corner - so
       there is no second button here to keep in step with it. */

    setTimeout(paintSayList, 1200);
  }

  window.SaarthiWake = { toggle, start, stop, supported, carryOut, heardTheName,
                         commandAfterName, loadVoiceStatus, mic };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
