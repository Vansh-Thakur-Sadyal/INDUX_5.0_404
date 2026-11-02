/* When to open the microphone again, after a window where nobody spoke.
 *
 * This is one small decision pulled out of agent.js so it can be run without a
 * browser, because getting it wrong is not subtle: it was the "it listens and
 * cancels and again listens and cancels" loop on phones.
 *
 * What happened
 * -------------
 * A non-continuous recogniser gives up after a second or two of silence. On a
 * phone that is constant. The old code restarted it from BOTH the `end`
 * handler and the `error` handler - and `end` always fires after `error` - so
 * one silent window produced two restarts, one `start()` throwing "already
 * started", the orb flicking between listening and idle, and the browser's
 * listening chime going off twice a second.
 *
 * So: one restart path, and a wait that grows while nobody is speaking. The
 * first couple are quick, because a farmer pausing to think should not have to
 * wait for the microphone. After that it backs off, and eventually it stops
 * rather than holding the microphone open in somebody's pocket.
 */
(function (root, make) {
  'use strict';
  var api = make();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SaarthiListen = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* Indexed by how many silent windows have passed in a row. The first two are
     a pause for breath; the last is the one it settles at. */
  var RESTART_MS = [300, 300, 600, 1200, 2000];

  /* About half a minute of silence at the settled rate. After this, listening
     stops and the orb says so - one tap reopens it. */
  var ENOUGH_QUIET = 12;

  function nextDelay(quiet) {
    var at = quiet < 0 ? 0 : quiet;
    return RESTART_MS[at < RESTART_MS.length ? at : RESTART_MS.length - 1];
  }

  function giveUp(quiet) {
    return quiet >= ENOUGH_QUIET;
  }

  /** The running count, and what to do after each window closes. */
  function Backoff() {
    this.quiet = 0;
  }

  /** Somebody spoke. Whatever the backoff had got to, start it over. */
  Backoff.prototype.heard = function heard() {
    this.quiet = 0;
  };

  /** A listening window closed. [spoke] says whether anything was said in it. */
  Backoff.prototype.ended = function ended(spoke) {
    if (spoke) this.quiet = 0;
    else this.quiet += 1;
    if (giveUp(this.quiet)) return { stop: true, wait: 0 };
    return { stop: false, wait: nextDelay(this.quiet) };
  };

  /** A fresh conversation starts with a fresh count. */
  Backoff.prototype.reset = function reset() {
    this.quiet = 0;
  };

  return {
    RESTART_MS: RESTART_MS,
    ENOUGH_QUIET: ENOUGH_QUIET,
    nextDelay: nextDelay,
    giveUp: giveUp,
    Backoff: Backoff,
  };
}));
