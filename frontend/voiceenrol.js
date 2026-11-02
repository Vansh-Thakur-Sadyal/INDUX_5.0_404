/* Registering a voice, properly, on the page where a farmer's own details live.
 *
 * What this replaced was three unlabelled recordings taken from a rolling
 * buffer with nothing on screen to read, a progress strip that counted clips
 * the server had refused, and a "Thank you, Saarthi will listen for your voice"
 * that appeared whether or not anything had been stored. A farmer could press
 * the button, say nothing useful, be thanked, and end up with a voiceprint that
 * then decided whether they were allowed to speak.
 *
 * The shape of it now
 * -------------------
 *   consent  ->  three prompted sentences  ->  the server decides it is done
 *
 * Each sentence is shown before it is recorded and stays on screen while it is.
 * The server counts the good clips, not this page - trusting a local counter is
 * how a refused recording came to count towards the three. Every refusal names
 * something the farmer can change: too quiet, too close, mostly silence, not
 * one voice.
 *
 * It also offers a test afterwards, which is the only honest way to show
 * somebody what their registration is worth: say something, and see whether
 * Saarthi recognises you.
 */
'use strict';

(function () {
  const RECORD_MS = 3400;          // the clip itself
  const READ_MS = 1400;            // a moment to read the line before it starts

  const state = { status: null, busy: false, active: '' };

  const $ = (id) => document.getElementById(id);

  function supported() {
    return Boolean(window.SaarthiVoiceId && window.SaarthiVoiceId.supported());
  }

  function language() {
    return (window.Saarthi && window.Saarthi.language) || 'en';
  }

  // ------------------------------------------------------------------ painting
  async function refresh() {
    const card = $('vxCard');
    if (!card || !window.Saarthi) return;
    let status;
    try {
      status = await window.Saarthi.api.get(
        '/api/voiceid/status?language=' + encodeURIComponent(language()));
    } catch (err) {
      card.hidden = true;
      return;
    }
    state.status = status;
    card.hidden = false;
    paint(status);
  }

  function esc(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* One voice, in a sentence. The four states a farmer can be in, and
     "started" and "stale" are the two that used to be invisible: a half
     finished registration showed as registered, and a print made by an older
     matcher went on being compared with clips it cannot be compared with. */
  function describe(voice) {
    if (voice.stale) {
      return ['Needs registering again',
        'Registered by an older version of Saarthi, which described a voice '
        + 'differently.'];
    }
    if (voice.registered) {
      return [voice.quality === 'good' ? 'Registered' : 'Registered - fair match',
        voice.quality === 'good'
          ? 'Saarthi knows this voice.'
          : 'The three recordings did not agree closely. Doing it again '
            + 'somewhere quieter would make it surer.'];
    }
    if (!voice.consent) {
      return ['Waiting for agreement',
        'The person whose voice this is has to agree before anything is recorded.'];
    }
    if (voice.clips > 0 && voice.need > 0) {
      return [voice.clips + ' of ' + (voice.clips + voice.need) + ' recorded',
        'Not finished. Saarthi does not use a voice until all three are in and '
        + 'they sound like the same person.'];
    }
    if (voice.clips > 0) {
      return ['Not accepted',
        'All three are in but they did not sound like the same person, so '
        + 'Saarthi has not kept them.'];
    }
    return ['Not registered', 'Nothing recorded yet.'];
  }

  function paint(status) {
    const note = $('vxNote');
    const list = $('vxVoices');
    const voices = status.voices || [];

    const toggle = $('vxAuth');
    if (toggle) toggle.checked = status.auth_enabled !== false;

    note.textContent = voices.length
      ? (status.auth_enabled === false
          ? 'The voice check is off. Nothing registered has been deleted.'
          : 'Saarthi checks it is one of these voices before acting on what is said.')
      : (status.note || '');

    list.innerHTML = voices.map((voice) => {
      const [state_, why] = describe(voice);
      const active = voice.voice_id === state.active;
      return `<div class="vx-voice${active ? ' on' : ''}" data-voice="${esc(voice.voice_id)}">
        <div class="vx-voice-text">
          <b>${esc(voice.label || 'This voice')}</b>
          <small>${esc(state_)} - ${esc(why)}</small>
        </div>
        <div class="btn-row tight">
          <button class="btn ghost sm" type="button" data-vx-record="${esc(voice.voice_id)}">${
            voice.registered || voice.stale ? 'Register again'
              : voice.clips > 0 ? 'Carry on' : 'Record'
          }</button>
          ${voice.registered ? `<button class="btn ghost sm" type="button" data-vx-test="${esc(voice.voice_id)}">Test</button>` : ''}
          <button class="btn ghost sm" type="button" data-vx-rename="${esc(voice.voice_id)}">Rename</button>
          <button class="btn ghost sm danger" type="button" data-vx-forget="${esc(voice.voice_id)}">Remove</button>
        </div>
      </div>`;
    }).join('') || '<p class="hint">No voice is registered on this account.</p>';

    // The top button only has a job when there is nothing to carry on with;
    // once a voice exists, each row has its own.
    $('vxStart').hidden = voices.length > 0;
    const add = $('vxAdd');
    if (add) {
      add.hidden = voices.length === 0 || (status.room_for_more || 0) <= 0;
      add.textContent = 'Add another voice';
    }

    /* What is doing the comparing, said plainly.
     *
     * A farmer asking "will this tell me apart from my brother" deserves the
     * real answer, and the real answer differs: a trained speaker model
     * separates two men of the same age, and the spectral fallback does not
     * do it nearly as well. Naming it is also how a server that is missing
     * the model file becomes visible instead of just feeling worse. */
    const how = status.describer === 'ecapa-tdnn'
      ? 'Matched with a trained speaker model (ECAPA-TDNN), which separates '
        + 'two voices of a similar age.'
      : status.describer === 'cepstral'
        ? 'Matched on the shape of your voice. Coarser than the trained model '
          + '- it tells a man from a woman reliably, two similar voices less so.'
        : '';
    $('vxExplain').textContent = [
      voices.length > 1
        ? 'Each person on this account has their own registration and agreed to '
          + 'it themselves.' : '',
      how,
    ].filter(Boolean).join(' ');
  }

  /* The three dots and the sentence to read. The sentence is the part that was
     missing: a farmer given nothing to say says "hello" twice and stops, and
     three clips of one word describe the word rather than the speaker. */
  function step(done, total, say, phase) {
    const host = $('vxSteps');
    host.hidden = false;
    host.innerHTML = '';
    const dots = document.createElement('div');
    dots.className = 'vx-dots';
    for (let i = 0; i < total; i += 1) {
      const dot = document.createElement('span');
      dot.className = 'vx-dot' + (i < done ? ' done' : i === done ? ' live' : '');
      dots.appendChild(dot);
    }
    host.appendChild(dots);

    if (say) {
      const line = document.createElement('p');
      line.className = 'vx-say';
      line.textContent = '“' + say + '”';
      host.appendChild(line);
    }
    const label = document.createElement('p');
    label.className = 'vx-phase';
    label.textContent = phase || '';
    host.appendChild(label);
  }

  function tell(message, bad) {
    const line = $('vxResult');
    line.textContent = message || '';
    line.hidden = !message;
    line.classList.toggle('bad', Boolean(bad));
  }

  // ---------------------------------------------------------------- consent
  async function consent(voice) {
    const status = state.status || {};
    const target = voice || {};
    if (target.consent) return true;
    if (!voice && status.consent) return true;
    const agreed = window.confirm(
      'May Saarthi remember your voice?\n\n' + (status.note || '')
      + '\n\nYou can remove it at any time.');
    try {
      const body = await window.Saarthi.api.post('/api/voiceid/consent',
        { agreed, language: language(), voice_id: target.voice_id || '' });
      if (agreed && body.voice_id) state.active = body.voice_id;
      if (!agreed) {
        await refresh();
        tell(body.spoken || 'Nothing was stored.');
        return false;
      }
    } catch (err) {
      tell('Could not save that: ' + err.message, true);
      return false;
    }
    return true;
  }

  // -------------------------------------------------------------- recording
  async function recordOne(say, index, total) {
    step(index, total, say, 'Read it in a moment...');
    await new Promise((r) => setTimeout(r, READ_MS));
    step(index, total, say, 'Recording - speak now');
    await new Promise((r) => setTimeout(r, RECORD_MS));

    const samples = window.SaarthiVoiceId.recent(RECORD_MS / 1000);
    if (!samples) return { ok: false, detail: 'That was too quiet. Hold the phone closer.' };

    const form = new FormData();
    form.append('audio', window.SaarthiVoiceId.toWav(samples, 16000), 'enrol.wav');
    form.append('language', language());
    form.append('voice_id', state.active || '');
    step(index, total, say, 'Checking that recording...');
    try {
      const response = await fetch('/api/voiceid/enrol',
        { method: 'POST', credentials: 'include', body: form });
      const body = await response.json().catch(() => ({}));
      if (response.status === 403) return { ok: false, detail: body.detail || 'Consent is needed first.' };
      return body;
    } catch (err) {
      return { ok: false, detail: 'Could not reach Saarthi just then.' };
    }
  }

  function voiceById(id) {
    return ((state.status || {}).voices || []).find((v) => v.voice_id === id) || null;
  }

  async function register(voiceId) {
    if (state.busy) return;
    state.active = voiceId || state.active
      || ((state.status || {}).voice_id || '');
    if (!supported()) {
      tell('This browser cannot record. Chrome on a phone or a computer can.', true);
      return;
    }
    if (!(await consent(voiceById(state.active)))) return;

    state.busy = true;
    $('vxStart').disabled = true;
    tell('');
    try {
      const started = await window.SaarthiVoiceId.start();
      if (!started) {
        tell('Saarthi needs the microphone to learn your voice.', true);
        return;
      }
      let status = state.status || {};
      const total = status.clips_wanted || 3;
      // The ROW being recorded decides how many clips are already in, not
      // the account: with two voices registered, the account's own counters
      // belong to whichever one the server happened to pick.
      const mine = voiceById(state.active) || status;

      /* "Register again" has to START AGAIN, server-side.
       *
       * It did not, and the result is the state in the bug report: a farmer
       * with three clips on file pressed Register again, `done` began at three,
       * the recording loop's condition was false before it ran once, and they
       * were told "Not registered yet - 3 of 3 recordings are good. Press
       * register to carry on." Pressing it again did exactly nothing, for ever.
       */
      if ((mine.clips || 0) >= total || mine.registered || mine.stale) {
        try {
          const fresh = await window.Saarthi.api.post('/api/voiceid/restart',
            { voice_id: state.active || '' });
          state.status = status = { ...status, ...fresh };
          paint(status);
        } catch (err) {
          tell('Could not start again just then: ' + err.message, true);
          return;
        }
      }

      const lines = status.prompts || [];
      let done = (voiceById(state.active) || {}).clips || 0;
      let finished = false;

      // Attempts are capped so a microphone that is simply not working cannot
      // loop forever - three tries per clip is enough to fix a fixable problem.
      for (let attempt = 0; done < total && attempt < total * 3; attempt += 1) {
        const body = await recordOne(lines[done] || 'Say your name and your village.',
                                     done, total);
        if (typeof body.clips === 'number') done = body.clips;
        finished = Boolean(body.done);
        if (!body.ok) {
          tell(body.detail || 'That recording could not be used. Try again.', true);
          continue;
        }
        tell(body.detail || 'Good.');
        if (finished) break;
      }

      step(done, total, '', finished ? 'Done' : 'Not finished');
      /* Three messages, not two. "3 of 3 recordings are good" was being shown
         for a registration that had just been refused, which reads as a fault
         in the page rather than as something the farmer can act on. */
      tell(finished
        ? 'Registered. Saarthi will listen for your voice.'
        : done >= total
          ? 'The three recordings did not sound like the same person, so they '
            + 'were not kept. Somewhere quieter, with one person reading all '
            + 'three, usually fixes it.'
          : 'Not registered yet - ' + done + ' of ' + total
            + ' recordings are good. Press register to carry on.', !finished);
    } finally {
      window.SaarthiVoiceId.stop();
      state.busy = false;
      $('vxStart').disabled = false;
      await refresh();
    }
  }

  // ------------------------------------------------------------------- test
  /* The only honest way to show what a registration is worth: say something and
     see whether Saarthi recognises you. */
  async function test(voiceId) {
    if (state.busy || !supported()) return;
    state.busy = true;
    state.active = voiceId || state.active;
    tell('');
    try {
      const started = await window.SaarthiVoiceId.start();
      if (!started) { tell('Saarthi needs the microphone.', true); return; }
      step(0, 1, 'Say anything - a sentence is enough.', 'Recording...');
      await new Promise((r) => setTimeout(r, RECORD_MS));
      const samples = window.SaarthiVoiceId.recent(RECORD_MS / 1000);
      if (!samples) { tell('That was too quiet to judge.', true); return; }

      const form = new FormData();
      form.append('audio', window.SaarthiVoiceId.toWav(samples, 16000), 'test.wav');
      const response = await fetch('/api/voiceid/check',
        { method: 'POST', credentials: 'include', body: form });
      const body = await response.json().catch(() => ({}));

      // A clip is checked against EVERY voice on the account, so the answer
      // names the one it matched - the useful thing to know on a farm where
      // two people are registered.
      const who = body.label ? (' It matched "' + body.label + '".') : '';
      const said = {
        strong: 'That sounded like a registered voice.' + who,
        weak: 'That might have been a registered voice - Saarthi was not sure.' + who,
        no: 'That did not sound like any voice registered here.',
        unknown: 'Saarthi could not tell from that clip.',
      }[body.match] || 'Saarthi could not tell.';
      tell(said, body.match === 'no');
      step(1, 1, '', '');
    } finally {
      window.SaarthiVoiceId.stop();
      state.busy = false;
    }
  }

  async function forget(voiceId) {
    const voice = voiceById(voiceId);
    const who = voice && voice.label ? ('"' + voice.label + '"') : 'this voice';
    if (!window.confirm('Remove ' + who + ' from Saarthi?')) return;
    try {
      await window.Saarthi.api.del('/api/voiceid?voice_id='
        + encodeURIComponent(voiceId || ''));
      tell('Removed. Saarthi works the same without it.');
      $('vxSteps').hidden = true;
      if (state.active === voiceId) state.active = '';
    } catch (err) {
      tell('Could not remove it: ' + err.message, true);
    }
    await refresh();
  }

  // ------------------------------------------------------------ more voices
  /* A farm is worked by more than one person, and the first version stored one
     voiceprint per phone number - so a father and son sharing an account had to
     choose which of them Saarthi would recognise. */
  async function addVoice() {
    const count = ((state.status || {}).voices || []).length;
    const label = window.prompt(
      'What should this voice be called?\n\n'
      + 'The person it belongs to will be asked to agree before anything is '
      + 'recorded.', 'Owner ' + (count + 1));
    if (label === null) return;
    try {
      const body = await window.Saarthi.api.post('/api/voiceid/voices', { label });
      state.active = body.voice_id || '';
      state.status = body;
      paint(body);
      tell('Added. Press Record on that row when the person is ready.');
    } catch (err) {
      tell('Could not add it: ' + err.message, true);
    }
  }

  async function renameVoice(voiceId) {
    const voice = voiceById(voiceId);
    const label = window.prompt('What should this voice be called?',
                                (voice && voice.label) || '');
    if (label === null) return;
    try {
      await window.Saarthi.api.patch('/api/voiceid/voices/'
        + encodeURIComponent(voiceId), { label });
    } catch (err) {
      tell('Could not rename it: ' + err.message, true);
    }
    await refresh();
  }

  /* The switch. It turns the CHECK off, not the registration: nothing is
     deleted, so turning it back on costs nothing. */
  async function setAuth(on) {
    try {
      const body = await window.Saarthi.api.post('/api/voiceid/settings',
                                                 { enabled: on });
      state.status = { ...(state.status || {}), ...body };
      paint(state.status);
      tell(body.note || '');
    } catch (err) {
      tell('Could not change that: ' + err.message, true);
      await refresh();
    }
  }

  function wire() {
    if (!$('vxCard')) return;
    $('vxStart').addEventListener('click', () => register(''));
    const add = $('vxAdd');
    if (add) add.addEventListener('click', addVoice);
    const toggle = $('vxAuth');
    if (toggle) toggle.addEventListener('change', (e) => setAuth(e.target.checked));

    /* One listener for the whole list. The rows are redrawn on every refresh,
       and a listener per button would be a listener per redraw. */
    $('vxVoices').addEventListener('click', (e) => {
      const record = e.target.closest('[data-vx-record]');
      if (record) { register(record.dataset.vxRecord); return; }
      const check = e.target.closest('[data-vx-test]');
      if (check) { test(check.dataset.vxTest); return; }
      const name = e.target.closest('[data-vx-rename]');
      if (name) { renameVoice(name.dataset.vxRename); return; }
      const remove = e.target.closest('[data-vx-forget]');
      if (remove) forget(remove.dataset.vxForget);
    });
  }

  window.SaarthiVoiceEnrol = { refresh, wire, supported };
})();
