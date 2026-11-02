/* Asking Saarthi about something it has to SEE.
 *
 * Most questions about a page are answered better from its text: the exact
 * figure is written on it, and reading it is faster, cheaper and cannot be
 * misread off the height of a bar. This is for the rest - "why is this line
 * going down", "which bar is the tall one", "what does this picture show" -
 * where the answer is in a shape rather than in a number.
 *
 * What a web page can actually capture
 * ------------------------------------
 * Not whatever it likes, and the honest version of this feature says so. There
 * are two paths here and no third:
 *
 * **Draw it ourselves.** The charts on this dashboard are SVG, and an SVG can
 * be serialised and painted onto a canvas without any permission at all. This
 * is the path used for a chart, and it is exact - it is the same drawing the
 * farmer is looking at.
 *
 * **Ask the browser to share the screen.** `getDisplayMedia` shows the
 * browser's own picker, the farmer chooses what to share, and one frame is
 * taken. It needs a deliberate yes every time, which is the correct cost for
 * sending a picture of somebody's screen to a model.
 *
 * A page cannot silently screenshot itself, other tabs, or anything
 * cross-origin, and nothing here pretends otherwise. The DOM text goes with
 * the picture either way, so the model reads figures from the page rather than
 * guessing them off a shape.
 */
'use strict';

(function () {
  /* Big enough to read a chart, small enough to send over a field connection
     and to stay inside the server's limit. */
  const MAX_EDGE = 1280;
  const JPEG_QUALITY = 0.82;

  const $ = (id) => document.getElementById(id);

  function language() {
    return (window.Saarthi && window.Saarthi.language) || 'en';
  }

  // --------------------------------------------------------- drawing an SVG
  /* An SVG on the page, painted onto a canvas. No permission, no picker, and
     the result is exactly what is on screen rather than a re-render. */
  function fromSvg(svg) {
    return new Promise((resolve, reject) => {
      const box = svg.getBoundingClientRect();
      const width = Math.max(1, Math.round(box.width || 600));
      const height = Math.max(1, Math.round(box.height || 300));

      const copy = svg.cloneNode(true);
      copy.setAttribute('width', width);
      copy.setAttribute('height', height);
      // The page's colours live in CSS variables that mean nothing once the
      // SVG is detached, so the computed values are baked in.
      const style = getComputedStyle(svg);
      copy.style.background = style.backgroundColor === 'rgba(0, 0, 0, 0)'
        ? getComputedStyle(document.body).backgroundColor : style.backgroundColor;

      const markup = new XMLSerializer().serializeToString(copy);
      const blob = new Blob([markup], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);

      const image = new Image();
      image.onload = () => {
        const canvas = document.createElement('canvas');
        const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        const context = canvas.getContext('2d');
        context.fillStyle = getComputedStyle(document.body).backgroundColor || '#fff';
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob((out) => out ? resolve(out) : reject(new Error('could not draw it')),
                      'image/jpeg', JPEG_QUALITY);
      };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('could not draw it')); };
      image.src = url;
    });
  }

  // ------------------------------------------------------ sharing the screen
  /* One frame, with the farmer choosing what is shared in the browser's own
     picker. The track is stopped immediately: this takes a picture, it does
     not watch. */
  async function fromScreen() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
      throw new Error('This browser cannot share the screen.');
    }
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { width: { max: MAX_EDGE } }, audio: false,
    });
    try {
      const video = document.createElement('video');
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      // One frame, once there is one to take.
      await new Promise((r) => setTimeout(r, 220));

      const canvas = document.createElement('canvas');
      const scale = Math.min(1, MAX_EDGE / Math.max(video.videoWidth, video.videoHeight));
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      return await new Promise((resolve, reject) =>
        canvas.toBlob((out) => out ? resolve(out) : reject(new Error('could not take the picture')),
                      'image/jpeg', JPEG_QUALITY));
    } finally {
      stream.getTracks().forEach((track) => track.stop());
    }
  }

  // ------------------------------------------------------------------ asking
  /* The text around the picture goes with it. This is what stops the model
     reading a value off the height of a bar: if the number is written on the
     page, it is in the evidence, and the prompt tells it to prefer that. */
  function contextAround(element) {
    const card = element && element.closest
      ? element.closest('article.card, section.page') : null;
    const host = card || document.querySelector('.page.active') || document.body;
    const copy = host.cloneNode(true);
    copy.querySelectorAll('[data-assistant-ignore], script, style').forEach((n) => n.remove());
    const parts = [];
    const walk = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT);
    for (let node = walk.nextNode(); node; node = walk.nextNode()) {
      const text = node.nodeValue.replace(/\s+/g, ' ').trim();
      if (text) parts.push(text);
    }
    return parts.join(' ').slice(0, 1500);
  }

  async function ask(picture, question, element) {
    const form = new FormData();
    form.append('image', picture, 'screen.jpg');
    form.append('question', question || 'What does this show?');
    form.append('language', language());
    form.append('context', contextAround(element));

    const response = await fetch('/api/assistant/vision',
                                 { method: 'POST', credentials: 'include', body: form });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || 'The picture could not be read.');
    return body;
  }

  /* The whole thing: find something to look at, capture it, ask, speak the
     answer. `what` is a CSS selector or an element; without one it falls back
     to sharing the screen, because there is nothing on the page we are allowed
     to draw by ourselves. */
  async function look(question, what) {
    const target = typeof what === 'string' ? document.querySelector(what) : what;
    let picture;
    let how;
    const svg = target
      ? (target.matches && target.matches('svg') ? target : target.querySelector('svg'))
      : document.querySelector('.page.active svg');

    if (svg) {
      picture = await fromSvg(svg);
      how = 'chart';
    } else {
      picture = await fromScreen();
      how = 'screen';
    }
    const body = await ask(picture, question, target || svg);
    return { ...body, how };
  }

  window.SaarthiVision = { look, fromSvg, fromScreen, ask, contextAround,
                           canDrawCharts: () => Boolean(document.querySelector('svg')),
                           canShareScreen: () => Boolean(navigator.mediaDevices
                             && navigator.mediaDevices.getDisplayMedia) };
})();
