/* Carrying out exactly one action the server approved.
 *
 * The narrow function the blueprint asks for. It takes a tool name and
 * arguments that have already been validated server-side, resolves the target
 * through the snapshot that issued it, does the one thing, and reports what
 * actually happened.
 *
 * Three rules, and they are the whole file
 * ----------------------------------------
 * **No model output is ever executed.** Not as code, not as a selector, not as
 * a URL. A tool name is looked up in a table defined here; anything not in it
 * does nothing at all.
 *
 * **A target must come from a snapshot.** `pagecontext.js` issues ids and holds
 * the elements they refer to. An id this page never issued resolves to nothing,
 * and an id that has since been re-rendered away resolves to nothing - which is
 * the right answer, because acting on a detached node silently does nothing
 * while looking like success.
 *
 * **Success is observed, never assumed.** Every handler returns what it found,
 * and that goes back to the server. A model told "done" when the button was not
 * there will cheerfully build on it.
 */
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);

  function target(id) {
    return window.SaarthiPageContext
      ? window.SaarthiPageContext.resolve(id)
      : null;
  }

  /* What each approved tool actually does. The only place a tool name becomes
     behaviour, and the reason a tool that is not here cannot do anything. */
  const RUNNERS = {
    get_page_summary(args) {
      const snapshot = window.SaarthiPageContext.capture();
      return { ok: true, detail: snapshot.elements.length + ' elements read',
               summary: window.SaarthiPageContext.asText(snapshot).slice(0, 600) };
    },

    find_element(args) {
      const wanted = String(args.label || '').toLowerCase();
      const snapshot = window.SaarthiPageContext.capture();
      const hits = snapshot.elements.filter((entry) =>
        (entry.label || entry.text || '').toLowerCase().includes(wanted));
      return { ok: hits.length > 0,
               detail: hits.length + ' match(es)',
               // Candidates, with their ids, so an ambiguous ask can be put
               // back to the farmer rather than guessed at.
               candidates: hits.slice(0, 5).map((e) => ({
                 targetId: e.targetId, label: e.label || e.text, type: e.type })) };
    },

    scroll_to(args) {
      const element = target(args.targetId);
      if (!element) return { ok: false, detail: 'that part of the page is not here now' };
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      return { ok: true, detail: 'scrolled to it' };
    },

    navigate(args) {
      if (!window.Saarthi || !window.Saarthi.go) {
        return { ok: false, detail: 'the dashboard is not ready' };
      }
      window.Saarthi.go(args.page);
      return { ok: true, detail: 'opened ' + args.page };
    },

    click_element(args) {
      const element = target(args.targetId);
      if (!element) return { ok: false, detail: 'that control is not on the page now' };
      if (element.disabled) return { ok: false, detail: 'that control is disabled' };
      element.click();
      return { ok: true, detail: 'pressed ' + (element.textContent || '').trim().slice(0, 40) };
    },

    async capture_screenshot(args) {
      if (!window.SaarthiVision) return { ok: false, detail: 'pictures are not available' };
      const element = args.targetId ? target(args.targetId) : null;
      const svg = element
        ? (element.matches('svg') ? element : element.querySelector('svg'))
        : document.querySelector('.page.active svg');
      if (!svg) return { ok: false, detail: 'there is no chart here to picture' };
      const picture = await window.SaarthiVision.fromSvg(svg);
      return { ok: true, detail: picture.size + ' bytes', picture };
    },
  };

  /* Run one decision from the server. A decision that was not allowed is not
     run - this does not re-decide, and it does not ask again. */
  async function run(decision) {
    if (!decision || decision.allowed !== true) {
      return { ok: false, detail: (decision && decision.reason) || 'not allowed' };
    }
    const runner = RUNNERS[decision.tool];
    if (!runner) {
      // The server approved a tool this page cannot do. Reported rather than
      // ignored: the two lists have drifted and somebody should know.
      return { ok: false, detail: 'this page cannot do ' + decision.tool };
    }
    let outcome;
    try {
      outcome = await runner(decision.arguments || {});
    } catch (err) {
      outcome = { ok: false, detail: String(err.message || err).slice(0, 200) };
    }
    report(decision.tool, outcome);
    return outcome;
  }

  /* What really happened, back to the server. Fire and forget: a farmer waiting
     on a log write is a farmer waiting for nothing. */
  function report(tool, outcome) {
    if (!window.Saarthi || !window.Saarthi.api) return;
    window.Saarthi.api.post('/api/assistant/action-result', {
      tool, ok: Boolean(outcome.ok), detail: String(outcome.detail || '').slice(0, 300),
    }).catch(() => { /* the action still happened */ });
  }

  /* A consequential action, shown to the farmer in its own words before it can
     run. The id is the thing being confirmed - not a flag, not a boolean the
     page sets - so a yes applies to this action and no other. */
  async function askThenRun(decision) {
    if (!decision.confirm_id) return run(decision);
    const agreed = window.confirm(
      'Saarthi wants to ' + (decision.consequence || decision.tool) + '.\n\nAllow it?');
    if (!agreed) return { ok: false, detail: 'the farmer said no' };

    let released;
    try {
      released = await window.Saarthi.api.post('/api/assistant/confirm',
                                               { confirm_id: decision.confirm_id });
    } catch (err) {
      return { ok: false, detail: 'that confirmation was not accepted' };
    }
    return run(released);
  }

  /* Everything a turn proposed, in order. Read-only and allowed actions run;
     anything needing a yes asks for one. */
  async function carryOut(actions) {
    const done = [];
    for (const decision of actions || []) {
      done.push(decision.confirm_id ? await askThenRun(decision) : await run(decision));
    }
    return done;
  }

  window.SaarthiActions = { run, carryOut, askThenRun, tools: Object.keys(RUNNERS) };
})();
