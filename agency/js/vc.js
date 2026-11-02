/* VisionCoders — presentation helpers */
(function () {
    'use strict';

    // Fullscreen is OPT-IN. The page used to grab it on the first click
    // anywhere, which is startling on a site somebody is only reading: the
    // browser chrome vanishes under them and they have to find Esc to get
    // their tabs back. Press F instead.
    function enterFullscreen() {
        var el = document.documentElement;
        var req = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
        if (!req || document.fullscreenElement || document.webkitFullscreenElement) return;
        try {
            var p = req.call(el);
            if (p && typeof p.catch === 'function') p.catch(function () { /* user or policy declined */ });
        } catch (e) { /* not permitted here — leave the page as it is */ }
    }

    // Press F to toggle, for a presentation or a demo.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'f' && e.key !== 'F') return;
        var t = e.target;
        if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;  // let people type "f"
        if (t && t.isContentEditable) return;
        if (document.fullscreenElement) {
            if (document.exitFullscreen) document.exitFullscreen();
        } else {
            enterFullscreen();
        }
    });
})();
