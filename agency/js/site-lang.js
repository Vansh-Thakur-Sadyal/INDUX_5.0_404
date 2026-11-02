/* Translate a plain HTML page into any of the fourteen languages, in place.
 *
 * One script, deployed to both static sites - visioncoders.in and the Saarthi
 * sign-in site. Neither has a build step, a framework or a string catalogue,
 * and hand-writing fourteen copies of every page is the approach that goes
 * stale the next time somebody edits a heading.
 *
 * What changed, and why it had to
 * -------------------------------
 * This used to call `translate.googleapis.com/translate_a/single?client=gtx`:
 * an undocumented endpoint with no key, no contract and no support. It worked
 * until it was used, and then it rate-limited - and because a failure here
 * leaves the English in place, a throttled page looks like a page with no
 * translation at all rather than like a page with a problem. It also joined a
 * batch into one newline-separated blob and split the answer back apart by
 * newline, so a translation that merged or added a line shifted every phrase
 * after it onto the wrong element.
 *
 * It now goes through Saarthi's own /api/i18n/translate, which is Azure
 * Translator behind a server-side cache. Three things follow from that:
 *
 *   * the answer is a MAP from English to translation, so nothing can be
 *     misfiled no matter what comes back;
 *   * the cache is shared with the dashboard and the other site, so a string
 *     any of the three has translated is already paid for;
 *   * a key with a quota and an SLA replaces an endpoint that was never ours.
 *
 * The four rules this follows
 * ---------------------------
 *  1. **English is never stored.** The original text of every node is kept on
 *     the node, so switching back to English restores the page exactly rather
 *     than translating it a second time through another language.
 *  2. **A failure shows English.** Never a blank, never a spinner that stays.
 *  3. **Nothing structural is touched.** Text nodes and human-readable
 *     attributes only - never a <script>, a <code>, a URL or a bare number.
 *  4. **The choice travels.** Each of these sites is its own origin and none
 *     can read the others' localStorage, so the choice is written under the
 *     shared `farm_language` key AND appended to any link that leaves for one
 *     of our other sites. Without that, choosing Punjabi here and signing in
 *     lands on an English dashboard.
 */
(function () {
  "use strict";

  /* Captured here and not inside `build`: `document.currentScript` is only the
     running script while the file is being evaluated, and `build` runs from
     DOMContentLoaded, by which time it is null and every page would get the
     default corner. */
  var SCRIPT = document.currentScript;

  /* Where the translation API is. Same-origin on the two sites this app serves
     itself, which the page says with `data-api`; the absolute URL is for the
     static sites on other origins, which reach it through CORS. */
  var API = (SCRIPT && SCRIPT.dataset && SCRIPT.dataset.api)
    || "https://saarthi2.0.visioncoders.in/api/i18n";
  var KEY = "farm_language";              // shared with the dashboard
  var MAP_KEY = "farm_strings_";          // the per-language phrase cache
  var PER_REQUEST = 60;
  var LANES = 4;

  /* Drawn until /api/i18n/languages answers, and what is drawn if it never
     does. The server's list is the authority - this is so the picker is never
     empty. */
  var LANGUAGES = [
    { code: "en", native_name: "English", english_name: "English" },
    { code: "hi", native_name: "हिन्दी", english_name: "Hindi" },
    { code: "bn", native_name: "বাংলা", english_name: "Bengali" },
    { code: "ta", native_name: "தமிழ்", english_name: "Tamil" },
    { code: "te", native_name: "తెలుగు", english_name: "Telugu" },
    { code: "mr", native_name: "मराठी", english_name: "Marathi" },
    { code: "gu", native_name: "ગુજરાતી", english_name: "Gujarati" },
    { code: "kn", native_name: "ಕನ್ನಡ", english_name: "Kannada" },
    { code: "ml", native_name: "മലയാളം", english_name: "Malayalam" },
    { code: "pa", native_name: "ਪੰਜਾਬੀ", english_name: "Punjabi" },
    { code: "or", native_name: "ଓଡ଼ିଆ", english_name: "Odia" },
    { code: "as", native_name: "অসমীয়া", english_name: "Assamese" },
    { code: "ur", native_name: "اردو", english_name: "Urdu" },
    { code: "ne", native_name: "नेपाली", english_name: "Nepali" }
  ];

  /* Elements whose text is not prose. <code> and <pre> are here because a
     translated command is a command that does not run. */
  var SKIP_TAGS = ["SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "TEXTAREA",
                   "CANVAS", "TEMPLATE", "IFRAME", "OBJECT"];
  var ATTRS = ["placeholder", "title", "alt", "aria-label"];
  var HAS_WORDS = /[A-Za-z]{2}/;
  var ONLY_SYMBOLS = /^[\s\d\W]*$/;
  var IS_URL = /^(https?:\/\/|mailto:|tel:|\/|#|www\.)/i;

  var current = "en";
  var busy = false;
  var originals = new Map();              // text node -> its English
  var nodes = [];
  var attrNodes = [];

  function readStore(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function writeStore(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private */ }
  }

  function cacheFor(code) {
    try { return JSON.parse(readStore(MAP_KEY + code) || "{}"); } catch (e) { return {}; }
  }
  function remember(code, pairs) {
    var map = cacheFor(code);
    for (var k in pairs) { if (pairs.hasOwnProperty(k)) map[k] = pairs[k]; }
    /* A page's worth of phrases is tens of kilobytes. If the quota is gone the
       cache is a nicety and translation still works without it. */
    try { writeStore(MAP_KEY + code, JSON.stringify(map)); } catch (e) { /* full */ }
  }

  /* A language handed over on the URL wins once, then is remembered. */
  function adoptFromUrl() {
    try {
      var found = /[?&#]lang=([a-z]{2})/.exec(location.href);
      if (found) writeStore(KEY, found[1]);
    } catch (e) { /* the picker still works for this visit */ }
  }

  function currentCode() {
    return readStore(KEY) || "en";
  }

  /* Add the current choice to a link that leaves for another of our sites. */
  window.siteLangUrl = function (url) {
    var code = currentCode();
    if (!code || code === "en") return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "lang=" + encodeURIComponent(code);
  };

  function skipped(el) {
    while (el) {
      if (SKIP_TAGS.indexOf(el.tagName) >= 0) return true;
      if (el.id === "siteLangWrap") return true;
      if (el.hasAttribute && (el.hasAttribute("data-no-translate")
          || el.getAttribute("translate") === "no")) return true;
      el = el.parentElement;
    }
    return false;
  }

  /** Every text node and readable attribute, with the English kept on it. */
  function collect() {
    var phrases = [];
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var value = node.nodeValue;
        if (!value || ONLY_SYMBOLS.test(value) || !HAS_WORDS.test(value)) {
          return NodeFilter.FILTER_REJECT;
        }
        return skipped(node.parentElement) ? NodeFilter.FILTER_REJECT
                                           : NodeFilter.FILTER_ACCEPT;
      }
    });
    var node;
    while ((node = walker.nextNode())) {
      if (!originals.has(node)) {
        originals.set(node, node.nodeValue);
        nodes.push(node);
      }
      phrases.push(originals.get(node).trim());
    }

    var selector = "[" + ATTRS.join("],[") + "]";
    Array.prototype.forEach.call(document.querySelectorAll(selector), function (el) {
      if (skipped(el)) return;
      ATTRS.forEach(function (attr) {
        var value = el.getAttribute(attr);
        if (!value || ONLY_SYMBOLS.test(value) || !HAS_WORDS.test(value)) return;
        if (IS_URL.test(value)) return;
        var keep = "data-en-" + attr.replace("aria-", "");
        if (!el.hasAttribute(keep)) el.setAttribute(keep, value);
        phrases.push(el.getAttribute(keep).trim());
        if (attrNodes.indexOf(el) < 0) attrNodes.push(el);
      });
    });
    return phrases;
  }

  /** Write a map of English -> translation onto everything we know about. */
  function apply(map) {
    nodes.forEach(function (node) {
      var english = originals.get(node);
      if (english === undefined) return;
      var phrase = english.trim();
      if (current === "en") { node.nodeValue = english; node.__lang = "en"; return; }
      var translated = map[phrase];
      if (translated === undefined) {
        /* No translation yet. If this node is still showing the language the
           farmer just switched AWAY from, put the English back while we wait.
           Leaving it was how a page with the picker on Urdu sat there in
           Marathi: the Urdu map starts empty, every node fell through this
           branch, and the previous language stayed on screen. */
        if (node.__lang && node.__lang !== current) {
          node.nodeValue = english;
          node.__lang = "en";
        }
        return;
      }
      // Keep the original's own spacing: collapsing it joins a label to the
      // punctuation that followed it.
      node.nodeValue = english.replace(phrase, translated);
      node.__lang = current;
    });

    attrNodes.forEach(function (el) {
      ATTRS.forEach(function (attr) {
        var keep = "data-en-" + attr.replace("aria-", "");
        if (!el.hasAttribute(keep)) return;
        var english = el.getAttribute(keep);
        if (current === "en") { el.setAttribute(attr, english); return; }
        var translated = map[english.trim()];
        if (translated !== undefined) el.setAttribute(attr, translated);
      });
    });

    document.documentElement.lang = current;
    /* The page layout is NOT flipped for Urdu.
       `dir="rtl"` on <html> mirrors the whole design - the navigation moves to
       the other side, the hero text reverses, and a full stop ends up at the
       start of the line. These pages are laid out left to right by hand and
       there is no mirrored stylesheet for them, so flipping produced a broken
       page rather than an Urdu one.
       Urdu still READS right to left: the Unicode bidirectional algorithm
       handles that inside each run of text on its own, which is the part that
       actually matters. `dir` is set explicitly rather than left alone so a
       page that was flipped before this fix is put back. */
    document.documentElement.dir = "ltr";
  }

  function fetchChunk(code, chunk) {
    var attempt = 0;
    function go() {
      return fetch(API + "/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: code, strings: chunk })
      }).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      }).then(function (body) {
        return body.translations || {};
      }).catch(function () {
        attempt += 1;
        if (attempt < 2) {
          return new Promise(function (r) { setTimeout(r, 900); }).then(go);
        }
        return null;                      // those phrases stay English
      });
    }
    return go();
  }

  /* The language most recently asked for. A pass checks THIS before it paints,
     so switching to Bengali and back to English cannot let the Bengali chunks
     land afterwards and stick.

     It is the LANGUAGE, not a pass counter. A counter also cancelled a pass
     whenever a newer pass for the SAME language started - and the observer
     starts one on every re-render, which on a page with carousels and scroll
     animations is constantly. In-flight chunks were invalidated faster than
     they could arrive, so nothing ever finished and the only thing that
     translated the page was a hard refresh, where the stored phrases are
     applied in one go before anything has had time to re-render. */
  var target = "en";
  var queued = false;

  function translateInto(code) {
    target = code;
    writeStore(KEY, code);
    /* A pass for this language is already running. Note that more work arrived
       and let it finish - restarting it is what cancelled it for ever. */
    if (busy) { queued = true; return Promise.resolve(); }
    return pass(code);
  }

  function pass(code) {
    current = code;
    setBusy(true);

    var phrases = collect();
    if (code === "en") {
      apply({});
      setBusy(false);
      return Promise.resolve();
    }

    var map = cacheFor(code);
    // Whatever was translated on an earlier visit goes on screen before a
    // single request is made.
    apply(map);

    var missing = [];
    var seen = {};
    phrases.forEach(function (phrase) {
      if (map[phrase] === undefined && !seen[phrase]) {
        seen[phrase] = true;
        missing.push(phrase);
      }
    });
    if (!missing.length) { setBusy(false); return settle(code); }

    var chunks = [];
    for (var i = 0; i < missing.length; i += PER_REQUEST) {
      chunks.push(missing.slice(i, i + PER_REQUEST));
    }
    var next = 0;
    busy = true;

    function lane() {
      if (next >= chunks.length || target !== code) return Promise.resolve();
      var chunk = chunks[next];
      next += 1;
      return fetchChunk(code, chunk).then(function (pairs) {
        if (target !== code) return null;
        if (pairs) {
          for (var k in pairs) { if (pairs.hasOwnProperty(k)) map[k] = pairs[k]; }
          remember(code, pairs);
          apply(map);
        }
        return lane();
      });
    }

    var lanes = [];
    for (var n = 0; n < Math.min(LANES, chunks.length); n += 1) lanes.push(lane());
    return Promise.all(lanes).then(function () {
      busy = false;
      if (target === code) { apply(map); setBusy(false); }
      return settle(code);
    });
  }

  /* One more pass if anything changed while that one ran.
     Either the farmer picked a different language mid-flight, or the page
     rendered new markup and the observer had to be told to wait. Bounded, so a
     page that never stops mutating cannot spin: three passes is far more than
     a real screen needs, and anything still untranslated after them stays
     English, which is readable. */
  var depth = 0;
  function settle(code) {
    busy = false;
    var again = target !== code || queued;
    queued = false;
    if (!again || depth >= 3) { depth = 0; return Promise.resolve(); }
    depth += 1;
    return pass(target);
  }

  /* Choosing a language reloads the page.
     -----------------------------------------------------------------------
     Translating in place works, and a reload is still the better answer here.
     These pages are not ours alone: a carousel has already measured its
     slides, a dropdown has cached its labels, and a heading that grows by
     forty per cent in Tamil leaves those measurements wrong until something
     re-renders. A reload re-runs every one of them against the new text, and
     it costs nothing the farmer notices - the phrases are in localStorage by
     then, so the page comes back already translated rather than in English.

     The language is written to storage FIRST. If the reload beat the write,
     the page would come back in the language it just left, which is the one
     outcome worse than not switching at all. */
  function choose(code) {
    writeStore(KEY, code);
    cacheFor(code);                      // touch: a read, so a quota error surfaces now
    window.location.reload();
  }
  window.siteLanguage = currentCode;
  window.siteSetLanguage = choose;

  // ------------------------------------------------------------------ the bar

  /* Where the picker sits when there is nothing to sit beside. */
  function corner() {
    var where = (SCRIPT && SCRIPT.dataset && SCRIPT.dataset.position) || "top-right";
    return where === "bottom-right" ? "bottom:16px;right:16px"
         : where === "bottom-left" ? "bottom:16px;left:16px"
         : where === "top-left" ? "top:14px;left:16px"
         : "top:14px;right:16px";
  }

  /* Sit just to the LEFT of something already in the header.
     -----------------------------------------------------------------------
     `data-anchor` is a CSS selector for that thing: the Saarthi button on the
     VisionCoders site, the menu button on Saarthi's own. Both of those had a
     picker parked in a corner where it either sat under the fold or on top of
     the control next to it.

     Measured rather than inserted into the markup. Dropping it into the
     header's DOM means inheriting that header's flexbox, its Bootstrap
     display utilities (which carry `!important`) and its responsive hiding -
     on this site the Saarthi button is `d-none` below large, so an element
     parented to it would simply vanish on a phone. Measuring leaves the
     header untouched and degrades to the corner the moment the anchor is not
     on screen.

     Re-measured on resize and on scroll, because these headers are sticky and
     some of them shrink as the page moves. */
  function anchorTo(selector) {
    var wrap = document.getElementById("siteLangWrap");
    if (!wrap) return false;
    var el = selector ? document.querySelector(selector) : null;
    var box = el && el.getBoundingClientRect();
    /* Not rendered, hidden by a display utility, or scrolled away: there is
       nothing to sit beside, so take the corner instead. */
    if (!box || !box.width || !box.height || box.bottom < 0
        || box.left < 60 || box.top > window.innerHeight) {
      wrap.style.cssText = "position:fixed;z-index:2147483000;transition:opacity .2s;"
        + corner();
      return false;
    }
    var height = wrap.offsetHeight || 34;
    var top = Math.max(6, Math.round(box.top + (box.height - height) / 2));
    var right = Math.max(8, Math.round(window.innerWidth - box.left + 12));
    wrap.style.cssText = "position:fixed;z-index:2147483000;transition:opacity .2s;"
      + "top:" + top + "px;right:" + right + "px";
    return true;
  }

  function followAnchor() {
    var selector = SCRIPT && SCRIPT.dataset && SCRIPT.dataset.anchor;
    if (!selector) return;
    var place = function () { anchorTo(selector); };
    place();
    // Once more after the webfonts and the hero image have settled, which is
    // what decides the header's final height.
    window.setTimeout(place, 400);
    window.setTimeout(place, 1500);
    window.addEventListener("resize", place, { passive: true });
    window.addEventListener("scroll", place, { passive: true });
  }

  function setBusy(on) {
    var wrap = document.getElementById("siteLangWrap");
    if (wrap) wrap.style.opacity = on && busy ? "0.6" : "1";
  }

  function fill() {
    var select = document.getElementById("siteLang");
    if (!select) return;
    select.innerHTML = LANGUAGES.map(function (l) {
      return '<option value="' + l.code + '">' + l.native_name
        + (l.code === "en" ? "" : " (" + l.english_name + ")") + "</option>";
    }).join("");
    select.value = current;
  }

  function build() {
    if (document.getElementById("siteLangWrap")) return;
    var wrap = document.createElement("div");
    wrap.id = "siteLangWrap";
    wrap.setAttribute("data-no-translate", "");
    /* Where the script tag says, so one file serves a page with a fixed header
       and a page without one. Default top-right, which is where this picker
       has always been on the sign-in site. */
    wrap.style.cssText =
      "position:fixed;z-index:2147483000;transition:opacity .2s;" + corner();

    var select = document.createElement("select");
    select.id = "siteLang";
    select.setAttribute("aria-label", "Choose language");
    select.style.cssText =
      "background:rgba(20,26,22,.92);color:#f2f5f4;border:1px solid rgba(255,255,255,.28);"
      + "border-radius:8px;padding:7px 10px;font-size:13.5px;cursor:pointer;max-width:11rem";
    select.onchange = function () { choose(select.value); };
    wrap.appendChild(select);
    document.body.appendChild(wrap);
    fill();
  }

  function refreshLanguages() {
    fetch(API + "/languages").then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (body) {
      if (body && body.languages && body.languages.length) {
        LANGUAGES = body.languages;
        fill();
      }
    }).catch(function () { /* the built-in list stands */ });
  }

  function start() {
    adoptFromUrl();
    current = currentCode();
    build();
    followAnchor();
    refreshLanguages();
    translateInto(current);

    /* These pages swap panels and render messages after load, so translate
       what arrives later too. Debounced: one render fires many mutations. */
    var pending = null;
    new MutationObserver(function () {
      if (current === "en") return;
      clearTimeout(pending);
      pending = setTimeout(function () { translateInto(current); }, 300);
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
}());
