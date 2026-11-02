/* What the page says, as a bounded, structured snapshot.
 *
 * The blueprint calls this DOM-first page understanding, and the reason to
 * prefer it over sending a picture is not cost: it is accuracy. The exact
 * figure is written in the DOM. A screenshot of the same figure has to be read
 * back out of pixels, and a model asked to do that will sometimes read a
 * different number - which, in a tool that tells farmers what to spray and when
 * to sell, is the one failure that matters.
 *
 * So this extracts text and structure, and `vision.js` is kept for the
 * questions that are genuinely about a shape.
 *
 * What is deliberately left out
 * -----------------------------
 * **Values of sensitive inputs.** Passwords, one-time codes, payment fields,
 * anything a password manager would fill. The LABEL goes - "there is a field
 * called Code" is useful context - and the VALUE never does. The snapshot says
 * `omittedSensitiveFields` so the model knows the page has more on it than it
 * was shown, rather than concluding the box was empty.
 *
 * **Anything invisible.** Hidden elements, the inactive pages of this
 * single-page app, scripts, styles, and the assistant's own furniture. A model
 * told about a dozen screens the farmer cannot see answers about the wrong one.
 *
 * **Everything past the bounds.** Node count and text length are capped and the
 * snapshot says `truncated`, because a long table should cost the end of the
 * table rather than the beginning of the instructions.
 *
 * Cross-origin iframes and closed shadow roots are not reachable from here and
 * are not attempted. Browser security is the reason, and pretending otherwise
 * would produce a snapshot that is quietly missing things.
 */
'use strict';

(function () {
  const MAX_ELEMENTS = 120;
  const MAX_TEXT = 400;            // per element
  const MAX_TOTAL = 9000;          // the whole snapshot's text

  /* Inputs whose VALUE must never leave the page. Matched on type, name, id,
     autocomplete and the label, because a payment field is called a dozen
     things and only some of them are a `type`. */
  const SENSITIVE = /pass|pwd|otp|code|secret|token|card|cvv|cvc|iban|account[\s_-]*(no|number)|aadhaar|pan\b|upi|pin\b/i;
  const SENSITIVE_TYPES = new Set(['password', 'hidden']);
  const SENSITIVE_AUTOCOMPLETE = /cc-|one-time-code|current-password|new-password/i;

  function visible(element) {
    if (!element || !element.getClientRects) return false;
    if (element.hidden || element.getAttribute('aria-hidden') === 'true') return false;
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (Number(style.opacity) === 0) return false;
    return element.getClientRects().length > 0;
  }

  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT);
  }

  /* The accessible name, which is what a person would call the control -
     usually better than its text, and the only thing an icon-only button has. */
  function nameOf(element) {
    return clean(element.getAttribute('aria-label')
      || element.getAttribute('title')
      || element.textContent
      || element.value
      || '');
  }

  function isSensitive(input) {
    if (SENSITIVE_TYPES.has((input.type || '').toLowerCase())) return true;
    if (SENSITIVE_AUTOCOMPLETE.test(input.getAttribute('autocomplete') || '')) return true;
    const label = labelFor(input);
    return SENSITIVE.test([input.name, input.id, input.placeholder, label].join(' '));
  }

  function labelFor(input) {
    if (input.labels && input.labels.length) return clean(input.labels[0].textContent);
    const wrapper = input.closest('label');
    if (wrapper) {
      const copy = wrapper.cloneNode(true);
      copy.querySelectorAll('input, select, textarea').forEach((n) => n.remove());
      return clean(copy.textContent);
    }
    return clean(input.getAttribute('aria-label') || input.placeholder || '');
  }

  /* A stable handle the model can refer to and the executor can resolve back to
     this element. Assigned here and nowhere else, so an id in a tool call that
     this function never issued cannot resolve to anything. */
  let counter = 0;
  const handles = new Map();

  function handle(element) {
    if (!element.dataset.assistantId) {
      counter += 1;
      element.dataset.assistantId = 'el-' + counter;
    }
    handles.set(element.dataset.assistantId, element);
    return element.dataset.assistantId;
  }

  function resolve(id) {
    const element = handles.get(id);
    // Re-checked rather than trusted: the page may have re-rendered since the
    // snapshot, and acting on a detached node silently does nothing.
    if (!element || !element.isConnected || !visible(element)) return null;
    return element;
  }

  function hrefOf(link) {
    try {
      const url = new URL(link.getAttribute('href'), location.href);
      // Same-origin keeps its path; anything else is reported as the origin
      // alone, because a query string on a foreign URL can carry a token.
      return url.origin === location.origin
        ? url.pathname + (url.hash || '')
        : url.origin;
    } catch (err) {
      return '';
    }
  }

  // ------------------------------------------------------------- the snapshot
  function capture(options) {
    const settings = options || {};
    const root = settings.root
      || document.querySelector('.page.active')
      || document.querySelector('main')
      || document.body;

    const elements = [];
    let total = 0;
    let truncated = false;
    let omittedSensitiveFields = false;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, {
      acceptNode(node) {
        if (node.closest('[data-assistant-ignore]')) return NodeFilter.FILTER_REJECT;
        // An inactive page of this single-page app is not "the page".
        if (node.classList && node.classList.contains('page')
            && !node.classList.contains('active')) return NodeFilter.FILTER_REJECT;
        if (/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|SVG|CANVAS)$/.test(node.tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    const push = (entry) => {
      if (elements.length >= MAX_ELEMENTS || total >= MAX_TOTAL) {
        truncated = true;
        return false;
      }
      elements.push(entry);
      total += (entry.text || entry.label || '').length;
      return true;
    };

    for (let node = walker.currentNode; node; node = walker.nextNode()) {
      if (node === root) continue;
      if (!visible(node)) continue;
      const tag = node.tagName;

      if (/^H[1-6]$/.test(tag)) {
        const text = clean(node.textContent);
        if (text) push({ type: 'heading', level: Number(tag[1]), text });
      } else if (tag === 'A' && node.getAttribute('href')) {
        const label = nameOf(node);
        if (label) push({ type: 'link', label, href: hrefOf(node), targetId: handle(node) });
      } else if (tag === 'BUTTON'
                 || (tag === 'INPUT' && /^(button|submit)$/i.test(node.type))) {
        const label = nameOf(node);
        if (label) {
          push({ type: 'button', label, targetId: handle(node),
                 disabled: Boolean(node.disabled) });
        }
      } else if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
        const sensitive = isSensitive(node);
        if (sensitive) omittedSensitiveFields = true;
        push({
          type: 'field',
          label: labelFor(node) || node.name || node.id || 'field',
          control: (node.type || tag.toLowerCase()),
          // The VALUE only when it is not sensitive, and never for a password
          // or a one-time code however it is labelled.
          value: sensitive ? null : clean(node.value).slice(0, 120),
          omitted: sensitive,
          targetId: handle(node),
        });
      } else if (/^(P|LI|TD|TH|SMALL|B|STRONG|SPAN|DD|DT|FIGCAPTION)$/.test(tag)) {
        // Only leaves, or the same sentence is reported once per ancestor.
        if (node.querySelector('p, li, td, th, button, a, input, select, textarea')) continue;
        const text = clean(node.textContent);
        if (text.length > 1) push({ type: 'paragraph', text });
      }
    }

    return {
      page: {
        title: clean(document.title),
        // The path, never the query: a query string is where a token ends up.
        url: location.origin + location.pathname,
        route: (location.hash || '').replace('#', '') || 'overview',
        locale: document.documentElement.lang || 'en',
        capturedAt: new Date().toISOString(),
      },
      elements,
      omittedSensitiveFields,
      truncated,
    };
  }

  /* The same snapshot as a short block of prose, for the places that want
     evidence rather than structure. */
  function asText(snapshot) {
    return (snapshot.elements || []).map((entry) => {
      if (entry.type === 'heading') return '#'.repeat(entry.level) + ' ' + entry.text;
      if (entry.type === 'link') return '[link] ' + entry.label + ' -> ' + entry.href;
      if (entry.type === 'button') return '[button] ' + entry.label
        + (entry.disabled ? ' (disabled)' : '');
      if (entry.type === 'field') return '[field] ' + entry.label
        + (entry.omitted ? ' (value not shared)' : ': ' + (entry.value || '(empty)'));
      return entry.text;
    }).join('\n').slice(0, MAX_TOTAL);
  }

  window.SaarthiPageContext = { capture, asText, resolve, handle, visible };
})();
