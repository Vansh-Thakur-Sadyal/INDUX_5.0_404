/* Filling a form by saying it.
 *
 * The form this was built for has eleven boxes on it: crop, sowing date, stage,
 * ten symptom checkboxes, free text, spend, expected yield, expected price. A
 * farmer standing in a field with one hand on a phone fills in none of them, so
 * the two agents that need those answers say "missing input" - and the most
 * useful part of the system ends up behind the least usable part of it.
 *
 * Press the microphone, say it all in one breath, and the boxes fill:
 *
 *     "gehu hai, saat November ko boya tha, peele patte dikh rahe hain,
 *      kharcha pandrah hazaar aaya"
 *
 * What it will not do
 * -------------------
 * Fill a box the farmer did not speak to. The server checks every value against
 * the sentence it came from - a number has to have been said, a choice has to
 * be on the list, a date has to parse - and anything that fails is dropped and
 * named. An empty box is honest: the agent then says what it is missing, which
 * is the design. A box quietly filled with a plausible number is not, because
 * nobody re-reads a box that is already full.
 *
 * It works on ANY form, not only that one: it reads whatever `data-ask` fields
 * are on screen and sends their shape with the question.
 */
'use strict';

(function () {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  function supported() {
    return Boolean(SR);
  }

  function language() {
    return (window.Saarthi && window.Saarthi.language) || 'hi';
  }

  /* The form as the server needs to see it: one entry per box, with its label,
     its kind and its choices. Read off the DOM rather than kept in a list here,
     so a field added to the form is fillable the day it is added. */
  function shapeOf(root) {
    const fields = [];
    root.querySelectorAll('[data-ask]').forEach((input) => {
      const key = input.dataset.ask;
      if (!key || fields.some((f) => f.key === key)) return;
      if (input.readOnly || input.disabled) return;      // the crop, when locked

      const group = root.querySelector(`[data-ask-group="${CSS.escape(key)}"]`);
      const wrap = group || input.closest('.ask-field') || input.parentElement;
      const label = wrap && wrap.querySelector('span');
      const boxes = root.querySelectorAll(`input[data-ask-multi="${CSS.escape(key)}"]`);
      const select = root.querySelector(`select[data-ask-select="${CSS.escape(key)}"]`);

      let type = input.type === 'hidden' ? 'text' : input.type;
      let options = [];
      if (boxes.length) {
        type = 'multiselect';
        options = Array.from(boxes).map((b) => b.value);
      } else if (select) {
        type = 'select';
        options = Array.from(select.options)
          .map((o) => o.value)
          .filter((v) => v && !v.startsWith('__'));
      } else if (input.type === 'number') {
        type = 'number';
      } else if (input.type === 'date') {
        type = 'date';
      }

      fields.push({
        key,
        label: (label ? label.textContent : key).trim(),
        type,
        options,
        placeholder: input.placeholder || '',
      });
    });
    return fields;
  }

  /* Put a value into whichever control holds that field, and make it look
     filled - the farmer has to be able to see what was understood and change
     it, so every one of these is a normal edit, not a hidden write. */
  function put(root, key, value) {
    const selector = CSS.escape(key);
    const boxes = root.querySelectorAll(`input[data-ask-multi="${selector}"]`);
    if (boxes.length) {
      const wanted = String(value).split(',').map((v) => v.trim().toLowerCase());
      boxes.forEach((box) => {
        if (wanted.includes(box.value.toLowerCase())) box.checked = true;
      });
      const hidden = root.querySelector(`input[type=hidden][data-ask="${selector}"]`);
      if (hidden) {
        const picked = Array.from(
          root.querySelectorAll(`input[data-ask-multi="${selector}"]:checked`)
        ).map((b) => b.value);
        hidden.value = picked.join(', ');
      }
      flash(boxes[0].closest('.ask-field'));
      return true;
    }

    const select = root.querySelector(`select[data-ask-select="${selector}"]`);
    if (select) {
      const match = Array.from(select.options)
        .find((o) => o.value.toLowerCase() === String(value).toLowerCase());
      if (match) {
        select.value = match.value;
        // The hidden input is what the form actually reads, and it is written
        // here as well as by the change listener. Relying on that listener
        // alone meant the box LOOKED filled and the chain was sent nothing -
        // the worst of the three possible outcomes.
        const hidden = root.querySelector(`input[type=hidden][data-ask="${selector}"]`);
        if (hidden) hidden.value = match.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        flash(select.closest('.ask-field'));
        return true;
      }
    }

    const input = root.querySelector(`[data-ask="${selector}"]`);
    if (!input || input.readOnly) return false;
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    flash(input.closest('.ask-field') || input.parentElement);
    return true;
  }

  /* A box that just filled itself has to say so, or the farmer has no idea
     which of eleven boxes moved. */
  function flash(node) {
    if (!node || !node.classList) return;
    node.classList.remove('ask-filled');
    void node.offsetWidth;                  // restart the animation
    node.classList.add('ask-filled');
    setTimeout(() => node.classList.remove('ask-filled'), 2200);
  }

  function note(root, text, bad) {
    let line = root.parentElement.querySelector('.ask-voice-note');
    if (!line) {
      line = document.createElement('p');
      line.className = 'ask-voice-note';
      root.parentElement.insertBefore(line, root);
    }
    line.textContent = text;
    line.classList.toggle('bad', Boolean(bad));
    line.hidden = !text;
  }

  /* The BCP-47 tag for whichever of the thirteen is in force. Hard-coding
     "en-IN or hi-IN" meant every farmer on one of the other eleven was listened
     to as a Hindi speaker. */
  const LOCALES = {
    en: 'en-IN', hi: 'hi-IN', bn: 'bn-IN', ta: 'ta-IN', te: 'te-IN', mr: 'mr-IN',
    gu: 'gu-IN', kn: 'kn-IN', ml: 'ml-IN', pa: 'pa-IN', or: 'or-IN', as: 'as-IN',
    ur: 'ur-IN',
  };

  // ------------------------------------------------------------------ listening
  function listenOnce(onText, onState) {
    const recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = LOCALES[language()] || 'hi-IN';

    let heard = '';
    recognition.addEventListener('result', (event) => {
      let text = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        text += event.results[i][0].transcript;
      }
      heard = text.trim();
      onState('listening', heard);
    });
    recognition.addEventListener('error', (event) => {
      onState('error', event.error === 'not-allowed'
        ? 'Saarthi needs the microphone. Allow it in the address bar.'
        : '');
    });
    recognition.addEventListener('end', () => {
      if (heard) onText(heard);
      else onState('idle', '');
    });

    try {
      recognition.start();
      onState('listening', '');
    } catch (err) {
      onState('idle', '');
    }
    return recognition;
  }

  // --------------------------------------------------------------------- wiring
  function attach(button, grid) {
    if (!button || !grid || button.dataset.voiceWired === '1') return;
    button.dataset.voiceWired = '1';
    if (!supported()) {
      button.hidden = true;
      return;
    }
    button.hidden = false;

    let recognition = null;
    const setState = (state, text) => {
      button.dataset.state = state;
      if (state === 'listening') {
        note(grid, text || 'Listening - say what you can, all in one go.');
      } else if (state === 'thinking') {
        note(grid, 'Working out which box each thing goes in...');
      } else if (state === 'error' && text) {
        note(grid, text, true);
      }
    };

    button.addEventListener('click', async () => {
      if (button.dataset.state === 'listening') {
        if (recognition) { try { recognition.stop(); } catch (e) { /* done */ } }
        return;
      }
      recognition = listenOnce(async (said) => {
        setState('thinking');
        let body;
        try {
          body = await window.Saarthi.api.post('/api/pipeline/fill-inputs', {
            said,
            language: language(),
            fields: shapeOf(grid),
          });
        } catch (err) {
          setState('idle');
          note(grid, 'Could not reach Saarthi just then.', true);
          return;
        }
        setState('idle');

        const filled = Object.keys(body.values || {})
          .filter((key) => put(grid, key, body.values[key]));
        const lines = [];
        lines.push(filled.length
          ? `Heard: "${body.heard}" - filled ${filled.length} box${filled.length > 1 ? 'es' : ''}.`
          : `Heard: "${body.heard}" - nothing in that matched a box.`);
        (body.notes || []).forEach((n) => lines.push(n));
        note(grid, lines.join(' '), !filled.length);
      }, setState);
    });
  }

  window.SaarthiFormVoice = { attach, shapeOf, put, supported };
})();
