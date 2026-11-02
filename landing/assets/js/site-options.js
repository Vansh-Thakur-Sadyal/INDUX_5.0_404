/* ═══ SAARTHI OPTIONS ═════════════════════════════════════════════════════
   These are only the DEFAULTS for a first-time visitor. The two buttons in
   the Menu panel override them and remember the choice on this device.     */
var HERO_VIDEO_LOOP = true;   /* true = play forever   |  false = play once, hold last frame */
var AUTO_FULL_VIEW  = false;  /* true = go full screen |  false = stay in the normal window  */
/* ════════════════════════════════════════════════════════════════════════ */

(function () {
  var KEY_LOOP = 'saarthi.videoLoop';
  var KEY_FULL = 'saarthi.fullView';

  function read(key, fallback) {
    try { var v = localStorage.getItem(key); return v === null ? fallback : v === '1'; }
    catch (e) { return fallback; }                    /* private mode / file:// */
  }
  function write(key, on) {
    try { localStorage.setItem(key, on ? '1' : '0'); } catch (e) {}
  }
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  var loopOn = read(KEY_LOOP, HERO_VIDEO_LOOP);
  var fullOn = read(KEY_FULL, AUTO_FULL_VIEW);

  ready(function () {

    /* ── 1. Hero video ─────────────────────────────────────────────── */
    var video = document.querySelector('.banner-section video');

    function applyLoop() {
      if (!video) return;
      video.loop = loopOn;
      if (loopOn) {
        video.setAttribute('loop', '');
        if (video.paused) { try { video.play(); } catch (e) {} }
      } else {
        video.removeAttribute('loop');
      }
    }
    /* Stop just short of the end rather than on 'ended' - the browser rewinds
       to 0 when that fires, which snaps the hero back to the opening shot. */
    if (video) {
      video.addEventListener('timeupdate', function () {
        var d = video.duration;
        if (!loopOn && d && isFinite(d) && video.currentTime >= d - 0.10) video.pause();
      });
    }
    applyLoop();

    /* ── 2. Full view ──────────────────────────────────────────────────
       A browser only grants fullscreen from a real user gesture, so this
       cannot fire on page load. It fires on the first click/tap/key, and
       the Menu button below is itself a gesture - so that always works.  */
    function goFull() {
      if (document.fullscreenElement) return;
      var el  = document.documentElement;
      var req = el.requestFullscreen || el.webkitRequestFullscreen ||
                el.mozRequestFullScreen || el.msRequestFullscreen;
      if (!req) return;
      try { var p = req.call(el); if (p && p.catch) p.catch(function () {}); }
      catch (e) {}
    }
    function exitFull() {
      if (!document.fullscreenElement) return;
      var ex = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
      if (ex) { try { ex.call(document); } catch (e) {} }
    }

    var EVENTS = ['pointerdown', 'mousedown', 'mouseup', 'click',
                  'keydown', 'touchstart', 'touchend'];
    var armed = false;
    function armOnce() {
      if (armed) return; armed = true;
      function fire() {
        EVENTS.forEach(function (e) { document.removeEventListener(e, fire, true); });
        if (fullOn) goFull();
      }
      EVENTS.forEach(function (e) { document.addEventListener(e, fire, true); });
    }
    if (fullOn) armOnce();

    /* ── 3. The two buttons in the Menu panel ──────────────────────── */
    var loopBtn = document.getElementById('optVideoBtn');
    var fullBtn = document.getElementById('optFullBtn');

    function paint() {
      if (loopBtn) {
        loopBtn.textContent = loopOn ? 'Loop' : 'Play once';
        loopBtn.setAttribute('aria-pressed', loopOn ? 'true' : 'false');
      }
      if (fullBtn) {
        fullBtn.textContent = fullOn ? 'On' : 'Off';
        fullBtn.setAttribute('aria-pressed', fullOn ? 'true' : 'false');
      }
    }
    paint();

    if (loopBtn) loopBtn.addEventListener('click', function (e) {
      e.preventDefault();
      loopOn = !loopOn; write(KEY_LOOP, loopOn); applyLoop(); paint();
    });

    if (fullBtn) fullBtn.addEventListener('click', function (e) {
      e.preventDefault();
      fullOn = !fullOn; write(KEY_FULL, fullOn); paint();
      /* this click IS the user gesture, so it takes effect immediately */
      if (fullOn) goFull(); else exitFull();
    });
  });
})();
