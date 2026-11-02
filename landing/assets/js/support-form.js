/* VisionCoders — contact form -> n8n "Agri_Support_Desk" support desk.
 *
 * Posts to a SAME-ORIGIN endpoint that nginx proxies through to the n8n
 * webhook, so there is no CORS round trip and the n8n hostname never appears
 * in the page source.
 *
 * The workflow answers with JSON and a meaningful status code:
 *   200  { ok:true,  ticket, message }  query accepted, ack email on its way
 *   400  { ok:false, error }            bad email or question under 5 chars
 *   429  { ok:false, ticket, error }    this address already has a query open
 */
(function () {
  'use strict';

  var ENDPOINT = '/api/support-query';
  var EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;   // same test the workflow uses

  function statusBox(form) {
    var el = form.querySelector('.support-status');
    if (!el) {
      el = document.createElement('p');
      el.className = 'support-status';
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      form.appendChild(el);
    }
    return el;
  }

  function show(form, kind, text) {
    var el = statusBox(form);
    el.className = 'support-status is-' + kind;
    el.textContent = text;
  }

  function clear(form) {
    var el = form.querySelector('.support-status');
    if (el) { el.className = 'support-status'; el.textContent = ''; }
  }

  function wire(form) {
    var btn = form.querySelector('button[type="submit"]');
    var label = btn && btn.querySelector('.btn-text');
    var idle = label ? label.textContent : '';
    var busy = false;

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      if (busy) return;

      var name = (form.querySelector('[name="name"]') || {}).value || '';
      var email = (form.querySelector('[name="email"]') || {}).value || '';
      var message = (form.querySelector('[name="message"]') || {}).value || '';

      name = name.trim();
      email = email.trim();
      message = message.trim();

      // Validate here as well as server-side, so a typo costs no round trip.
      if (!EMAIL_RE.test(email)) {
        show(form, 'error', 'Please enter a valid email address so we can send the answer.');
        return;
      }
      if (message.length < 5) {
        show(form, 'error', 'Please tell us a little more — at least a few words.');
        return;
      }

      busy = true;
      if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
      if (label) label.textContent = 'Sending…';
      show(form, 'pending', 'Sending your question…');

      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify({ name: name, email: email, message: message })
      })
        .then(function (res) {
          return res.json()
            .catch(function () { return {}; })          // non-JSON (proxy/gateway error)
            .then(function (data) { return { status: res.status, data: data }; });
        })
        .then(function (r) {
          var d = r.data || {};
          if (r.status === 200 && d.ok) {
            show(form, 'success',
              (d.message || 'Thanks — we have your question.') +
              (d.ticket ? ' Your reference is ' + d.ticket + '.' : ''));
            form.reset();
          } else if (d.error) {
            // 400 and 429 both carry a sentence written for the visitor.
            show(form, 'error', d.error);
          } else if (r.status === 404) {
            show(form, 'error', 'The support desk is not accepting messages right now. Please email us directly.');
          } else {
            show(form, 'error', 'Something went wrong sending your question. Please try again in a moment.');
          }
        })
        .catch(function () {
          show(form, 'error', 'We could not reach the support desk. Please check your connection and try again.');
        })
        .then(function () {
          busy = false;
          if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
          if (label) label.textContent = idle;
        });
    });

    // Clear a stale message as soon as the visitor starts editing again.
    ['input', 'change'].forEach(function (evt) {
      form.addEventListener(evt, function () {
        var el = form.querySelector('.support-status');
        if (el && el.textContent && !busy) clear(form);
      });
    });
  }

  function init() {
    var forms = document.querySelectorAll('form[data-support-form]');
    for (var i = 0; i < forms.length; i++) wire(forms[i]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
