/* Saarthi 2.0 dashboard - vanilla JS, no build step, no CDN dependency.
   Everything is rendered from the API so nothing on screen is hardcoded data. */
(() => {
"use strict";

/* ------------------------------------------------------------------ api */
const API = {
  async get(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error((await r.text()).slice(0, 200) || r.statusText);
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  },
  async patch(path, body) {
    const r = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  },
  async put(path, body) {
    const r = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  },
  async del(path) {
    const r = await fetch(path, { method: "DELETE", headers: { Accept: "application/json" } });
    const text = await r.text();
    let data; try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  },
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, digits = 1) => (v === null || v === undefined || Number.isNaN(Number(v)))
  ? "--" : Number(v).toFixed(digits).replace(/\.0$/, "");

const state = {
  sessionId: localStorage.getItem("saarthi.session") || null,
  language: localStorage.getItem("saarthi.lang") || "en",
  languages: [],
  capabilities: [],
  speech: { configured: false },
  telephony: { configured: false },
  lastAdvisory: null,
  sensorMode: "simulation",
  photo: null,
  vision: null,
  crop: null,
  schemeFilter: "",
};

/* ---------------------------------------------------------------- icons */
const ICONS = {
  check: '<path d="m4 12 5 5L20 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.5l3.5 2"/>',
  rain: '<path d="M7 16a4.5 4.5 0 0 1 .6-9A6 6 0 0 1 19 8.5a3.8 3.8 0 0 1-.6 7.5"/><path d="M8 19l-1 2M12 19l-1 2M16 19l-1 2"/>',
  link: '<path d="M10 13a4 4 0 0 0 5.7.4l2.6-2.6a4 4 0 1 0-5.7-5.7L11.4 6.4"/><path d="M14 11a4 4 0 0 0-5.7-.4L5.7 13.2a4 4 0 1 0 5.7 5.7l1.1-1.1"/>',
  route: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.5 6H14a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h.5"/>',
  chat: '<path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6.5A8 8 0 0 1 11 4h2a8 8 0 0 1 8 8Z"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  soil: '<path d="M4 14h16M4 18h16"/><path d="M12 12c0-4 2-6 5-7-1 4-2 6-5 7Z"/><path d="M12 12c0-3-1.5-4.5-4-5 .5 3 1.6 4.6 4 5Z"/>',
  crop: '<path d="M12 21V9"/><path d="M12 11c0-4 2.5-6.5 6-7.5-.5 4.5-2.5 7-6 7.5Z"/><path d="M12 14c0-3-2-5-5-5.8.4 3.6 2 5.4 5 5.8Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  bug: '<path d="M8 7a4 4 0 0 1 8 0"/><rect x="7" y="7" width="10" height="11" rx="5"/><path d="M3 11h4M17 11h4M4 16h3M17 16h3M5 7l2 1M19 7l-2 1"/>',
  chart: '<path d="M4 19V5"/><path d="M4 19h16"/><path d="m7 15 3-4 3 3 4-6"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="3.2"/>',
  phone: '<path d="M6 3h3l2 5-2.5 1.5a12 12 0 0 0 5 5L15 12l5 2v3a2 2 0 0 1-2.2 2A16 16 0 0 1 4 5.2 2 2 0 0 1 6 3Z"/>',
  chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  network: '<circle cx="12" cy="5" r="2.4"/><circle cx="5" cy="18" r="2.4"/><circle cx="19" cy="18" r="2.4"/><path d="M12 7.4 6.6 15.8M12 7.4l5.4 8.4M7.4 18h9.2"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  water: '<path d="M12 3c3.5 4.2 6 7 6 10a6 6 0 1 1-12 0c0-3 2.5-5.8 6-10Z"/>',
  leaf: '<path d="M4 20c0-8 5-13 16-14 0 11-6 15-16 14Z"/><path d="M9 15c2-3 4.5-5 8-6"/>',
  weather: '<path d="M7 16a4 4 0 1 1 1-7.9A5 5 0 0 1 18 9a3.5 3.5 0 0 1-.5 7H7Z"/><path d="M9 19l-1 2M13 19l-1 2M17 19l-1 2"/>',
  thermo: '<path d="M12 14V5a2 2 0 1 1 4 0v9a4 4 0 1 1-4 0Z" transform="translate(-2)"/>',
  flask: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/>',
  download: '<path d="M12 4v10m0 0 4-4m-4 4-4-4"/><path d="M5 18h14"/>',
  map: '<path d="m9 4 6 2.5L21 4v15l-6 2.5L9 19l-6 2.5V6.5Z"/><path d="M9 4v15M15 6.5v15"/>',
  user: '<circle cx="12" cy="8.5" r="3.6"/><path d="M4.8 20a7.4 7.4 0 0 1 14.4 0"/>',
  shield: '<path d="M12 3 5 6v6c0 4.2 2.9 7.3 7 8 4.1-.7 7-3.8 7-8V6l-7-3Z"/><path d="m9 12 2 2 4-4"/>',
};
const icon = (name, size = 17) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ICONS.grid}</svg>`;

function paintIcons(root = document) {
  $$("i[data-icon]", root).forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
}

/* ---------------------------------------------------------------- toast */
let toastTimer;
function toast(message, ms = 3600) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

/* --------------------------------------------------------------- charts */
function lineChart(series, opts = {}) {
  // series: [{name, color, points:[{label, value}]}]
  const w = 620, h = opts.height || 190, pad = { l: 38, r: 12, t: 12, b: 26 };
  const all = series.flatMap((s) => s.points.map((p) => p.value)).filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (!all.length) return `<div class="empty-chart">${esc(opts.empty || "No data yet")}</div>`;
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const span = max - min || 1;
  const n = Math.max(...series.map((s) => s.points.length));
  const x = (i) => pad.l + (i * (w - pad.l - pad.r)) / Math.max(n - 1, 1);
  const y = (v) => pad.t + (h - pad.t - pad.b) * (1 - (v - min) / span);

  let grid = "";
  for (let g = 0; g <= 3; g++) {
    const gy = pad.t + ((h - pad.t - pad.b) * g) / 3;
    const val = max - (span * g) / 3;
    grid += `<line x1="${pad.l}" y1="${gy}" x2="${w - pad.r}" y2="${gy}" stroke="#eef3ed"/>
             <text x="6" y="${gy + 3}" font-size="9" fill="#90a095">${val.toFixed(span > 20 ? 0 : 1)}</text>`;
  }
  const paths = series.map((s) => {
    const pts = s.points.map((p, i) => (p.value === null || p.value === undefined ? null : `${x(i)},${y(p.value)}`)).filter(Boolean);
    if (!pts.length) return "";
    const dots = s.points.map((p, i) => (p.value === null || p.value === undefined ? "" :
      `<circle cx="${x(i)}" cy="${y(p.value)}" r="2.6" fill="${s.color}"/>`)).join("");
    return `<polyline points="${pts.join(" ")}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
  }).join("");
  const labels = series[0].points.map((p, i) =>
    (i % Math.ceil(n / 7) === 0 ? `<text x="${x(i)}" y="${h - 8}" font-size="9" fill="#90a095" text-anchor="middle">${esc(p.label)}</text>` : "")).join("");
  const legend = series.length > 1
    ? `<div class="chart-legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
  return `<svg viewBox="0 0 ${w} ${h}" role="img">${grid}${paths}${labels}</svg>${legend}`;
}

function barChart(points, opts = {}) {
  const w = 620, h = opts.height || 190, pad = { l: 38, r: 12, t: 12, b: 28 };
  const vals = points.map((p) => Number(p.value) || 0);
  if (!points.length) return `<div class="empty-chart">${esc(opts.empty || "No data yet")}</div>`;
  const max = Math.max(...vals, opts.min || 1);
  const bw = (w - pad.l - pad.r) / points.length;
  const bars = points.map((p, i) => {
    const bh = ((Number(p.value) || 0) / max) * (h - pad.t - pad.b);
    const x = pad.l + i * bw + bw * 0.22, bwid = bw * 0.56;
    return `<rect x="${x}" y="${h - pad.b - bh}" width="${bwid}" height="${Math.max(bh, 1)}" rx="4" fill="${p.color || opts.color || "#3fae63"}"/>
      <text x="${x + bwid / 2}" y="${h - pad.b - bh - 4}" font-size="9" fill="#6c7f71" text-anchor="middle">${esc(p.display ?? p.value)}</text>
      <text x="${x + bwid / 2}" y="${h - 9}" font-size="9" fill="#90a095" text-anchor="middle">${esc(p.label)}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" role="img"><line x1="${pad.l}" y1="${h - pad.b}" x2="${w - pad.r}" y2="${h - pad.b}" stroke="#e7efe6"/>${bars}</svg>`;
}

/* Pie with a hollow centre and a legend - for small categorical splits
   (agent outcomes, provenance mix) where exact values matter less than shape. */
function donutChart(segments, centreValue, centreLabel) {
  const data = segments.filter((s) => Number(s.value) > 0);
  const total = data.reduce((sum, s) => sum + Number(s.value), 0);
  if (!total) return `<div class="empty-chart">Nothing to show</div>`;
  const r = 52, cx = 70, cy = 70, circumference = 2 * Math.PI * r;
  let offset = 0;
  const rings = data.map((s) => {
    const fraction = Number(s.value) / total;
    const dash = `${(circumference * fraction).toFixed(2)} ${circumference}`;
    const el = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}"
      stroke-width="17" stroke-dasharray="${dash}"
      stroke-dashoffset="${(-circumference * offset).toFixed(2)}"
      transform="rotate(-90 ${cx} ${cy})"><title>${esc(s.label)}: ${s.value}</title></circle>`;
    offset += fraction;
    return el;
  }).join("");
  return `<div class="donut-wrap">
      <svg viewBox="0 0 140 140" width="150" height="150">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#eef3ed" stroke-width="17"/>
        ${rings}
        <text x="${cx}" y="${cy - 2}" text-anchor="middle" font-size="26" font-weight="800"
          fill="#17251d" font-family="Manrope">${esc(centreValue ?? total)}</text>
        <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="10" fill="#6c7f71">${esc(centreLabel || "")}</text>
      </svg>
      <div class="donut-legend">${data.map((s) => `
        <span><i style="background:${s.color}"></i>${esc(s.label)}<b>${s.value}</b></span>`).join("")}
      </div>
    </div>`;
}

/* Horizontal bars - readable with 15 rows, unlike a cramped vertical bar chart. */
function hBars(items, opts = {}) {
  if (!items.length) return `<div class="empty-chart">${esc(opts.empty || "No data")}</div>`;
  const max = opts.max || Math.max(...items.map((i) => Number(i.value) || 0)) || 1;
  return `<div class="hbars">${items.map((i) => `
      <div class="hbar" ${i.agent ? `data-agent="${i.agent}"` : ""}>
        <span class="hbar-label" title="${esc(i.label)}">${esc(i.label)}</span>
        <span class="hbar-track"><i style="width:${Math.max(2, (Number(i.value) / max) * 100)}%;background:${i.color || "var(--green)"}"></i></span>
        <span class="hbar-value">${esc(i.display ?? i.value)}</span>
      </div>`).join("")}</div>`;
}

/* One 100% bar split into phases - makes "the agents are not the slow part" obvious. */
function stackedBar(segments, caption) {
  const total = segments.reduce((sum, s) => sum + Number(s.value), 0) || 1;
  return `<div class="stacked">
      <div class="stacked-bar">${segments.map((s) => `
        <i style="width:${(Number(s.value) / total) * 100}%;background:${s.color}"
           title="${esc(s.label)}: ${esc(s.display)}"></i>`).join("")}</div>
      <div class="chart-legend">${segments.map((s) => `
        <span><i style="background:${s.color}"></i>${esc(s.label)} <b>${esc(s.display)}</b></span>`).join("")}</div>
      ${caption ? `<p class="hint">${esc(caption)}</p>` : ""}
    </div>`;
}

/* Colours come from the theme tokens, not from literals: a donut with a hard-coded
   near-white track and near-black text is unreadable once the page goes dark. */
function donut(value, label, sub) {
  const r = 58, c = 2 * Math.PI * r, pct = Math.max(0, Math.min(100, Number(value) || 0));
  const stroke = pct >= 75 ? "var(--green)" : pct >= 55 ? "var(--amber)" : "var(--red)";
  return `<svg viewBox="0 0 160 160" width="168" height="168">
    <circle cx="80" cy="80" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="15"/>
    <circle cx="80" cy="80" r="${r}" fill="none" stroke="${stroke}" stroke-width="15" stroke-linecap="round"
      stroke-dasharray="${(c * pct) / 100} ${c}" transform="rotate(-90 80 80)"/>
    <text x="80" y="76" text-anchor="middle" font-size="30" font-weight="800" fill="var(--ink)" font-family="Manrope">${pct}</text>
    <text x="80" y="95" text-anchor="middle" font-size="11" fill="var(--muted)">${esc(label || "")}</text>
    <text x="80" y="111" text-anchor="middle" font-size="10" fill="var(--muted-soft)">${esc(sub || "")}</text>
  </svg>`;
}

/* ------------------------------------------------------------- language */
function fillLanguageSelects() {
  const options = state.languages.map((l) =>
    `<option value="${l.code}">${esc(l.native_name)}${l.code === "en" ? "" : " (" + esc(l.english_name) + ")"}</option>`).join("");
  ["#langSidebar", "#fabLang", "#callLang", "#pfLang"].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.innerHTML = options;
    el.value = sel === "#callLang" ? "hi" : state.language;
  });
}

async function setLanguage(code, chosen) {
  /* Through the same queue the observer uses, not straight into applyLanguage.
     A switch used to run OUTSIDE the `translating` guard, so a page that
     re-rendered mid-switch started a second, concurrent pass for the same
     language. The two raced over the same nodes - each skipping what the other
     had already claimed with `__lang` - and whichever block was redrawn last
     kept the language from before. Queued, they run one after the other and the
     second picks up exactly what the first could not see yet. */
  retranslate(code);
  state.language = code;
  localStorage.setItem("saarthi.lang", code);
  ["#langSidebar", "#fabLang"].forEach((s) => { if ($(s)) $(s).value = code; });
  const lang = state.languages.find((l) => l.code === code);
  if (state.sessionId) {
    try { await API.post(`/api/chat/session/${state.sessionId}/language`, { language: code }); } catch { /* new session will carry it */ }
  }
  /* Chosen from the picker: start the whole screen again in the new language.
     After the session language is saved, never before - a reload that beat
     that request would come back to a conversation still held in the old one.
     `chosen` is false on boot (which applies the stored language on every
     load, so reloading there would never stop) and absent from
     followReplyLanguage, which must not throw away a conversation in progress
     just because the farmer spoke a different language into it. */
  if (chosen) window.location.reload();
}


/* The server answered in a language. Follow it.
   -------------------------------------------------------------------------
   The picker says what the farmer chose; the reply says what they actually
   got. When a farmer types or speaks Punjabi into an English conversation the
   server answers in Punjabi, and leaving the picker on English would mean the
   NEXT reply came back in English again - the switch would last exactly one
   message. `quiet` because this is not the farmer choosing: it updates the
   page and the session without re-translating the whole interface under them. */
async function followReplyLanguage(code) {
  if (!code || code === state.language) return;
  if (!(state.languages || []).some((l) => l.code === code)) return;
  state.language = code;
  try { localStorage.setItem("saarthi.lang", code); } catch { /* private window */ }
  ["#langSidebar", "#fabLang"].forEach((sel) => { if ($(sel)) $(sel).value = code; });
  const lang = (state.languages || []).find((l) => l.code === code);
}

/* ---------------------------------------------------------------- pages */
const RENDERERS = {};
let currentPage = "overview";

/* The pages an administrator account is allowed on. Everything else is a
   farmer page, which for an admin is an empty state with no field, no reading
   and no crop - so they are sent to the one that has data instead. */
const ADMIN_PAGES = ["admin", "knowledge"];

function go(page) {
  if (state.isAdmin && !ADMIN_PAGES.includes(page)) page = "admin";
  currentPage = page;
  /* Overview has the big hero button; two identical controls one above the other
     is just noise. Every other page keeps the header one. */
  const top = $("#runPipelineTop");
  if (top) top.hidden = page === "overview" || Boolean(state.isAdmin);
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  /* Switching pages used to be a cut: one screen vanished and another was
     simply there, which on a voice command - where the farmer did not touch
     anything - reads as a glitch rather than a move. A short fade and rise
     costs 180 ms and makes it clear that something was done on purpose.
     `enter` is removed on the next frame so the animation can run again the
     next time this page is opened. */
  $$(".page").forEach((p) => {
    const active = p.id === "page-" + page;
    p.classList.toggle("active", active);
    if (!active) { p.classList.remove("enter"); return; }
    p.classList.remove("enter");
    void p.offsetWidth;                       // restart the animation
    p.classList.add("enter");
  });
  $("#sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  location.hash = page;
  if (RENDERERS[page]) {
    RENDERERS[page]()
      .then(retranslate)
      .catch((e) => toast("Could not load: " + e.message));
  }
}


/* Take a reading without leaving the Overview. Connecting is a device-chooser step
   the browser only allows from a real click, so this hands over to the Sensor page
   when nothing is paired yet rather than failing silently. */
async function heroFetch() {
  const note = $("#heroFetchNote");
  if (!window.SaarthiBLE || !window.SaarthiBLE.info().connected) {
    note.textContent = "No sensor paired yet - opening the Sensor page.";
    go("devices");
    return;
  }
  const btn = $("#heroFetch");
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Fetching...";
  note.textContent = "Waiting for the next packet.";
  try {
    await bleFetchOnce();
    note.textContent = "Done. The figures above are from that reading.";
    await RENDERERS.overview();
  } catch (e) {
    note.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}


/* Which plot the dashboard is looking at. Each field has its own soil, its own crop
   and its own position, so a reading belongs to one of them - and everything built
   from that reading has to follow the same choice or the pages disagree. */
function activeParcel() {
  const picker = $("#heroField");
  return (picker && picker.value) || localStorage.getItem("saarthi.parcel") || "";
}

function setActiveParcel(id) {
  if (id) localStorage.setItem("saarthi.parcel", id);
}

async function paintFieldPickers() {
  const data = await API.get(asOwner("/api/profile/fields")).catch(() => ({ fields: [] }));
  state.fields = data.fields || [];
  const chosen = activeParcel();
  const options = state.fields.length
    ? state.fields.map((f) => `<option value="${esc(f.field_id)}">${esc(f.name)}`
        + (f.crop ? ` - ${esc(f.crop)}` : "") + "</option>").join("")
    : '<option value="">No fields registered yet</option>';
  ["#heroField", "#sensorField"].forEach((sel) => {
    const node = $(sel);
    if (!node) return;
    node.innerHTML = options;
    if (chosen && state.fields.some((f) => f.field_id === chosen)) node.value = chosen;
  });
  setActiveParcel(activeParcel());
}


/* --------------------------------------------------------- notifications */
/* Things that happened while the farmer was not looking. Kept in this browser:
   there is no push channel, and inventing a server-side inbox for a list of local
   events would be a lot of machinery for no extra truth. */
const NOTIF_KEY = "saarthi.notifications";

function loadNotifications() {
  try {
    return JSON.parse(localStorage.getItem(NOTIF_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function notify(kind, title, detail) {
  const items = loadNotifications();
  items.unshift({ kind, title, detail, at: new Date().toISOString(), seen: false });
  try {
    localStorage.setItem(NOTIF_KEY, JSON.stringify(items.slice(0, 40)));
  } catch (e) { /* a full quota must not break the run that is reporting it */ }
  renderNotifications();
}

function renderNotifications() {
  const items = loadNotifications();
  const unseen = items.filter((n) => !n.seen).length;
  const dot = $("#bellDot");
  if (dot) dot.hidden = unseen === 0;

  const list = $("#notifList");
  if (!list) return;
  list.innerHTML = items.length
    ? items.map((n) => `
        <div class="notif ${esc(n.kind)}">
          <b>${esc(n.title)}</b>
          <small>${esc(n.detail || "")}</small>
          <time>${esc(String(n.at).replace("T", " ").slice(0, 16))}</time>
        </div>`).join("")
    : `<p class="hint">Nothing yet.</p>`;
}

function markNotificationsSeen() {
  const items = loadNotifications().map((n) => ({ ...n, seen: true }));
  try { localStorage.setItem(NOTIF_KEY, JSON.stringify(items)); } catch (e) { /* ignore */ }
  renderNotifications();
}

/* Checks that only make sense against the last time the farmer looked: a price that
   moved, a forecast that turned. Run once per dashboard load. */
async function checkForChanges() {
  const seenKey = "saarthi.lastSeen";
  let last = {};
  try { last = JSON.parse(localStorage.getItem(seenKey) || "{}"); } catch (e) { last = {}; }
  const now = {};

  const farm = state.farm || {};
  const market = await API.get("/api/market?commodity="
    + encodeURIComponent(farm.crop || "wheat")).catch(() => null);
  if (market && market.available && market.median_modal) {
    now.price = market.median_modal;
    if (last.price && Math.abs(now.price - last.price) / last.price >= 0.05) {
      const up = now.price > last.price;
      notify("price", `${farm.crop || "Wheat"} price ${up ? "up" : "down"} `
        + `${Math.abs(Math.round((now.price - last.price) / last.price * 100))}%`,
        `Now ₹${Math.round(now.price)} per quintal, was ₹${Math.round(last.price)}.`);
    }
  }

  const weather = await API.get("/api/weather?days=5").catch(() => null);
  if (weather && weather.available) {
    const rain = (weather.forecast || []).slice(0, 3)
      .reduce((a, d) => a + Number(d.rain_mm || 0), 0);
    now.rain3 = Math.round(rain * 10) / 10;
    if (last.rain3 !== undefined && now.rain3 >= 10 && last.rain3 < 10) {
      notify("weather", "Rain is coming",
        `${now.rain3} mm expected in the next three days. Hold off on spraying.`);
    }
  }

  /* Schemes. There is no government feed behind the directory - it is reference
     data that ships with the app - so this says what is true: the list on this
     dashboard has gained something since you last looked. */
  const schemes = await API.get("/api/schemes").catch(() => null);
  if (schemes && schemes.version) {
    now.schemeVersion = schemes.version;
    now.schemeIds = schemes.ids || [];
    if (last.schemeVersion && last.schemeVersion !== now.schemeVersion) {
      const before = new Set(last.schemeIds || []);
      const added = (now.schemeIds || []).filter((id) => !before.has(id));
      const names = (schemes.schemes || [])
        .filter((s) => added.includes(s.id))
        .map((s) => s.title.split(" (")[0]);
      notify("scheme",
        added.length ? `${added.length} scheme(s) added` : "The scheme list changed",
        names.length ? names.join(", ") : "Open Govt Schemes to see what is listed now.");
    }
  }

  try { localStorage.setItem(seenKey, JSON.stringify(now)); } catch (e) { /* ignore */ }
}


/* -------------------------------------------------------------- language */
/* Walking the DOM rather than tagging every element by hand. A hand-kept catalogue
   of a few hundred labels across thirteen languages goes stale the first time
   anybody edits a heading, and it cannot cover a language added later.

   The English original is stashed on the node, so switching back to English is an
   exact restore and not a second translation. */

const I18N_SKIP = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "SVG", "PATH", "TEXTAREA"]);
let uiLanguage = "en";
let translating = false;

function translatableNodes(root) {
  const out = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent || I18N_SKIP.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      if (parent.closest("[data-no-i18n]")) return NodeFilter.FILTER_REJECT;
      const text = node.nodeValue.trim();
      /* A node we have already translated holds no Latin letters any more, so the
         test below would reject it and the English restore would never see it.
         Anything already carrying its English original always qualifies. */
      if (node.__en !== undefined) return NodeFilter.FILTER_ACCEPT;
      /* Leave data alone: a bare number, a price, a date, a coordinate. Translating
         "2580" or "27.96" would be actively wrong. */
      if (!/[A-Za-z]{2}/.test(text)) return NodeFilter.FILTER_REJECT;
      /* The pipeline's advisory runs well past 400 characters, and it is the one
         block on the screen the farmer most needs in their own language - the cap
         was leaving the advice in English under a fully translated page. Long
         strings go out in a chunk of their own below. */
      if (text.length > 3000) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let node = walker.nextNode();
  while (node) { out.push(node); node = walker.nextNode(); }
  return out;
}

function attributeTargets(root) {
  return Array.from(root.querySelectorAll("[placeholder], [title], [aria-label]"))
    .filter((el) => !el.closest("[data-no-i18n]"));
}

/* Only translate a string once per language: if the node already holds this
   language, there is nothing to ask for and nothing to change. */
function needsTranslating(node, lang) {
  return node.__lang !== lang;
}

/* Collect, translate, apply. Called on boot and whenever the language changes, and
   again after a page renders new content. */
/* The language most recently asked for. A pass checks this before it paints, so
   switching to Bengali and back to English cannot let the Bengali chunks land
   afterwards and stick.

   It is the LANGUAGE, not a pass counter. A counter also cancelled a pass whenever
   a newer pass for the SAME language started - and since the observer starts one on
   every re-render, in-flight chunks were invalidated faster than they could arrive
   and the screen sat at two thirds translated for ever. */
let langTarget = "en";

async function applyLanguage(code, root) {
  const scope = root || document.body;
  const lang = (code || "en").toLowerCase();
  langTarget = lang;
  /* Set now, not when the translating finishes. These two mean different things:
     uiLanguage is the language the screen is MEANT to be in, and the observer uses
     it to decide whether a freshly rendered block needs translating. Leaving it on
     "en" until the last chunk landed made the observer ignore every re-render that
     happened while a cold language was still loading - which is most of them - so
     whole blocks were never collected and sat in English for good. */
  uiLanguage = lang;

  const texts = translatableNodes(scope);
  const attrs = attributeTargets(scope);

  if (lang === "en") {
    /* Exact restore from what was stashed, not a translation back. */
    texts.forEach((n) => {
      if (n.__en !== undefined) { n.nodeValue = n.__en; n.__lang = "en"; }
    });
    attrs.forEach((el) => {
      ["placeholder", "title", "aria-label"].forEach((a) => {
        const key = "__en_" + a;
        if (el[key] !== undefined) el.setAttribute(a, el[key]);
      });
    });
    uiLanguage = "en";
    return;
  }

  const wanted = new Set();
  texts.forEach((n) => {
    if (n.__en === undefined) n.__en = n.nodeValue;
    /* Already in this language - asking for it again would make every repeat pass
       cost as much as the first one, on a dashboard that re-renders constantly. */
    if (n.__lang === lang) return;
    wanted.add(n.__en.trim());
  });
  attrs.forEach((el) => {
    ["placeholder", "title", "aria-label"].forEach((a) => {
      const value = el.getAttribute(a);
      if (!value || !/[A-Za-z]{2}/.test(value)) return;
      const key = "__en_" + a;
      if (el[key] === undefined) el[key] = value;
      wanted.add(el[key].trim());
    });
  });
  if (!wanted.size) { uiLanguage = lang; return; }

  /* Chunked and parallel. One request for the whole page meant five sequential
     model batches on the server and a page that did not move for half a minute;
     this way each chunk is applied the moment it lands, so the screen fills in from
     the first second. A cached language comes back in one round trip. */
  const all = [...wanted];
  const CHUNK = 40;
  const chunks = [];
  /* A paragraph and a button label do not belong in the same request: forty short
     labels fit one model reply comfortably, one long advisory does not. */
  const long = all.filter((t) => t.length > 400);
  const short = all.filter((t) => t.length <= 400);
  for (let i = 0; i < short.length; i += CHUNK) chunks.push(short.slice(i, i + CHUNK));
  long.forEach((t) => chunks.push([t]));

  const map = {};
  const applyMap = () => {
    if (lang !== langTarget) return;
    texts.forEach((n) => {
      if (n.__lang === lang) return;
      const hit = map[n.__en.trim()];
      if (hit) {
        n.nodeValue = n.__en.replace(n.__en.trim(), hit);
        n.__lang = lang;
      }
    });
    attrs.forEach((el) => {
      ["placeholder", "title", "aria-label"].forEach((a) => {
        const key = "__en_" + a;
        if (el[key] === undefined) return;
        const hit = map[el[key].trim()];
        if (hit) el.setAttribute(a, hit);
      });
    });
  };

  /* Only say "translating" if it is actually going to take a moment - a cached
     language should not flash a message at the farmer. */
  const waiting = setTimeout(() => toast("Translating the screen..."), 900);
  let failed = 0;

  /* Three at a time, in page order. Firing every chunk at once translated a cold
     screen in under two seconds right up until the model API started answering
     429 Rate limit exceeded - and a rate-limited chunk falls back to English, so
     the fast version was the one that left half the dashboard untranslated. Six
     lanes with the server retrying its own rate limits keeps a cold screen quick
     without stampeding the model API. Chunks run in page order, so the top of the
     screen - the part the farmer is looking at - fills first. */
  const LANES = 6;
  let next = 0;

  const fetchChunk = async (chunk) => {
    /* One retry, because a 429 here costs the farmer a screen of English and the
       second attempt usually lands once the first few chunks have drained. */
    for (let attempt = 0; attempt < 2; attempt += 1) {
      if (lang !== langTarget) return true;
      try {
        const res = await API.post("/api/i18n/translate",
          { language: lang, strings: chunk });
        Object.assign(map, res.translations || {});
        applyMap();
        return true;
      } catch (e) {
        if (attempt === 0) await new Promise((r) => setTimeout(r, 1200));
      }
    }
    return false;
  };

  const lane = async () => {
    while (next < chunks.length) {
      if (lang !== langTarget) return;
      const chunk = chunks[next];
      next += 1;
      if (!(await fetchChunk(chunk))) failed += 1;
    }
  };

  await Promise.all(Array.from({ length: Math.min(LANES, chunks.length) }, lane));

  clearTimeout(waiting);
  if (lang !== langTarget) return;
  if (failed && failed === chunks.length) toast("Could not translate right now.");
}

/* Re-run after new markup appears. Debounced: one page render fires many mutations.

   Driven by a MutationObserver rather than by calls dotted through the renderers.
   Every table, card and list on this dashboard is written by its own function, and
   relying on each of them to remember a retranslate() call is how three labels on
   the Finance page stayed in English - they are drawn after the page render, by a
   path that did not have the call. */
let retranslateTimer = null;
let missedWhileBusy = false;
function retranslate(code) {
  /* `code` is a language the farmer just chose, and it must go through even
     when that language is English - restoring the originals IS the work. A call
     with no code is the observer saying "new markup appeared", which has
     nothing to do on an English screen. */
  if (code) uiLanguage = code;
  else if (uiLanguage === "en") return;
  if (translating) {
    /* Do not drop it. A page render that lands mid-translation is exactly the
       case that left whole screens in English: the observer fired, saw the flag
       and returned, and nothing ever came back for that markup. */
    missedWhileBusy = true;
    return;
  }
  clearTimeout(retranslateTimer);
  /* A chosen language starts now. The observer's 250 ms is there to collect a
     burst of mutations into one pass; making the farmer wait it out after a tap
     on the picker is a quarter second of nothing happening. */
  retranslateTimer = setTimeout(async () => {
    translating = true;
    try {
      await applyLanguage(uiLanguage);
    } finally {
      translating = false;
    }
    if (missedWhileBusy) {
      missedWhileBusy = false;
      retranslate();
    } else if (settlePasses < 2 && stillUntranslated()) {
      /* A chunk failed, or markup arrived in the gap between the last apply and
         the flag being cleared. One more pass costs a cached round trip and is
         the difference between a screen that finishes and one that needs a
         reload. */
      settlePasses += 1;
      retranslate();
    } else {
      settlePasses = 0;
    }
  }, code ? 0 : 250);
}

/* Is anything on screen still not in the language it is meant to be in?
   Only counts nodes we have already claimed - a node with no `__en` has never
   been collected, and the next pass will collect it anyway. */
let settlePasses = 0;
function stillUntranslated() {
  if (uiLanguage === "en") return false;
  return translatableNodes(document.body).some(
    (n) => n.__en !== undefined && n.__lang !== uiLanguage);
}

function watchForNewText() {
  const observer = new MutationObserver((records) => {
    if (uiLanguage === "en") return;
    /* Only wake for markup that could carry a label. Attribute and value changes
       fire constantly while a farmer types. */
    const worthIt = records.some((r) => {
      if (r.type !== "childList") return false;
      /* The toast is written with textContent, which counts as an insertion. Its
         own text is a status message, not a label, and translating it would make
         every toast cost a round trip. */
      const t = r.target;
      if (t && t.id === "toast") return false;
      return [...r.addedNodes].some((n) =>
        n.nodeType === 1 || (n.nodeType === 3 && /[A-Za-z]{2}/.test(n.nodeValue || "")));
    });
    if (worthIt) retranslate();
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

/* ------------------------------------------------------------- overview */
RENDERERS.overview = async function renderOverview() {
  const parcel = activeParcel();
  const data = await API.get("/api/dashboard/overview"
    + (parcel ? "?parcel_id=" + encodeURIComponent(parcel) : ""));
  const farm = data.farm || {};
  const hour = new Date().getHours();
  $("#greeting").textContent =
    `${hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening"}, ${farm.farmer_name || "Farmer"}!`;

  const soil = data.soil || {};
  const w = data.weather || {};
  const cur = w.current || {};
  if (w.available) {
    $("#topWeather").innerHTML =
      `<span class="wsun"></span><b>${num(cur.temperature_c, 0)}&deg;C</b><small>${esc(cur.condition || "")}</small>`;
  } else {
    $("#topWeather").innerHTML = `<span class="wsun"></span><b>--</b><small>Weather unavailable</small>`;
  }

  const moisture = (data.parameters || []).find((p) => p.field === "moisture_pct") || {};
  const simulated = data.sensor_mode === "simulation";
  const tiles = [
    {
      ico: "soil", cls: "", title: "Soil Health",
      value: soil.available ? soil.index : "--", unit: soil.available ? "/100" : "",
      sub: soil.available ? soil.label : "No sensor reading", bar: soil.available ? soil.index : 0,
      note: simulated ? "Simulated data" : "",
    },
    {
      ico: "water", cls: "blue", title: "Soil Moisture",
      value: moisture.value !== undefined && moisture.value !== null ? num(moisture.value, 0) : "--",
      unit: "%", sub: moisture.band ? moisture.band[0].toUpperCase() + moisture.band.slice(1) : "No reading",
      bar: moisture.value || 0, barCls: "blue", note: simulated ? "Simulated data" : "",
    },
    {
      ico: "crop", cls: "", title: "Crop Condition",
      value: (data.crop && data.crop.condition) || "--", unit: "",
      sub: (data.crop && data.crop.name ? data.crop.name : "") + (data.crop && data.crop.season ? " - " + data.crop.season : ""),
      note: "From soil index only",
    },
    {
      ico: "weather", cls: "amber", title: "Weather Risk",
      value: (data.weather_risk && data.weather_risk.level) || "--", unit: "",
      sub: (data.weather_risk && data.weather_risk.risks && data.weather_risk.risks.length)
        ? data.weather_risk.risks.join(", ") : "No major risk in 5 days",
      note: w.available ? "Open-Meteo" : "Forecast unavailable",
    },
  ];
  $("#metricTiles").innerHTML = tiles.map((t) => `
    <article class="metric">
      <div class="m-top"><span class="m-ico ${t.cls}">${icon(t.ico, 16)}</span><h3>${esc(t.title)}</h3></div>
      <div class="m-val">${esc(t.value)}${t.unit ? `<small>${esc(t.unit)}</small>` : ""}</div>
      <div class="m-sub${t.note && t.note.includes("Simulated") ? " muted" : ""}">${esc(t.sub || "")}</div>
      ${t.bar !== undefined ? `<div class="bar"><i class="${t.barCls || ""}" style="width:${Math.max(0, Math.min(100, t.bar))}%"></i></div>` : ""}
      ${t.note ? `<div class="m-sub muted" style="margin-top:6px">${esc(t.note)}</div>` : ""}
    </article>`).join("");

  // The overview shows the farmer's real mapped plots. Their own fields are the
  // authority here; the dashboard's demo plots are only a stand-in until they map one.
  const mine = await API.get(asOwner("/api/profile/fields")).catch(() => null);
  state.fields = (mine && mine.fields) || [];
  ensureMap("overview");
  $("#overviewMapHint").textContent = state.fields.length
    ? `${state.fields.length} plot(s) you mapped, ${mine.total_acres} acres in total.`
    : "No plots mapped yet - open My Fields to draw your first one.";

  $("#recoList").innerHTML = (data.recommendations || []).map((r) => `
    <div class="reco" data-go="${esc(r.page || "overview")}">
      <span class="r-ico">${icon(r.icon === "water" ? "water" : r.icon === "weather" ? "weather" : "leaf", 16)}</span>
      <span><b>${esc(r.title)}</b><small>${esc(r.detail)}</small></span>
      <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="#9aa89d" stroke-width="2"><path d="m9 6 6 6-6 6"/></svg>
    </div>`).join("") || `<p class="hint">No recommendations yet.</p>`;

  $("#activityList").innerHTML = (data.activity || []).length
    ? data.activity.map((a) => `
      <div class="reco"><span class="r-ico">${icon("spark", 15)}</span>
        <span><b>${esc(a.title)}</b><small>${esc(a.detail)} - ${esc((a.at || "").replace("T", " ").slice(0, 16))}</small></span>
      </div>`).join("")
    : `<p class="hint">No advisories yet. Ask Saarthi a question and it will appear here.</p>`;

  /* The selected field, not every reading on the account. Without the parcel
     the chart was identical whichever field was chosen, so the picker looked
     broken - and the number above it ("24 stored readings") was counting
     somebody else's plot. */
  const history = await API.get(
    "/api/sensors/history?limit=24&parcel_id=" + encodeURIComponent(activeParcel()));
  const pts = (history.series || []).map((s) => ({
    label: (s.recorded_at || "").slice(11, 16),
    value: s.moisture_pct,
  }));
  $("#trendNote").textContent = history.count
    ? `${history.count} stored readings${history.simulated_count ? ` (${history.simulated_count} simulated)` : ""}`
    : "No readings stored yet";
  $("#overviewTrend").innerHTML = lineChart(
    [{ name: "Soil moisture %", color: "#2f80d6", points: pts }],
    { empty: "No sensor readings yet. Generate one on the Sensor Devices page." });

  /* The agent board and the capability grid were drawn here for two cards on
     the profile page, which are gone: a tour of the machinery under a farmer's
     own details. The capabilities are still READ, because the warning below
     about an unconfigured service is worth having wherever it is noticed. */
  state.capabilities = data.capabilities || [];
  const missing = state.capabilities.filter((c) => !c.configured).length;
  if (missing && !sessionStorage.getItem("saarthi.capWarned")) {
    const names = state.capabilities.filter((c) => !c.configured).map((c) => c.name);
    notify("warn", `${missing} service(s) not configured`, names.join(", "));
    try { sessionStorage.setItem("saarthi.capWarned", "1"); } catch (e) { /* ignore */ }
  }
  renderNotifications();
};



/* ------------------------------------------------------------ ask modal */
/* What each agent would need. Kept beside the backend table of the same name; the
   server is the authority and sends the full list with every run, but the matrix and
   the agent cards have to be clickable before a run has ever happened. */
const AGENT_ASKS = {
  3: ["crop"], 4: ["crop", "sowing_date", "stage"], 6: ["crop"],
  8: ["symptoms", "crop"], 10: ["crop"], 11: ["crop", "symptoms"],
  12: ["crop"], 13: ["cost", "yield_quintal", "price"],
};
let askSpecs = {};          /* filled from the last run, which carries labels and help */

function rememberAskSpecs(needed) {
  (needed || []).forEach((item) => { askSpecs[item.key] = item; });
}

/* The labels are static, so they are fetched once at boot rather than arriving with
   a finished run. Without this the first form a farmer sees is raw field names. */
async function loadAskSpecs() {
  const body = await API.get("/api/pipeline/inputs").catch(() => null);
  if (body) rememberAskSpecs(body.inputs || []);
}

/* What the dashboard already knows. Asking a farmer to type the crop when the field
   they just picked says "wheat" is asking them to do the computer's work. */
function knownAnswers() {
  const field = (state.fields || []).find((f) => f.field_id === activeParcel())
    || (state.fields || [])[0] || {};
  const known = {};
  if (field.crop) known.crop = field.crop;
  if (field.sowing_date) known.sowing_date = field.sowing_date;
  return known;
}

/* One box, properly labelled. Shared by the pre-run form and the per-agent one so
   they cannot drift apart. */
/* Choices the server sends, with a local copy so this keeps working against a
   dashboard build that predates them. Picking beats typing: a misspelt crop
   reaches the agents as no crop at all. */
const ASK_FALLBACK_OPTIONS = {
  crop: ["wheat", "rice", "maize", "mustard", "chickpea", "lentil", "field pea",
         "barley", "potato", "onion", "garlic", "tomato", "cotton", "sugarcane",
         "soybean", "groundnut", "coriander", "fenugreek"],
  stage: ["sowing", "germination", "seedling", "tillering", "jointing", "booting",
          "flowering", "grain filling", "ripening", "maturity", "harvest"],
  symptoms: ["yellowing leaves", "brown or black spots", "holes in the leaves",
             "wilting", "curling leaves", "white powder on the leaves",
             "stunted growth", "insects on the plant", "rotting at the base",
             "patchy growth across the field"],
};

const ASK_CUSTOM = "__custom__";

function askOptionsFor(key, spec) {
  const fromServer = (spec && spec.options) || [];
  return fromServer.length ? fromServer : (ASK_FALLBACK_OPTIONS[key] || []);
}

function askFieldHtml(key, value, locked) {
  const spec = askSpecs[key] || { label: key.replace(/_/g, " "), type: "text",
                                  placeholder: "", help: "" };
  const type = spec.type || "text";
  const options = askOptionsFor(key, spec);
  const current = value || "";

  /* The crop comes off the field record, so it is shown read-only: editing it here
     would send the chain a crop that disagrees with the plot it is reasoning about,
     and the farmer would have no way to tell which one won. Change it on My Fields
     and it changes everywhere. */
  const help = locked
    ? `<small class="hint">From the field you selected. Change it on My Fields.</small>`
    : spec.help ? `<small class="hint">${esc(spec.help)}</small>` : "";

  if (locked || !options.length) {
    return `<label class="field ask-field">
      <span>${esc(spec.label)}</span>
      <input data-ask="${esc(key)}" type="${esc(type === "select" || type === "multiselect" ? "text" : type)}"
             ${type === "number" ? 'inputmode="decimal" step="any"' : ""}
             ${locked ? "readonly class=\"locked\"" : ""}
             placeholder="${esc(spec.placeholder || "")}"
             value="${esc(current)}">
      ${help}
    </label>`;
  }

  /* Several at once, because a sick plant usually shows more than one thing. */
  if (type === "multiselect") {
    const chosen = current.split(",").map((t) => t.trim()).filter(Boolean);
    const extra = chosen.filter((t) => !options.includes(t));
    const boxes = options.map((option, i) => `
      <label class="ask-check">
        <input type="checkbox" data-ask-multi="${esc(key)}" value="${esc(option)}"
               ${chosen.includes(option) ? "checked" : ""}>
        <span>${esc(option)}</span>
      </label>`).join("");
    return `<div class="field ask-field" data-ask-group="${esc(key)}">
        <span>${esc(spec.label)}</span>
        <div class="ask-checks">${boxes}</div>
        <input class="ask-custom" data-ask-extra="${esc(key)}" type="text"
               placeholder="Anything else you can see"
               value="${esc(extra.join(", "))}">
        <input type="hidden" data-ask="${esc(key)}" value="${esc(current)}">
        ${help}
      </div>`;
  }

  /* One of a list, with a way out for anything genuinely not on it. */
  const onList = !current || options.includes(current);
  const choices = options.map((option) =>
    `<option value="${esc(option)}" ${option === current ? "selected" : ""}>${esc(option)}</option>`
  ).join("");
  return `<div class="field ask-field" data-ask-group="${esc(key)}">
      <span>${esc(spec.label)}</span>
      <select data-ask-select="${esc(key)}">
        <option value="">Choose</option>
        ${choices}
        <option value="${ASK_CUSTOM}" ${onList ? "" : "selected"}>Something else</option>
      </select>
      <input class="ask-custom" data-ask-extra="${esc(key)}" type="text"
             placeholder="${esc(spec.placeholder || "")}"
             value="${esc(onList ? "" : current)}"
             ${onList ? "hidden" : ""}>
      <input type="hidden" data-ask="${esc(key)}" value="${esc(current)}">
      ${help}
    </div>`;
}

/* Keeps the hidden field - the one the run actually reads - in step with
   whatever the farmer picked or typed. Delegated, so it covers the pre-run box
   and the per-agent modal without either having to remember to wire it up. */
function wireAskPickers(root) {
  if (!root || root.dataset.askWired === "1") return;
  root.dataset.askWired = "1";

  const sync = (key, value) => {
    const hidden = root.querySelector(`input[type=hidden][data-ask="${key}"]`);
    if (hidden) hidden.value = value;
  };

  root.addEventListener("change", (event) => {
    const select = event.target.closest("select[data-ask-select]");
    if (select) {
      const key = select.dataset.askSelect;
      const extra = root.querySelector(`input[data-ask-extra="${key}"]`);
      if (select.value === ASK_CUSTOM) {
        if (extra) { extra.hidden = false; extra.focus(); }
        sync(key, extra ? extra.value : "");
      } else {
        if (extra) extra.hidden = true;
        sync(key, select.value);
      }
      return;
    }

    const box = event.target.closest("input[data-ask-multi]");
    if (box) {
      const key = box.dataset.askMulti;
      const picked = Array.from(
        root.querySelectorAll(`input[data-ask-multi="${key}"]:checked`)
      ).map((el) => el.value);
      const extra = root.querySelector(`input[data-ask-extra="${key}"]`);
      const written = (extra ? extra.value : "").split(",")
        .map((t) => t.trim()).filter(Boolean);
      sync(key, picked.concat(written).join(", "));
    }
  });

  root.addEventListener("input", (event) => {
    const extra = event.target.closest("input[data-ask-extra]");
    if (!extra) return;
    const key = extra.dataset.askExtra;
    const boxes = Array.from(
      root.querySelectorAll(`input[data-ask-multi="${key}"]:checked`)
    ).map((el) => el.value);
    const written = extra.value.split(",").map((t) => t.trim()).filter(Boolean);
    sync(key, boxes.concat(written).join(", "));
  });
}

function openAskModal(agentId) {
  const keys = AGENT_ASKS[Number(agentId)] || [];
  if (!keys.length) return false;
  const name = (FLOW_STEPS.find((f) => f.id === Number(agentId)) || {}).name || `Agent ${agentId}`;
  $("#askModalTitle").textContent = name;
  $("#askModalSub").textContent =
    "This agent stopped rather than guess. Answer what you know - blanks are fine, "
    + "and anything you fill in is kept for later runs.";
  const known = knownAnswers();
  $("#askModalGrid").innerHTML = keys.map((key) =>
    askFieldHtml(key, pipelineAnswers[key] || known[key] || "",
                 key === "crop" && !!known.crop)).join("");
  wireAskPickers($("#askModalGrid"));
  /* Say the lot instead of tapping it. Wired every time the form is rebuilt,
     because the boxes it fills are rebuilt with it. */
  if (window.SaarthiFormVoice) {
    window.SaarthiFormVoice.attach($("#askModalVoice"), $("#askModalGrid"));
  }
  $("#askModal").hidden = false;
  document.body.classList.add("modal-open");
  const first = $("#askModalGrid select, #askModalGrid input:not([type=hidden])");
  if (first) first.focus();
  return true;
}

function closeAskModal() {
  $("#askModal").hidden = true;
  document.body.classList.remove("modal-open");
  $("#askModal").dataset.mode = "";
  $("#askModalRun").textContent = "Save and run the chain";
  $("#askModalCancel").textContent = "Cancel";
}

function collectAskModal() {
  /* Only the fields the run reads: a picker keeps its own hidden input in step,
     and reading the visible controls as well would file "Something else" as a
     crop name. */
  $$("#askModalGrid input[data-ask]").forEach((input) => {
    const value = input.value.trim();
    /* readonly, not disabled: a disabled input is left out of the form entirely,
       and the chain would then run with no crop at all. */
    /* An emptied box clears the stored answer rather than quietly keeping the old
       one - that is how a farmer changes their mind about last season's crop. */
    if (value) pipelineAnswers[input.dataset.ask] = value;
    else delete pipelineAnswers[input.dataset.ask];
  });
}

function wireAskModal() {
  $("#askModalClose").addEventListener("click", closeAskModal);
  $("#askModalCancel").addEventListener("click", () => {
    const prerun = $("#askModal").dataset.mode === "prerun";
    closeAskModal();
    if (prerun) runPipeline(pipelineAnswers);     // "Run without these"
  });
  $("#askModal").addEventListener("click", (e) => {
    if (e.target.id === "askModal") closeAskModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#askModal").hidden) closeAskModal();
  });
  $("#askModalRun").addEventListener("click", () => {
    collectAskModal();
    closeAskModal();
    runPipeline(pipelineAnswers);
  });
}

/* ---------------------------------------------------------------- the flow */
/* The fifteen agents as a track: START, two rows that double back, FINISH. Each stop
   has three states - waiting, running, done - and done is painted by what the server
   says finished, never by a timer. */
const FLOW_STEPS = [
  { id: 1, name: "Soil Sensor Acquisition and Data Quality", ico: "soil" },
  { id: 2, name: "Soil Health and Interpretation", ico: "flask" },
  { id: 3, name: "Crop Suitability and Selection", ico: "crop" },
  { id: 4, name: "Crop Growth Stage and Agronomy", ico: "leaf" },
  { id: 5, name: "Weather and Climate Risk", ico: "weather" },
  { id: 6, name: "Nutrient Management and Fertiliser", ico: "flask" },
  { id: 7, name: "Irrigation and Water Management", ico: "water" },
  { id: 8, name: "Pest and Disease Risk", ico: "bug" },
  { id: 9, name: "Weed Management", ico: "crop" },
  { id: 10, name: "Seed, Sowing and Crop Establishment", ico: "soil" },
  { id: 11, name: "Crop Protection and Safe Application", ico: "shield" },
  { id: 12, name: "Agricultural Market Intelligence", ico: "chart" },
  { id: 13, name: "Farm Economics and Profitability", ico: "chart" },
  { id: 14, name: "Sustainable Farming and Crop Residue", ico: "leaf" },
  { id: 15, name: "Integrated Advisory and Recommendation Validation", ico: "check" },
];

const STAGE_TEXT = {
  stopped: "Stopped before it finished - nothing was saved",
  failed: "Stopped because something failed; see the bell",
  idle: "Press Run pipeline and every agent runs over your farm, in this order",
  routing: "Working out what you asked and which agents it needs",
  gathering: "Fetching live weather, soil background, mandi prices and knowledge",
  agents: "Running the agents in dependency order",
  answering: "Writing the answer from what the agents found",
  done: "Finished",
};

function drawFlow(state) {
  const node = $("#agentFlow");
  if (!node) return;
  const done = new Set(state.done || []);
  const running = new Set(state.running || []);
  const statuses = state.statuses || {};

  const cell = (step) => {
    const cls = done.has(step.id) ? "done" : running.has(step.id) ? "running" : "waiting";
    const outcome = statuses[String(step.id)] || "";
    const label = { ok: "", needs_input: "needs an answer from you",
                    unavailable: "could not run", error: "failed" }[outcome] || "";
    return `<div class="flow-step ${cls} ${esc(outcome)}" data-agent="${step.id}"
                 title="${esc(step.name)}">
        <span class="fs-ring">
          <span class="fs-ico">${icon(step.ico, 18)}</span>
          <span class="fs-num">${step.id}</span>
        </span>
        <span class="fs-body">
          <b class="fs-name">${esc(step.name)}</b>
          ${label ? `<small class="fs-note">${esc(label)}</small>` : ""}
        </span>
      </div>`;
  };

  const startCls = (state.stage && state.stage !== "idle") ? "done" : "waiting";
  const finishCls = state.stage === "done" ? "done" : "waiting";

  const arrow = '<span class="flow-arrow" aria-hidden="true">'
    + '<svg viewBox="0 0 24 12" width="20" height="10"><path d="M0 6h17M13 1l5 5-5 5" '
    + 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    + 'stroke-linejoin="round"/></svg></span>';

  /* Two rows, both left to right. The earlier version ran the second row backwards
     so the connector doubled back on itself, and the order had to be worked out
     rather than read. The wrap is marked instead. */
  /* Eight and seven, so agent 9 sits under agent 1 rather than under the START cap.
     The first row carries START, which takes one slot, so the rows only line up if
     the second one is a step shorter. */
  const row1 = FLOW_STEPS.slice(0, 8);
  const row2 = FLOW_STEPS.slice(8);
  const cap = (cls, ico, name) =>
    `<div class="flow-cap ${cls}">
       <span class="fs-ring"><span class="fs-ico">${icon(ico, 17)}</span></span>
       <span class="fs-name">${name}</span>
     </div>`;

  node.innerHTML =
    `<div class="flow-row">
       ${cap(startCls, "spark", "START")}${arrow}
       ${row1.map((st) => cell(st)).join(arrow)}
     </div>
     <div class="flow-row">
       <span class="flow-spacer" aria-hidden="true"></span>
       ${row2.map((st) => cell(st)).join(arrow)}${arrow}
       ${cap(finishCls, "check", "FINISH")}
     </div>`;

  $("#flowStage").textContent = (STAGE_TEXT[state.stage] || STAGE_TEXT.routing)
    + (state.agent_ms !== undefined
       ? ` - the agents themselves took ${state.agent_ms} ms` : "");
}

/* Polls while a run is in flight. Stops as soon as the server says the run is over,
   so a finished page is not left asking for progress that no longer exists. */
function startFlowPolling(token) {
  drawFlow({ stage: "routing", done: [], running: [] });

  let stopped = false;
  let truth = { stage: "routing", done: [], running: [], statuses: {} };
  lastProgress = truth;
  const revealed = new Set();
  let revealIndex = 0;

  /* The reveal order is the chain's own order, so what the farmer watches is the
     dependency order the agents really ran in. */
  const order = FLOW_STEPS.map((f) => f.id);

  const poll = async () => {
    if (stopped) return;
    try {
      const state = await API.get("/api/pipeline/progress?run_token=" + encodeURIComponent(token));
      if (state.live) { truth = state; lastProgress = state; }
    } catch (e) { /* a dropped poll must not break the run */ }
    if (!stopped) setTimeout(poll, 400);
  };

  /* One tick, one or two more lights - but only ones the server has confirmed. */
  const tick = () => {
    if (stopped) return;
    const done = new Set(truth.done || []);
    let added = 0;
    while (revealIndex < order.length && added < 2) {
      const id = order[revealIndex];
      if (!done.has(id)) break;              // not finished yet: wait for the server
      revealed.add(id);
      revealIndex += 1;
      added += 1;
    }
    const next = order[revealIndex];
    drawFlow({
      stage: truth.stage,
      done: [...revealed],
      running: next !== undefined && done.has(next) ? [next]
        : (truth.running || []).filter((id) => !revealed.has(id)),
      statuses: truth.statuses,
    });
    if (!stopped) setTimeout(tick, 170);
  };

  poll();
  tick();
  return () => { stopped = true; };
}

/* ------------------------------------------------------- pipeline / agents */
const STATUS_LABEL = { ok: "ok", needs_input: "needs input", unavailable: "unavailable", error: "error" };

function statusClass(status) {
  return status === "ok" ? "" : status === "needs_input" ? "warn" : "bad";
}

/* renderAgentBoard removed with the two cards it drew. */
let pipelineRunning = false;
/* Answers typed into the boxes the last run put up. Kept between runs so a farmer
   who fills in a sowing date once does not retype it on every run. */
const pipelineAnswers = {};
let pipelineToken = "";
let pipelineAbort = null;
/* What the server last confirmed, so a failure can freeze the chain there. */
let lastProgress = { done: [], running: [], statuses: {} };

async function stopPipeline() {
  if (!pipelineRunning) return;
  /* Two halves, and both are needed. Aborting the fetch frees the page immediately;
     the POST tells the server to cancel the task, which otherwise keeps running with
     a model call open long after nobody is waiting for it. */
  if (pipelineAbort) pipelineAbort.abort();
  try {
    await fetch("/api/pipeline/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_token: pipelineToken }),
    });
  } catch (e) { /* the page is already free either way */ }
  toast("Run stopped.");
}

/* What the chain would want to know, before it is asked to run.

   Seeded from the last run and falling back to the fixed list, so even a browser that
   has never run the chain can be asked the sensible questions rather than finding out
   the hard way. */
function pendingAskKeys() {
  const fromLastRun = Object.keys(askSpecs);
  if (fromLastRun.length) return fromLastRun;
  return [...new Set(Object.values(AGENT_ASKS).flat())];
}

function openPreRunModal() {
  const keys = pendingAskKeys();
  if (!keys.length) return false;
  $("#askModalTitle").textContent = "Before the chain runs";
  $("#askModalSub").textContent =
    "Two agents cannot answer without these. Everything is optional - leave a box "
    + "empty and that agent will say what it is missing instead of guessing.";
  const known = knownAnswers();
  $("#askModalGrid").innerHTML = keys.map((key) =>
    askFieldHtml(key, pipelineAnswers[key] || known[key] || "",
                 key === "crop" && !!known.crop)).join("");
  $("#askModalRun").textContent = "Run the chain";
  $("#askModalCancel").textContent = "Run without these";
  $("#askModal").dataset.mode = "prerun";
  wireAskPickers($("#askModalGrid"));
  /* Say the lot instead of tapping it. Wired every time the form is rebuilt,
     because the boxes it fills are rebuilt with it. */
  if (window.SaarthiFormVoice) {
    window.SaarthiFormVoice.attach($("#askModalVoice"), $("#askModalGrid"));
  }
  $("#askModal").hidden = false;
  document.body.classList.add("modal-open");
  const first = $("#askModalGrid select, #askModalGrid input:not([type=hidden])");
  if (first) first.focus();
  return true;
}

async function runPipeline(answers) {
  if (pipelineRunning) { toast("A pipeline run is already in progress"); return; }
  /* A plain press of Run asks first. Passing answers in - from either modal, or from
     the panel on the results page - means they have already been collected. */
  if (answers === undefined && openPreRunModal()) return;
  pipelineRunning = true;
  pipelineToken = "run-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  pipelineAbort = new AbortController();
  $("#stopPipeline").hidden = false;
  const stopFlow = startFlowPolling(pipelineToken);
  const buttons = $$(".run-pipe");
  buttons.forEach((b) => { b.disabled = true; b.dataset.label = b.innerHTML; });
  const started = Date.now();
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    buttons.forEach((b) => { b.innerHTML = `<span class="pulse on"></span>Running 15 agents... ${s}s`; });
  }, 500);
  /* Overview, not the results page: the chain is the thing to watch while it runs,
     and the results are a click away once there is something to read. */
  go("overview");
  $("#flowShowResult").hidden = true;
  /* Let the page settle on Overview first, then bring the chain into view. A jump
     straight to it loses the farmer: they press a button and the screen is somewhere
     else with no sense of how they got there. */
  setTimeout(() => {
    const card = $("#pipelineFlowCard");
    if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 260);
  const empty = $("#pipelineEmpty");
  if (empty) empty.innerHTML =
    `<div class="typing"><i></i><i></i><i></i></div>
     <b>Running all 15 agents</b>
     <small>Fetching live weather, background soil data and market records, retrieving
       knowledge, then running the chain in dependency order.</small>`;
  if (empty) empty.hidden = false;
  if ($("#pipelineBody")) $("#pipelineBody").hidden = true;

  try {
    const response = await fetch("/api/pipeline/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: pipelineAbort.signal,
      body: JSON.stringify({
        language: state.language,
        run_token: pipelineToken,
        answers: answers || pipelineAnswers,
      }),
    });
    const text = await response.text();
    let data; try { data = JSON.parse(text); } catch { data = { detail: text }; }
    if (!response.ok) throw new Error(data.detail || response.statusText);
    drawFlow({
      stage: "done",
      done: (data.agent_results || []).map((r) => r.agent_id),
      running: [],
      statuses: Object.fromEntries((data.agent_results || []).map((r) => [r.agent_id, r.status])),
      agent_ms: (data.timings || {}).agents_ms,
    });
    renderPipeline(data);
    $("#flowShowResult").hidden = false;
    toast(`Pipeline finished: ${data.summary.agents_run} agents in ${(data.duration_ms / 1000).toFixed(1)}s`);
  } catch (e) {
    const stopped = e.name === "AbortError";
    /* Freeze the chain where it actually got to, rather than leaving it half-lit
       with no explanation. What is already green really did finish. */
    drawFlow({ stage: stopped ? "stopped" : "failed",
               done: lastProgress.done || [], running: [],
               statuses: lastProgress.statuses || {} });
    notify(stopped ? "warn" : "error",
           stopped ? "Run stopped" : "Run aborted",
           stopped ? "You stopped it. Nothing was saved."
                   : `The chain stopped at ${(lastProgress.done || []).length} of 15. `
                     + e.message);
    $("#flowStage").textContent = stopped ? "Stopped" : "Could not finish: " + e.message;
    if (empty) empty.innerHTML = stopped
      ? `<b>Run stopped</b><small>Nothing was saved from it. Press Run pipeline to start again.</small>`
      : `<b>Pipeline failed</b><small>${esc(e.message)}</small>`;
    if (empty) empty.hidden = false;
  } finally {
    clearInterval(tick);
    stopFlow();
    $("#stopPipeline").hidden = true;
    pipelineAbort = null;
    buttons.forEach((b) => { b.disabled = false; b.innerHTML = b.dataset.label; });
    pipelineRunning = false;
  }
}

const STATUS_COLOR = { ok: "#2f9e4f", needs_input: "#d9930c", unavailable: "#c6513f", error: "#c6513f" };
const PROVENANCE_COLOR = {
  real_sensor: "#2f9e4f", simulated: "#d9930c", lab: "#1f7a3f", live_api: "#2f80d6",
  knowledge_base: "#6b57c9", farmer_reported: "#4aa3c7", estimated: "#9aa89d",
  unavailable: "#c6513f",
};

let lastPipelineData = null;


/* The boxes for everything an agent asked for. The agents ask in sentences, which is
   right on a phone call and useless in a form, so the backend declares a named input
   per question and this renders one field each. */

/* ---------------------------------------------------- the plain-words result */
/* The chain produces fifteen structured reports. A farmer wants two things out of
   that: what to do, and what was found. Everything else on this page is how it was
   worked out, which is a different question and now sits behind a fold. */

const SEVERITY_RANK = { high: 0, medium: 1, low: 2, none: 3 };
const URGENCY = {
  high: { label: "Do this now", cls: "urgent" },
  medium: { label: "This week", cls: "soon" },
  low: { label: "Worth doing", cls: "later" },
  none: { label: "Keep in mind", cls: "later" },
};

/* One tile per part of the farm, coloured by the worst thing the relevant agents
   found. The mapping is editorial and lives here, because the agents report facts
   and it is this page that decides a farmer thinks in "water", not "agent 7". */
const SCORE_PARTS = [
  { key: "soil",     label: "Soil",      ico: "soil",    agents: [1, 2] },
  { key: "crop",     label: "Crop",      ico: "crop",    agents: [3, 4, 10] },
  { key: "water",    label: "Water",     ico: "water",   agents: [7] },
  { key: "weather",  label: "Weather",   ico: "sun",     agents: [5] },
  { key: "nutrient", label: "Nutrients", ico: "flask",   agents: [6] },
  { key: "threats",  label: "Pests and weeds", ico: "bug", agents: [8, 9, 11] },
  { key: "money",    label: "Money",     ico: "chart",   agents: [12, 13] },
  { key: "land",     label: "Residue",   ico: "leaf",    agents: [14] },
];

const SCORE_RANK = { high: 3, medium: 2, low: 1, none: 0 };

function renderScorecard(data) {
  const byId = {};
  (data.agent_results || []).forEach((r) => { byId[r.agent_id] = r; });

  const tiles = SCORE_PARTS.map((part) => {
    const mine = part.agents.map((id) => byId[id]).filter(Boolean);
    if (!mine.length) return null;

    const stalled = mine.filter((r) => r.status !== "ok");
    const worst = mine.reduce((acc, r) =>
      (SCORE_RANK[r.severity] ?? 0) > (SCORE_RANK[acc.severity] ?? 0) ? r : acc, mine[0]);

    /* "Waiting" is its own state, not a severity. An agent that refused to guess has
       found nothing wrong and nothing right, and colouring it green would say the
       part of the farm it covers is fine when nobody has looked. */
    const state = stalled.length === mine.length ? "waiting"
      : worst.severity === "high" ? "bad"
      : worst.severity === "medium" ? "warn" : "good";
    const line = state === "waiting"
      ? (stalled[0].headline || "Waiting on an answer from you")
      : (worst.headline || "Nothing to report.");
    return { ...part, state, line, agent: worst.agent_id };
  }).filter(Boolean);

  const bad = tiles.filter((t) => t.state === "bad").length;
  const warn = tiles.filter((t) => t.state === "warn").length;
  const waiting = tiles.filter((t) => t.state === "waiting").length;
  $("#scoreNote").textContent = bad || warn
    ? `${bad + warn} thing(s) need attention` + (waiting ? `, ${waiting} waiting on you` : "")
    : waiting ? `Nothing wrong so far; ${waiting} still waiting on you`
    : "Nothing needs attention right now";

  $("#scoreCard").innerHTML = tiles.map((t) => `
    <article class="score ${t.state}" data-agent="${t.agent}" title="${esc(t.line)}">
      <span class="sc-ico">${icon(t.ico, 20)}</span>
      <b>${esc(t.label)}</b>
      <small>${esc(t.line)}</small>
    </article>`).join("");
}

/* At most five steps, most serious first, written as instructions. Eighteen bullets
   grouped by urgency was complete and unusable; the rest are still on each agent in
   the fold below. */
function renderPlan(data) {
  const results = (data.agent_results || []).filter((r) => (r.actions || []).length);
  const ranked = results.slice().sort((a, b) =>
    (SCORE_RANK[b.severity] ?? 0) - (SCORE_RANK[a.severity] ?? 0)
    || (b.confidence || 0) - (a.confidence || 0));

  const seen = new Set();
  const steps = [];
  for (const r of ranked) {
    for (const action of (r.actions || []).slice(0, 1)) {     // one per agent
      const key = action.trim().toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      steps.push({ action, agent: r.agent_name, id: r.agent_id, severity: r.severity });
      break;
    }
    if (steps.length >= 5) break;
  }

  const urgent = steps.filter((x) => x.severity === "high").length;
  $("#planSummary").textContent = steps.length
    ? (urgent ? `${urgent} of these cannot wait.`
              : "Nothing urgent - work through them when you can.")
      + " Every agent's full list is in the detail below."
    : "No action came out of this run.";

  $("#planList").innerHTML = steps.length
    ? steps.map((step, index) => `
        <div class="step ${esc(step.severity || "none")}">
          <span class="step-no">${index + 1}</span>
          <div>
            <p>${esc(step.action)}</p>
            <span class="step-who" data-agent="${step.id}">${esc(step.agent)}</span>
          </div>
        </div>`).join("")
    : `<p class="hint">The agents had nothing to recommend, which usually means
        something they needed was missing.</p>`;
}

function renderFindings(data) {
  /* Most serious first, so a problem is not three scrolls below a reassurance. */
  const results = (data.agent_results || []).slice().sort((a, b) =>
    (SEVERITY_RANK[a.severity] ?? 3) - (SEVERITY_RANK[b.severity] ?? 3)
    || a.agent_id - b.agent_id);

  $("#findingList").innerHTML = results.map((r) => {
    const stalled = r.status !== "ok";
    const line = r.headline || (r.findings || [])[0] || "Nothing to report.";
    return `<div class="finding ${esc(r.severity || "none")} ${stalled ? "stalled" : ""}"
                 data-agent="${r.agent_id}">
        <span class="f-dot"></span>
        <div>
          <b>${esc(r.agent_name)}</b>
          <small>${esc(line)}</small>
          ${stalled ? `<em class="f-need">needs an answer from you</em>` : ""}
        </div>
      </div>`;
  }).join("");
}

function renderPipelineAsk(needed) {
  /* The questions are asked once, in the box that opens before the chain runs.
     A second copy of the same form further down the results page meant the farmer
     met it on every screen, which is what made it feel like nagging. All this does
     now is remember the labels so that box can be built. */
  rememberAskSpecs(needed);
}

function renderPipeline(data) {
  if (!data || data.available === false) {
    $("#pipelineEmpty").hidden = false;
    $("#pipelineBody").hidden = true;
    return;
  }
  lastPipelineData = data;
  $("#pipelineEmpty").hidden = true;
  $("#pipelineBody").hidden = false;

  const s = data.summary || {};
  const results = data.agent_results || [];
  const t = data.timings || {};
  const docCount = new Set((data.retrieved || []).map((r) => r.doc_id)).size;
  $("#pipelineSubtitle").textContent =
    `${s.ok} of ${results.length} agents answered`
    + (s.needs_input ? `, ${s.needs_input} waiting on you` : "");

  /* ---- headline tiles ---- */

  /* ---- the farm first, the machinery after ---- */
  renderScorecard(data);
  renderPlan(data);
  renderFindings(data);

  /* ---- what the chain still needs from the farmer ---- */
  renderPipelineAsk(s.needed_inputs || []);

  /* ---- outcome donut ---- */
  $("#chartStatus").innerHTML = donutChart([
    { label: "Completed", value: s.ok || 0, color: STATUS_COLOR.ok },
    { label: "Need input", value: s.needs_input || 0, color: STATUS_COLOR.needs_input },
    { label: "Unavailable", value: s.unavailable || 0, color: STATUS_COLOR.unavailable },
    { label: "Errors", value: s.errors || 0, color: "#7a2f22" },
  ], s.agents_run, "agents") +
    `<p class="hint">"Need input" means an agent refused to guess and asked you a question instead.</p>`;

  /* ---- provenance donut ---- */
  const counts = (data.validation && data.validation.provenance_counts) || data.provenance_summary || {};
  $("#chartProvenance").innerHTML = donutChart(
    Object.entries(counts).map(([k, v]) => ({
      label: k.replace(/_/g, " "), value: v, color: PROVENANCE_COLOR[k] || "#9aa89d",
    })), Object.values(counts).reduce((a, b) => a + b, 0), "sources") +
    `<p class="hint">Every finding carries its source, so a simulated reading cannot pass as a measurement.</p>`;

  /* ---- phase timing ---- */
  $("#chartPhases").innerHTML = stackedBar([
    { label: "Fetching data", value: t.gather_ms || 0, display: `${((t.gather_ms || 0) / 1000).toFixed(1)}s`, color: "#2f80d6" },
    { label: "15 agents", value: Math.max(t.agents_ms || 0, 1), display: `${t.agents_ms || 0} ms`, color: "#2f9e4f" },
    { label: "Writing answer", value: t.answer_ms || 0, display: `${((t.answer_ms || 0) / 1000).toFixed(1)}s`, color: "#6b57c9" },
  ], "The agents are the cheap part. Weather, soil and market lookups plus the language model are the wall clock.");

  /* ---- status matrix ---- */
  $("#agentMatrix").innerHTML = results.map((r) => {
    const cls = r.status === "ok" ? "ok" : r.status === "needs_input" ? "warn" : "bad";
    const ring = Math.round((r.confidence || 0) * 100);
    return `<button class="matrix-cell ${cls}" data-agent="${r.agent_id}"
        data-status="${esc(r.status)}" title="${esc(r.agent_name)}">
        <span class="mc-num">${r.agent_id}</span>
        <span class="mc-name">${esc(r.agent_name.split(" and ")[0])}</span>
        <span class="mc-ring"><i style="width:${ring}%"></i></span>
        <span class="mc-pct">${ring}%</span>
        ${r.severity && r.severity !== "none" ? `<span class="mc-sev ${esc(r.severity)}"></span>` : ""}
      </button>`;
  }).join("");
  $("#agentDrawer").hidden = true;

  /* ---- confidence bars ---- */
  $("#chartConfidence").innerHTML = hBars(results.map((r) => ({
    agent: r.agent_id,
    label: `${r.agent_id}. ${r.agent_name.split(" and ")[0]}`,
    value: Math.round((r.confidence || 0) * 100),
    display: `${Math.round((r.confidence || 0) * 100)}%`,
    color: STATUS_COLOR[r.status] || "#9aa89d",
  })), { max: 100 });

  /* ---- validation checks as compact tiles ---- */
  const v = data.validation || {};
  $("#validationGrid").innerHTML = [
    { label: "Cross-checked", value: `${v.agents_checked || 0} agents`, ok: true },
    { label: "Conflicts", value: v.conflicts_found || 0, ok: !v.conflicts_found },
    { label: "Simulated data", value: v.simulated_data_used ? "used" : "none", ok: !v.simulated_data_used },
    { label: "Stale inputs", value: v.stale_inputs ? "yes" : "none", ok: !v.stale_inputs },
    { label: "Escalation", value: v.escalation_required ? "required" : "not needed", ok: !v.escalation_required },
    { label: "Missing inputs", value: (v.missing_inputs || []).length, ok: !(v.missing_inputs || []).length },
  ].map((c) => `<div class="check-tile ${c.ok ? "" : "flag"}">
      <span class="ct-ico">${c.ok ? "✓" : "!"}</span>
      <b>${esc(c.value)}</b><small>${esc(c.label)}</small>
    </div>`).join("");

  /* ---- answer + warning flags ---- */
  $("#pipelineAnswer").textContent = data.answer || "";
  $("#pipelineAnswer").classList.add("clamped");
  $("#toggleAnswer").textContent = "Show full text";
  $("#pipelineAnswerMeta").textContent = data.answer_source === "llm"
    ? `Written by ${data.answer_model} strictly from the validated findings`
    : "Assembled by the rule engine (no Mistral key)";
  $("#pipelineWarnings").innerHTML = (data.warnings || []).slice(0, 4).map((w) =>
    `<span class="flag" title="${esc(w)}">${esc(w.slice(0, 70))}${w.length > 70 ? "..." : ""}</span>`).join("");

  /* ---- open questions as chips ---- */
  $("#pipelineQuestions").innerHTML = (s.open_questions || []).map((q) =>
    `<button class="qchip" data-ask="${esc(q)}">${esc(q)}</button>`).join("")
    || `<p class="hint">Nothing outstanding - every agent had what it needed.</p>`;

  /* ---- retrieved knowledge as relevance bars ---- */
  const docs = new Map();
  (data.retrieved || []).forEach((r) => {
    if (!docs.has(r.doc_id) || docs.get(r.doc_id).score < r.score) {
      docs.set(r.doc_id, { title: r.title, score: r.score });
    }
  });
  $("#chartSources").innerHTML = hBars([...docs.values()].map((d) => ({
    label: d.title, value: Math.round(d.score * 100), display: d.score.toFixed(2), color: "#6b57c9",
  })), { max: 100, empty: "No passages retrieved" });
}

/* Detail drawer: the long text lives here, shown only for the agent you click. */
function showAgentDetail(agentId) {
  const data = lastPipelineData;
  if (!data) return;
  const r = (data.agent_results || []).find((x) => x.agent_id === Number(agentId));
  const drawer = $("#agentDrawer");
  if (!r) { drawer.hidden = true; return; }
  $$("#agentMatrix .matrix-cell").forEach((c) =>
    c.classList.toggle("selected", c.dataset.agent === String(agentId)));

  const list = (title, items, cls) => (items && items.length)
    ? `<div class="drawer-block ${cls || ""}"><h4>${title}</h4><ul>${items.map((i) =>
        `<li>${esc(typeof i === "string" ? i : (i.source + (i.detail ? " - " + i.detail : "")))}</li>`).join("")}</ul></div>`
    : "";

  drawer.hidden = false;
  drawer.innerHTML = `
    <div class="drawer-head">
      <span class="num">${r.agent_id}</span>
      <div><b>${esc(r.agent_name)}</b><small>${esc(r.headline || "")}</small></div>
      <span class="head-note ${statusClass(r.status)}">${esc(STATUS_LABEL[r.status] || r.status)}</span>
      <button class="icon-btn" id="closeDrawer" aria-label="Close">&times;</button>
    </div>
    <div class="drawer-grid">
      ${list("Findings", r.findings)}
      ${list("Actions", r.actions)}
      ${list("Needs from you", r.questions, "warn")}
      ${list("Warnings", r.warnings, "warn")}
      ${list("Assumptions", r.assumptions, "muted")}
      ${list("Evidence", r.evidence, "muted")}
    </div>
    <div class="drawer-foot">
      <span>confidence ${Math.round((r.confidence || 0) * 100)}%</span>
      <span>severity ${esc(r.severity)}</span>
      <span>provenance ${esc((r.provenance || []).join(", ") || "none")}</span>
      <span>${r.duration_ms} ms</span>
    </div>`;
  $("#closeDrawer").addEventListener("click", () => {
    drawer.hidden = true;
    $$("#agentMatrix .matrix-cell").forEach((c) => c.classList.remove("selected"));
  });
}

RENDERERS.pipeline = async function renderPipelinePage() {
  if (pipelineRunning) return;
  const data = await API.get("/api/pipeline/last");
  renderPipeline(data);
};

/* ----------------------------------------------------------------- soil */

RENDERERS.soil = async function renderSoil() {
  const parcel = activeParcel();
  const data = await API.get("/api/sensors/latest"
    + (parcel ? "?parcel_id=" + encodeURIComponent(parcel) : ""));
  const badge = $("#soilModeBadge") || {};
  if (!data.available) {
    badge.textContent = "No sensor reading";
    badge.className = "head-note warn";
    $("#soilGauge").innerHTML = `<div class="empty-chart">No reading yet</div>`;
    $("#paramGrid").innerHTML = `<p class="hint">Generate a reading on the Sensor Devices page, or connect the Android gateway.</p>`;
    $("#soilChecks").innerHTML = "";
    $("#soilTrend").innerHTML = `<div class="empty-chart">No readings stored</div>`;
    return;
  }
  const simulated = data.simulated;
  badge.textContent = simulated ? "SIMULATED DATA" : "Real sensor connected";
  badge.className = "head-note " + (simulated ? "warn" : "");

  const overview = await API.get("/api/dashboard/overview");
  const index = overview.soil || {};
  $("#soilGauge").innerHTML = index.available
    ? donut(index.index, index.label, simulated ? "simulated" : "measured")
    : `<div class="empty-chart">Index unavailable</div>`;
  $("#soilMethod").textContent = index.method || "";
  $("#soilUpdated").textContent = "Recorded " + String(data.reading.recorded_at || "").replace("T", " ").slice(0, 16)
    + " - " + (simulated ? "simulation mode" : "real sensor mode");

  const unitIco = { ph: "flask", ec_ds_m: "chip", moisture_pct: "water", temperature_c: "sun" };
  $("#paramGrid").innerHTML = (data.parameters || []).map((p) => `
    <div class="param">
      <div class="p-top">
        <span class="p-ico">${p.field.startsWith("nitrogen") ? "N" : p.field.startsWith("phosph") ? "P"
          : p.field.startsWith("potass") ? "K" : icon(unitIco[p.field] || "soil", 14)}</span>
        <span class="p-band ${esc(p.band)}">${esc(p.band)}</span>
      </div>
      <small>${esc(p.label)}</small>
      <b>${p.value === null ? "--" : esc(num(p.value, p.field === "ph" || p.field === "ec_ds_m" ? 2 : 1))}
        <span style="font-size:11px;color:#6c7f71">${esc(p.unit)}</span></b>
      <small>typical ${p.typical_range[0]} - ${p.typical_range[1]}</small>
    </div>`).join("");

  const v = data.validation || {};
  const checks = [
    { ok: v.status === "valid", text: `Validation status: ${v.status} (${(v.completeness * 100).toFixed(0)}% of fields usable)` },
    { ok: !v.stale, text: v.stale ? "Reading is stale" : `Reading is fresh (${v.age_seconds}s old)` },
    { ok: !simulated, text: simulated ? "SIMULATED data - not a measurement of your field" : "Real sensor provenance" },
    { ok: !(v.invalid_fields || []).length, text: (v.invalid_fields || []).length ? "Out-of-range fields dropped: " + v.invalid_fields.join(", ") : "No out-of-range values" },
    { ok: false, warn: true, text: "Register map: " + (v.register_map_status || "unknown") },
  ];
  $("#soilChecks").innerHTML = checks.map((c) => `
    <li><span class="tick ${c.warn ? "warn" : c.ok ? "" : "bad"}">${c.warn ? "!" : c.ok ? "✓" : "!"}</span>${esc(c.text)}</li>`).join("");

  const params = (data.parameters || []).map((p) => `<option value="${p.field}">${esc(p.label)}</option>`).join("");
  const sel = $("#trendParam");
  if (sel.innerHTML !== params) { sel.innerHTML = params; sel.value = "moisture_pct"; }
  await drawSoilTrend(sel.value);
};

async function drawSoilTrend(field) {
  const history = await API.get(
    "/api/sensors/history?limit=30&parcel_id=" + encodeURIComponent(activeParcel()));
  const pts = (history.series || []).map((s) => ({ label: (s.recorded_at || "").slice(11, 16), value: s[field] }));
  $("#soilTrend").innerHTML = lineChart([{ name: field, color: "#2f9e4f", points: pts }],
    { empty: "No stored readings yet" });
}

/* -------------------------------------------------------------- weather */

/* A forecast matters here only because of what it stops or allows. These are the four
   decisions weather actually drives on a smallholding, each answered from the same
   figures the charts below are drawn from - no second source, no model. */
function renderWeatherVerdict(w) {
  const node = $("#weatherVerdict");
  if (!node) return;
  if (!w.available) {
    node.innerHTML = `<p class="hint">No forecast right now, so nothing here would be
      better than a guess. ${esc(w.reason || "")}</p>`;
    return;
  }
  const days = (w.forecast || []).slice(0, 5);
  const rain48 = days.slice(0, 2).reduce((a, d) => a + Number(d.rain_mm || 0), 0);
  const rain5 = days.reduce((a, d) => a + Number(d.rain_mm || 0), 0);
  const highs = days.map((d) => Number(d.t_max)).filter((n) => !Number.isNaN(n));
  const lows = days.map((d) => Number(d.t_min)).filter((n) => !Number.isNaN(n));
  const wind = Number((w.current || {}).wind_kmph || 0);
  const maxHigh = highs.length ? Math.max(...highs) : null;
  const minLow = lows.length ? Math.min(...lows) : null;

  const items = [
    {
      title: "Spraying",
      ok: rain48 < 5 && wind < 15,
      yes: "Fine for the next two days - little rain and light wind.",
      no: rain48 >= 5
        ? `Hold off: ${rain48.toFixed(1)} mm of rain is expected in 48 hours and it will wash the spray off.`
        : `Hold off: wind is ${wind.toFixed(0)} km/h and the spray will drift.`,
    },
    {
      title: "Irrigation",
      ok: rain5 < 10,
      yes: "No useful rain coming, so irrigate on your normal schedule.",
      no: `${rain5.toFixed(1)} mm expected over five days - wait and see before watering.`,
    },
    {
      title: "Heat stress",
      ok: maxHigh === null || maxHigh < 38,
      yes: maxHigh === null ? "No temperature data." : `Peak ${maxHigh.toFixed(0)}\u00b0C - within the normal range.`,
      no: `Peak ${maxHigh === null ? "?" : maxHigh.toFixed(0)}\u00b0C - water earlier in the day and avoid midday work.`,
    },
    {
      title: "Frost",
      ok: minLow === null || minLow > 4,
      yes: minLow === null ? "No temperature data." : `Lowest ${minLow.toFixed(0)}\u00b0C - no frost risk.`,
      no: `Lowest ${minLow === null ? "?" : minLow.toFixed(0)}\u00b0C - frost is possible; irrigate the evening before.`,
    },
  ];

  node.innerHTML = items.map((i) => `
    <div class="verdict ${i.ok ? "good" : "watch"}">
      <span class="v-mark">${i.ok ? "&#10003;" : "!"}</span>
      <div><b>${esc(i.title)}</b><small>${esc(i.ok ? i.yes : i.no)}</small></div>
    </div>`).join("");
}


function browserPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) { reject(new Error("no geolocation")); return; }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude.toFixed(5),
                         lon: pos.coords.longitude.toFixed(5) }),
      reject, { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 });
  });
}

RENDERERS.weather = async function renderWeather() {
  /* Default is the land, not the phone. A farmer checking the forecast for their
     wheat does not want the weather where they happen to be standing - and the one
     time that matters is exactly when they are away from the field. */
  let query = "/api/weather?days=7";
  let where = "your registered land";
  if ($("#weatherWhere") && $("#weatherWhere").value === "me") {
    const here = await browserPosition().catch(() => null);
    if (here) {
      query += `&lat=${here.lat}&lon=${here.lon}`;
      where = "where you are now";
    } else {
      $("#weatherWhere").value = "farm";
      toast("Could not get your location - showing the forecast for your land.");
    }
  }
  const w = await API.get(query);
  const src = $("#weatherSource") || {};
  if (src) src.textContent = "Open-Meteo - " + where;
  renderWeatherVerdict(w);
  const cur0 = w.current || {};
  const days0 = (w.forecast || []).slice(0, 7);
  const rain7 = days0.reduce((a, d) => a + Number(d.rain_mm || 0), 0);
  const highs0 = days0.map((d) => Number(d.t_max)).filter((n) => !Number.isNaN(n));
  if (!w.available) {
    $("#weatherNow").innerHTML = `<div class="empty-state"><b>Weather unavailable</b><small>${esc(w.reason || "")}</small></div>`;
    $("#forecastRow").innerHTML = "";
    $("#tempChart").innerHTML = `<div class="empty-chart">No forecast</div>`;
    $("#rainChart").innerHTML = `<div class="empty-chart">No forecast</div>`;
    $("#weatherAlerts").innerHTML = "";
    src.className = "head-note bad";
    src.textContent = "Unavailable";
    return;
  }
  src.className = "head-note";
  src.textContent = `${w.source} - updated ${w.age_seconds}s ago`;
  const c = w.current || {};
  $("#weatherNow").innerHTML = `
    <div class="wn-top">${icon("sun", 42)}
      <div><div class="wn-temp">${num(c.temperature_c, 0)}&deg;C</div>
      <div class="wn-cond">${esc(c.condition || "")}</div></div></div>
    <div class="wn-stats">
      <div class="wn-stat"><small>Humidity</small><b>${num(c.humidity_pct, 0)}%</b></div>
      <div class="wn-stat"><small>Wind</small><b>${num(c.wind_kmh, 0)} km/h</b></div>
      <div class="wn-stat"><small>Rain now</small><b>${num(c.precipitation_mm, 1)} mm</b></div>
    </div>
    <p class="hint">Observed at ${esc(c.observed_at || "")} - ${esc(w.timezone || "")}</p>`;

  const days = (w.forecast || []).slice(0, 5);
  const dayName = (d) => new Date(d).toLocaleDateString(undefined, { weekday: "short" });
  $("#forecastRow").innerHTML = days.map((d, i) => `
    <div class="fc"><div class="fc-day">${i === 0 ? "Today" : dayName(d.date)}</div>
      ${icon(Number(d.rain_probability) > 50 ? "weather" : "sun", 22)}
      <b>${num(d.t_max, 0)}&deg;</b><small>min ${num(d.t_min, 0)}&deg;</small>
      <small>${num(d.rain_probability, 0)}% rain</small></div>`).join("");

  $("#tempChart").innerHTML = lineChart([
    { name: "Max °C", color: "#d9930c", points: days.map((d) => ({ label: dayName(d.date), value: d.t_max })) },
    { name: "Min °C", color: "#2f80d6", points: days.map((d) => ({ label: dayName(d.date), value: d.t_min })) },
  ]);
  $("#rainChart").innerHTML = barChart(days.map((d) => ({
    label: dayName(d.date), value: d.rain_probability || 0, display: `${num(d.rain_probability, 0)}%`, color: "#2f80d6",
  })), { min: 100 });

  const trace = await API.post("/api/agents/run", { question: "What is the weather risk for my farm this week?", language: state.language });
  const agent5 = (trace.agent_results || []).find((r) => r.agent_id === 5);
  const alerts = [];
  if (agent5) {
    (agent5.data.risks || []).forEach((r) => alerts.push({ cls: "warn", title: r, detail: agent5.headline }));
    (agent5.actions || []).forEach((a) => alerts.push({ cls: "", title: "Recommended", detail: a }));
  }
  $("#weatherAlerts").innerHTML = alerts.length ? alerts.map((a) => `
    <div class="alert ${a.cls}">${icon("weather", 18)}<div><b>${esc(a.title)}</b><small>${esc(a.detail)}</small></div></div>`).join("")
    : `<div class="alert"><div><b>No major weather risk</b><small>Agent 5 found nothing that changes planned operations in the next five days.</small></div></div>`;
};

/* ---------------------------------------------------------------- pests */
function initPests() {
  // The crop list is filled from the model's own classes in RENDERERS.pests, so the
  // picker can never offer a crop the model was not trained on.
  $("#cropSelect").addEventListener("change", (e) => { state.crop = e.target.value || null; });

  const box = $("#uploadBox"), input = $("#photoInput");
  box.addEventListener("click", () => input.click());
  box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("drag"); });
  box.addEventListener("dragleave", () => box.classList.remove("drag"));
  box.addEventListener("drop", (e) => {
    e.preventDefault(); box.classList.remove("drag");
    if (e.dataTransfer.files[0]) showPhoto(e.dataTransfer.files[0]);
  });
  input.addEventListener("change", () => { if (input.files[0]) showPhoto(input.files[0]); });
  $("#analyzeBtn").addEventListener("click", runPestAnalysis);
}

function showPhoto(file) {
  if (!file.type.startsWith("image/")) { toast("That is not an image file"); return; }
  if (file.size > 8 * 1024 * 1024) { toast("Photo is larger than 8 MB"); return; }
  state.photo = file;
  const reader = new FileReader();
  reader.onload = () => {
    $("#photoPreview").innerHTML = `<img src="${reader.result}" alt="Crop photo preview">
      <p class="hint">${esc(file.name)} - ${(file.size / 1024).toFixed(0)} KB. Press Analyse
        to send it to the model.</p>`;
  };
  reader.readAsDataURL(file);
}

RENDERERS.pests = async function renderPests() {
  const status = await API.get("/api/vision/status");
  state.vision = status;
  const badge = $("#visionBadge") || {};
  if (!status.available) {
    badge.textContent = "Model not installed";
    badge.className = "head-note bad";
    $("#pestResult").className = "";
    $("#pestResult").innerHTML =
      `<div class="alert bad"><div><b>No trained model on this server</b>
        <small>${esc(status.reason || "")}</small></div></div>`;
    $("#analyzeHint").textContent = "Image screening is unavailable until the model is deployed.";
    return;
  }
  badge.textContent = `${status.classes} crops & conditions - ${(status.test_accuracy * 100).toFixed(1)}% accurate`;
  badge.className = "head-note";
  $("#screeningMeta").textContent = "What the trained model found";
  // What the chip in the card head used to say, in the sentence that was
  // already there. The chip is gone; the model's reach and its measured
  // accuracy are not the sort of thing to drop quietly.
  $("#analyzeHint").textContent =
    `${status.classes} crops and conditions, ${(status.test_accuracy * 100).toFixed(1)}% accurate on ` +
    `held-out images. Pick the crop first. Below ${(status.confidence_threshold * 100).toFixed(0)}% ` +
    `confidence the model says it is undecided rather than guessing a disease.`;

  const crops = status.crops || [];
  const sel = $("#cropSelect");
  sel.innerHTML = `<option value="">Choose the crop...</option>` + crops.map((c) =>
    `<option value="${esc(c)}">${esc(c.replace(/_/g, " "))}</option>`).join("");
  if (state.crop) sel.value = state.crop;
};

async function runPestAnalysis() {
  if (!state.photo) { toast("Choose a leaf photo first"); return; }
  const crop = $("#cropSelect").value;
  if (!crop) { toast("Choose which crop is in the photo"); return; }
  state.crop = crop;

  const btn = $("#analyzeBtn");
  btn.disabled = true; btn.textContent = "Checking...";
  $("#pestGraphic").hidden = true;
  $("#pestResult").className = "";
  $("#pestResult").innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;

  const form = new FormData();
  form.append("image", state.photo);
  form.append("crop", crop);
  form.append("language", state.language);
  if (state.sessionId) form.append("session_id", state.sessionId);

  try {
    const r = await fetch("/api/vision/diagnose", { method: "POST", body: form });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "analysis failed");
    if (data.session_id) { state.sessionId = data.session_id; localStorage.setItem("saarthi.session", data.session_id); }
    renderPestOutcome(data.screening, data.advisory);
  } catch (e) {
    $("#pestGraphic").hidden = true;
    $("#pestResult").innerHTML = `<div class="alert bad"><div><b>Could not check the photo</b><small>${esc(e.message)}</small></div></div>`;
  }
  btn.disabled = false; btn.textContent = "Check this leaf";
}

/* One result, mostly a picture: a confidence dial, the runners-up as bars, and a
   couple of lines of plain text. The model's training metrics and class list used
   to sit on this page too; they belong to the model, not to this leaf. */
function renderPestOutcome(s, advisory) {
  const graphic = $("#pestGraphic"), text = $("#pestResult");
  text.className = "";
  if (!s || !s.available) {
    graphic.hidden = true;
    text.innerHTML = `<div class="alert bad"><div><b>Model unavailable</b><small>${esc((s && s.reason) || "")}</small></div></div>`;
    return;
  }

  const check = s.crop_check || {};
  const chosen = (check.selected_crop || state.crop || "").replace(/_/g, " ");
  const bars = (items) => hBars((items || []).slice(0, 4).map((p) => ({
    label: p.display, value: Math.round(p.probability * 100),
    display: `${(p.probability * 100).toFixed(1)}%`,
  })), { max: 100 });

  // Wrong leaf for the crop that was chosen: show the mismatch, name no disease.
  if (s.verdict === "crop_mismatch" || s.verdict === "unknown_crop") {
    const pct = Math.round((check.crop_probability || 0) * 100);
    graphic.hidden = false;
    graphic.innerHTML = `
      <div class="verdict-row bad">
        ${donut(pct, "match", `with ${esc(chosen)}`)}
        <div class="verdict-text">
          <span class="verdict-tag bad">Wrong leaf for this crop</span>
          <b>This photo does not look like ${esc(chosen)}</b>
          <small>No disease is named, because the model would be naming it on the
            wrong plant.</small>
        </div>
      </div>
      <h3 class="mini-title">What the photo looks like instead</h3>
      ${bars(s.alternatives)}`;
    text.innerHTML = `<p class="answer-text">${esc(s.reason || "")}</p>
      <p class="hint">Checked in ${esc(s.inference_ms)} ms. Take the photo again in
        daylight, filling the frame with one leaf.</p>`;
    return;
  }

  // Not confident enough to name anything.
  if (!s.confident) {
    graphic.hidden = false;
    const top = s.alternatives && s.alternatives[0];
    graphic.innerHTML = `
      <div class="verdict-row warn">
        ${donut(Math.round(((top && top.probability) || 0) * 100), "best guess", "not enough")}
        <div class="verdict-text">
          <span class="verdict-tag warn">Undecided</span>
          <b>Not sure enough to name a disease</b>
          <small>The model answers only above its confidence threshold.</small>
        </div>
      </div>
      <h3 class="mini-title">What it was weighing</h3>
      ${bars(s.alternatives)}`;
    text.innerHTML = `<p class="answer-text">${esc(s.reason || "")}</p>
      <p class="hint">A clearer photo of a single affected leaf usually settles it.</p>`;
    return;
  }

  const top = s.prediction;
  const healthy = top.condition === "healthy";
  const pct = Math.round(top.probability * 100);
  graphic.hidden = false;
  graphic.innerHTML = `
    <div class="verdict-row ${healthy ? "good" : "flagged"}">
      ${donut(pct, "confidence", healthy ? "looks healthy" : "likely")}
      <div class="verdict-text">
        <span class="verdict-tag ${healthy ? "good" : "flagged"}">${healthy ? "Healthy" : "Needs attention"}</span>
        <b>${esc(top.display)}</b>
        <small>${esc(chosen)} - checked in ${esc(s.inference_ms)} ms</small>
      </div>
    </div>
    <h3 class="mini-title">Other candidates</h3>
    ${bars(s.alternatives)}`;

  // The short text half: what to do, and the safety line if there is one.
  const a8 = ((advisory || {}).agent_results || []).find((r) => r.agent_id === 8);
  const a11 = ((advisory || {}).agent_results || []).find((r) => r.agent_id === 11);
  const actions = (a8 && a8.actions ? a8.actions : []).slice(0, 3);
  text.innerHTML = `
    <p class="answer-text">${esc((advisory && advisory.answer) || top.note || "")}</p>
    ${actions.length ? `<h3 class="mini-title">What to do</h3>
      <ul class="check-list">${actions.map((x) =>
        `<li><span class="tick">&#10003;</span>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${a11 ? `<div class="alert ${a11.severity === "high" ? "bad" : "warn"}">
      <div><b>Before you spray</b><small>${esc((a11.warnings || [])[0] || a11.headline)}</small></div></div>` : ""}
    <p class="hint">A screening aid, not a diagnosis. Confirm anything serious with
      your agriculture officer.</p>`;
}

/* --------------------------------------------------------------- market */
RENDERERS.market = async function renderMarket() {
  const cropSel = $("#marketCrop");
  if (!cropSel.innerHTML) {
    cropSel.innerHTML = ["wheat", "rice", "mustard", "chickpea", "maize", "potato", "onion", "tomato", "cotton", "soybean"]
      .map((c) => `<option value="${c}">${c[0].toUpperCase() + c.slice(1)}</option>`).join("");
    $("#marketState").innerHTML = ["Uttar Pradesh", "Punjab", "Haryana", "Rajasthan", "Madhya Pradesh", "Maharashtra", "Bihar", "Gujarat", "Karnataka", "All India"]
      .map((s) => `<option value="${s === "All India" ? "" : s}">${s}</option>`).join("");
  }
  const crop = cropSel.value, stateName = $("#marketState").value;
  const districtPick = ($("#marketDistrict") || {}).value || "";
  const data = await API.get(`/api/market?commodity=${encodeURIComponent(crop)}`
    + `&state=${encodeURIComponent(stateName)}`
    + `&district=${encodeURIComponent(districtPick)}`
    + `&parcel_id=${encodeURIComponent(activeParcel())}`);

  /* The districts actually present in today's records, so the list cannot offer one
     that would come back empty. */
  const dsel = $("#marketDistrict");
  if (dsel) {
    const asked = (data.asked_for || {}).district || "";
    const options = (data.districts || []).map((d) =>
      `<option value="${esc(d.district)}">${esc(d.district)} (${d.markets})</option>`).join("");
    dsel.innerHTML = '<option value="">All districts</option>' + options;
    if (districtPick) dsel.value = districtPick;
    else if (asked && (data.districts || []).some((d) => d.district === asked)) {
      dsel.value = asked;
    }
  }

  /* Where these prices are actually FROM.
     The selector said Punjab and the district list underneath it was full of
     Madhya Pradesh and West Bengal, because the search found nothing in Punjab
     and widened to the whole country. The numbers were real; the heading over
     them was not, and a farmer deciding where to sell was reading the heading. */
  const askedState = (data.asked_for || {}).state || "";
  const gotStates = data.states || [];
  const scopeNote = $("#marketScopeNote");
  if (scopeNote) {
    const widened = Boolean(askedState) && !(gotStates.length === 1
      && String(gotStates[0]).toLowerCase() === askedState.toLowerCase());
    const words = [];
    if (data.note) words.push(data.note);
    if (gotStates.length > 1) {
      words.push(`These records come from ${gotStates.length} states, not ${askedState} alone.`);
    } else if (gotStates.length === 1 && widened) {
      words.push(`These records are from ${gotStates[0]}.`);
    }
    scopeNote.textContent = words.join(" ");
    scopeNote.hidden = words.length === 0;
    scopeNote.classList.toggle("warn", widened);
  }

  const nearest = data.nearest || [];
  const askedDistrict = (data.asked_for || {}).district || "";
  $("#nearestNote").textContent = nearest.length
    ? (askedDistrict
        ? `Markets in ${askedDistrict} first, then the best price elsewhere.`
        : "Best prices in today's records.")
      + " Ordered by district and price - these records carry no coordinates, so this"
      + " is not a distance."
    : "No records to rank.";
  $("#nearestList").innerHTML = nearest.map((r) => `
    <div class="near ${esc(String(r.district || "").toLowerCase() === askedDistrict.toLowerCase() ? "own" : "")}">
      <div>
        <b>${esc(r.market)}</b>
        <small>${esc(r.district || "")}, ${esc(r.state || "")}${r.variety ? " - " + esc(r.variety) : ""}</small>
      </div>
      <span class="near-price">\u20b9${num(r.modal_price, 0)}</span>
    </div>`).join("");
  const recs = data.records || [];
  const dates = [...new Set(recs.map((r) => r.arrival_date).filter(Boolean))];
  if (!data.available) {
    $("#priceCard").innerHTML = `<div class="empty-state"><b>Live prices unavailable</b><small>${esc(data.reason || "")}</small></div>`;
    $("#marketChart").innerHTML = `<div class="empty-chart">No price records</div>`;
    $("#marketTable").innerHTML = "";
    return;
  }
  const high = data.highest || {}, low = data.lowest || {};
  $("#priceCard").innerHTML = `
    <div class="card-head"><div><h2>${esc(data.commodity)}</h2><p>Median modal price in the latest records</p></div>
      <span class="head-note">${esc(data.record_count)} records</span></div>
    <div class="price-big">₹${esc(data.median_modal)}</div>
    <div class="price-sub">per quintal - ${esc(data.source)}</div>
    <div class="wn-stats" style="margin-top:14px">
      <div class="wn-stat"><small>Highest</small><b>₹${esc(high.modal_price)}</b><small>${esc(high.market)}</small></div>
      <div class="wn-stat"><small>Lowest</small><b>₹${esc(low.modal_price)}</b><small>${esc(low.market)}</small></div>
      <div class="wn-stat"><small>Retrieved</small><b>${esc(String(data.retrieved_at || "").slice(0, 10))}</b><small>${esc(high.arrival_date || "")}</small></div>
    </div>
    <p class="hint">${esc(data.caveat || "")}</p>`;

  const records = (data.records || []).slice(0, 8);
  $("#marketChart").innerHTML = barChart(records.map((r) => ({
    label: (r.market || "").slice(0, 8), value: r.modal_price, display: "₹" + r.modal_price,
  })));
  $("#marketTable").innerHTML = `<table><thead><tr><th>Market</th><th>State</th><th>Variety</th><th>Date</th>
      <th class="num">Min</th><th class="num">Modal</th><th class="num">Max</th></tr></thead><tbody>
    ${(data.records || []).slice(0, 12).map((r) => `<tr><td>${esc(r.market)}</td><td>${esc(r.state)}</td>
      <td>${esc(r.variety || "-")}</td><td>${esc(r.arrival_date)}</td>
      <td class="num">${esc(r.min_price ?? "-")}</td><td class="num up">${esc(r.modal_price)}</td>
      <td class="num">${esc(r.max_price ?? "-")}</td></tr>`).join("")}</tbody></table>`;
};

/* ------------------------------------------------------------ assistant */
function bubble(container, role, text, meta) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.innerHTML = esc(text).replace(/\n/g, "<br>");
  if (meta) div.insertAdjacentHTML("beforeend", meta);
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  return div;
}

/* What each stage of the chain is doing, in words a farmer can read while they
   wait. Shown in place of the three dots, because "Checking the weather" is a far
   better answer to "is this thing working" than an animation is. */
const STAGE_WORDS = {
  routing: "Reading your question",
  gathering: "Checking your soil, weather and prices",
  agents: "The agents are working",
  answering: "Writing the answer",
};

/* The streaming path. Same answer as the plain endpoint, shown as it is written:
   measured on this deployment, the first words land at 440 ms instead of a blank
   bubble for 2.1 s. Used whenever we are not also synthesising speech - a spoken
   reply needs the finished sentence before it can be read aloud. */
async function streamMessage(text, { container, ragEl, agentsEl, showTrace }, thinking) {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      message: text,
      session_id: state.sessionId,
      language: state.language,
      use_rag: ragEl ? ragEl.checked : true,
      run_agents: agentsEl ? agentsEl.checked : true,
      channel: "dashboard",
      phone: ownerPhone() || undefined,
    }),
  });
  if (!response.ok || !response.body) {
    throw new Error("The answer could not be started (HTTP " + response.status + ")");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  let advisory = null;

  const paint = () => {
    thinking.innerHTML = esc(answer).replace(/\n/g, "<br>");
    container.scrollTop = container.scrollHeight;
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop();
    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let event;
      try { event = JSON.parse(line.slice(5).trim()); } catch { continue; }

      if (event.stage && !answer) {
        const words = STAGE_WORDS[event.stage];
        if (words) {
          thinking.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`
            + `<div class="src">${esc(words)}...</div>`;
        }
      } else if (event.delta !== undefined) {
        answer += event.delta;
        paint();
      } else if (event.replace !== undefined) {
        // The cleaned text, replacing the raw pieces. Markdown the model slipped
        // in went out as deltas; this is the version without it.
        answer = event.replace;
        paint();
      } else if (event.ui) {
        // An instruction, not an answer. Carry it out straight away rather than
        // waiting for the poller to find it a second or two later.
        if (window.SaarthiWake) window.SaarthiWake.carryOut(event.ui);
      } else if (event.advisory) {
        advisory = event.advisory;
      } else if (event.error) {
        throw new Error(event.error);
      }
    }
  }
  if (advisory) {
    answer = advisory.answer || answer;
    state.sessionId = advisory.session_id || state.sessionId;
  }
  return { reply: answer, advisory, notices: (advisory && advisory.warnings) || [] };
}

async function sendMessage(text, { container, ragEl, agentsEl, speakEl, showTrace, convEl }) {
  if (!text.trim()) return;
  bubble(container, "user", text);
  const thinking = bubble(container, "bot", "");
  thinking.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;

  // Streaming is the default. The two exceptions both need the whole sentence
  // before anything can happen with it: a spoken reply has to be synthesised, and
  // conversation mode is the phone engine, which answers in about a second anyway.
  const speaking = speakEl ? speakEl.checked : false;
  const conversational = convEl ? convEl.checked : false;
  if (!speaking && !conversational) {
    try {
      const res = await streamMessage(text, { container, ragEl, agentsEl, showTrace },
                                      thinking);
      state.lastAdvisory = res.advisory;
      if (state.sessionId) localStorage.setItem("saarthi.session", state.sessionId);
      renderReplyMeta(thinking, res.reply, res.advisory, false);
      (res.notices || []).slice(0, 2).forEach((n) => bubble(container, "sys", n));
      if (showTrace) renderTrace(res.advisory || {});
      return;
    } catch (e) {
      // Fall through to the plain endpoint. A proxy that buffers the stream, or a
      // browser that cannot read one, should cost the animation and not the answer.
      thinking.innerHTML = `<div class="typing"><i></i><i></i><i></i></div>`;
    }
  }

  try {
    const res = await API.post("/api/chat", {
      message: text,
      session_id: state.sessionId,
      language: state.language,
      use_rag: ragEl ? ragEl.checked : true,
      run_agents: agentsEl ? agentsEl.checked : true,
      speak: speakEl ? speakEl.checked : false,
      // True only for a question that arrived as speech and was identified by
      // Azure. Cleared below, so a typed follow-up is judged on its own words.
      language_known: Boolean(state.languageKnown),
      // Conversation mode is the same engine the phone line uses: per-farmer memory,
      // a reply in about a second, and the 15 agents running behind it for next time.
      conversational: convEl ? convEl.checked : false,
      channel: "dashboard",
      // Who is asking. The assistant answers as this farmer - their fields, their
      // memory - and anything it learns here is the same memory the phone line reads.
      phone: ownerPhone() || undefined,
    });
    state.sessionId = res.session_id;
    localStorage.setItem("saarthi.session", res.session_id);
    state.lastAdvisory = res.advisory;
    state.languageKnown = false;
    followReplyLanguage(res.language);

    const adv = res.advisory || {};
    const tags = [];
    if (!res.advisory) {
      tags.push("conversation");
      (res.notices || []).forEach(() => {});
    }
    if (adv.intent) tags.push(adv.intent.replace(/_/g, " "));
    if ((adv.agents_invoked || []).length) tags.push(`${adv.agents_invoked.length} agents`);
    if ((adv.retrieved || []).length) tags.push(`${adv.retrieved.length} sources`);
    tags.push(adv.answer_source === "llm" ? (adv.answer_model || "Mistral") : "rule engine");
    // several chunks often come from one document; show each document once
    const sources = [...new Set((adv.retrieved || []).map((r) => r.title))].slice(0, 3);
    const meta = `<div class="meta">${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>`
      + (sources.length ? `<div class="src">Sources: ${esc(sources.join(" | "))}</div>` : "");
    thinking.innerHTML = esc(res.reply).replace(/\n/g, "<br>") + meta;

    (res.notices || []).slice(0, 2).forEach((n) => bubble(container, "sys", n));
    if (res.audio_url) playAudio(res.audio_url);
    if (showTrace) renderTrace(adv);
  } catch (e) {
    thinking.innerHTML = esc("Could not get an answer: " + e.message);
  }
}

/* The footer under every reply: what it was about, how many agents ran, which
   documents it drew on and who wrote the prose. Shared by the streaming and the
   plain path - a streamed answer with no provenance would be the one place in
   this project where a number arrives unlabelled. */
function renderReplyMeta(node, reply, advisory, conversation) {
  const adv = advisory || {};
  const tags = [];
  if (conversation) tags.push("conversation");
  if (adv.intent) tags.push(adv.intent.replace(/_/g, " "));
  if ((adv.agents_invoked || []).length) tags.push(`${adv.agents_invoked.length} agents`);
  if ((adv.retrieved || []).length) tags.push(`${adv.retrieved.length} sources`);
  if (adv.timings) tags.push(`${adv.duration_ms} ms`);
  if (adv.answer_source) {
    tags.push(adv.answer_source === "llm" ? (adv.answer_model || "Mistral")
              : adv.answer_source.replace(/_/g, " "));
  }
  const sources = [...new Set((adv.retrieved || []).map((r) => r.title))].slice(0, 3);
  node.innerHTML = esc(reply).replace(/\n/g, "<br>")
    + `<div class="meta">${tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>`
    + (sources.length ? `<div class="src">Sources: ${esc(sources.join(" | "))}</div>` : "");
}

function renderTrace(advisory) {
  const box = $("#traceBox");
  if (!advisory || !(advisory.agent_results || []).length) { box.innerHTML = ""; return; }
  box.className = "trace";
  box.innerHTML = `<h2 style="margin:18px 0 10px">Agent trace - ${esc(advisory.intent.replace(/_/g, " "))}
      <span class="head-note">${advisory.duration_ms} ms</span></h2>`
    + advisory.agent_results.map((r) => `
      <details>
        <summary><span class="num">${r.agent_id}</span>${esc(r.agent_name)} - ${esc(r.headline || r.status)}
          <span class="head-note ${r.status === "ok" ? "" : "warn"}" style="margin-left:auto">${esc(r.status)}</span></summary>
        <ul>${(r.findings || []).map((f) => `<li>${esc(f)}</li>`).join("")}
          ${(r.actions || []).map((a) => `<li><b>Action:</b> ${esc(a)}</li>`).join("")}
          ${(r.warnings || []).map((w) => `<li class="warn-li"><b>Warning:</b> ${esc(w)}</li>`).join("")}
          ${(r.assumptions || []).map((a) => `<li><i>Assumption:</i> ${esc(a)}</li>`).join("")}
          ${(r.evidence || []).map((e) => `<li><i>Evidence:</i> ${esc(e.source)}</li>`).join("")}
        </ul>
      </details>`).join("");
}

/* ---------------------------------------------------------------- voice */
let recorder = null, chunks = [];
async function toggleMic(button, inputEl) {
  if (!state.speech.configured) {
    toast("Azure Speech is not configured. Add AZURE_SPEECH_KEY in config/api_keys.env.");
    return;
  }
  if (recorder && recorder.state === "recording") { recorder.stop(); return; }
  if (!navigator.mediaDevices || !window.MediaRecorder) { toast("This browser cannot record audio"); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(stream);
    chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      button.classList.remove("recording");
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      if (blob.size < 1200) { toast("Nothing recorded"); return; }
      toast("Transcribing...");
      try {
        const form = new FormData();
        form.append("audio", blob, "clip.webm");
        form.append("language", state.language);
        const r = await fetch("/api/speech/stt", { method: "POST", body: form });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || "speech recognition failed");
        // Azure identified which of the thirteen that was, in the same call.
        // Switch before submitting, so the question goes out in the language it
        // was spoken in and the answer comes back in it.
        if (data.confident && data.language) {
          // Identified from the AUDIO. Said out loud to the server on the next
          // request, because otherwise the text heuristics re-read the script
          // of the transcript and can disagree with the microphone.
          state.languageKnown = true;
          await followReplyLanguage(data.language);
          const named = (state.languages || []).find((l) => l.code === data.language);
          if (named) toast("Heard " + named.english_name);
        }
        inputEl.value = data.text || "";
        if (data.text) inputEl.form.requestSubmit();
        else toast("Could not make out any speech");
      } catch (e) { toast(e.message); }
    };
    recorder.start();
    button.classList.add("recording");
    toast("Listening... press the mic again to stop");
  } catch { toast("Microphone permission denied"); }
}

function applySpeechAvailability() {
  // Azure Speech off: grey the voice controls out and say why, rather than
  // leaving buttons that fail when pressed.
  const on = !!state.speech.configured;
  const reason = state.speech.note || "Azure Speech is not configured.";
  [["#micBtn", "Speak your question"], ["#fabMic", "Speak"]].forEach(([sel, title]) => {
    const el = $(sel);
    if (!el) return;
    el.disabled = !on;
    el.title = on ? title : reason;
    el.style.opacity = on ? "" : ".45";
    el.style.cursor = on ? "" : "not-allowed";
  });
  [["#speakReply", "Speak reply"], ["#fabSpeak", "Voice"]].forEach(([sel, label]) => {
    const box = $(sel);
    if (!box) return;
    box.disabled = !on;
    box.checked = on && box.checked;
    const wrap = box.closest(".toggle");
    if (wrap) {
      wrap.title = on ? "" : reason;
      wrap.style.opacity = on ? "" : ".5";
      const text = wrap.querySelector("span");
      if (text) text.textContent = on ? label : label + " (off)";
    }
  });
}

function playAudio(url) {
  const player = $("#player");
  player.src = url;
  player.play().catch(() => toast("Tap once on the page to allow audio playback"));
}

/* Force-end whatever is in flight. Queued, ringing or connected - the farmer should
   never be stuck behind a call they can no longer use, least of all because of the
   one-at-a-time rule meant to protect them. */
async function cancelCall() {
  const btn = $("#callCancel");
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Stopping...";
  try {
    const res = await API.post("/api/calls/cancel", {
      phone: localStorage.getItem("saarthi.phone") || "",
    });
    toast(res.note);
    await RENDERERS.calls();
    /* After the re-render, not before: renderCalls resets this line, so setting it
       first meant the result of the press was wiped a moment later. */
    $("#callNote").textContent = res.note;
    /* A connected call ends on its next turn, which has not happened yet. Look
       again shortly so the farmer sees it actually finish rather than being left
       looking at "Ending" with no idea whether it worked. */
    if ((res.ending || []).length) {
      /* A connected call ends on its next turn, which has not happened yet.
         Look again shortly so the farmer sees it actually finish rather than
         being left looking at "Ending" with no idea whether it worked. */
      setTimeout(() => refreshPendingCall().catch(() => {}), 6000);
      setTimeout(() => refreshPendingCall().catch(() => {}), 15000);
    }
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

RENDERERS.calls = async function renderCalls() {
  const st = await API.get("/api/calls/status");
  state.telephony = st;
  $("#callNote").textContent = st.note
    || "One call-back at a time. The delay is set on your profile page.";

  $("#helplineBox").innerHTML = st.helpline_number
    ? `<a class="call-number" href="tel:${esc(st.helpline_number.replace(/[^0-9+]/g, ""))}">${esc(st.helpline_number)}</a>`
    : `<span class="call-number off">Not set</span>`;

  /* The languages as a sentence, not as a keypad. The call asks which one it
     should use; printing the thirteen digits on a screen was the IVR's own
     script shown to somebody who is not on the call. */
  const names = (st.languages || []).map((l) => l.native_name);
  $("#helplineNote").textContent = st.helpline_number
    ? (names.length
        ? `Answers in ${names.length} languages. It asks which one you want.`
        : "")
    : "Add SAARTHI_HELPLINE_NUMBER (your Twilio number) to config/api_keys.env.";

  if (!$("#callPhone").value) {
    const me = await API.get(asOwner("/api/profile")).catch(() => null);
    if (me && me.profile && me.profile.phone) $("#callPhone").value = me.profile.phone;
  }
  await refreshPendingCall();
  await refreshTasks();
};

/* Booked call-backs. The server does the parsing, so a phrase that works here works
   on the phone too - there is only one parser and one set of rules. */
const TASK_STATE = {
  pending: "waiting",
  done: "called",
  cancelled: "cancelled",
  expired: "stopped",
  failed: "failed",
};

/* How many booked calls to show before "See more". Three fills the card
   without pushing everything below it off the screen. */
const TASKS_SHOWN = 3;
let tasksExpanded = false;

async function refreshTasks() {
  const data = await API.get("/api/tasks").catch(() => null);
  if (!data) return;
  const rows = data.tasks || [];
  $("#taskCount").textContent = data.pending
    ? `${data.pending} waiting` : "none waiting";

  /* A few at a time. The booked list only grows - every call ever asked for
     stays on it - and a farmer looking for the one they booked a minute ago
     had to read past a month of them. */
  const shown = tasksExpanded ? rows : rows.slice(0, TASKS_SHOWN);
  $("#taskTable").innerHTML = rows.length ? shown.map((t) => `
    <div class="task-row">
      <div>
        <div>${esc(t.summary || t.said)}</div>
        <div class="when">${esc(t.said)}</div>
      </div>
      <div class="btn-row tight">
        <span class="task-state ${esc(t.status)}">${esc(TASK_STATE[t.status] || t.status)}</span>
        ${t.status === "pending"
          ? `<button class="btn ghost sm" data-cancel-task="${esc(t.task_id)}">Cancel</button>`
          : ""}
      </div>
    </div>`).join("")
    + (rows.length > TASKS_SHOWN ? `<div class="more-row">
        <button class="btn ghost sm" id="taskMore" type="button">${
          tasksExpanded ? "See less" : `See more (${rows.length - TASKS_SHOWN})`
        }</button></div>` : "")
    : `<p class="hint">Nothing booked. Ask for a call above, or say it to Saarthi on the phone.</p>`;

  const more = $("#taskMore");
  if (more) {
    more.addEventListener("click", () => {
      tasksExpanded = !tasksExpanded;
      refreshTasks();
    });
  }

  $$("#taskTable [data-cancel-task]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      await API.del("/api/tasks/" + btn.dataset.cancelTask).catch(() => null);
      toast("Call-back cancelled.");
      refreshTasks();
    }));
}

async function bookTask() {
  const said = $("#taskSaid").value.trim();
  if (!said) { toast("Say what the call should be about."); return; }
  try {
    const out = await API.post("/api/tasks", { said, language: state.language });
    $("#taskSaid").value = "";
    // The server's own words, which name the time back - so a misread number is
    // visible here rather than at the moment the phone does not ring.
    $("#taskNote").textContent = out.reply;
    toast(out.reply);
    refreshTasks();
  } catch (e) {
    $("#taskNote").textContent = e.message || "Could not book that call.";
  }
}

/* The log is the record of what actually happened, so each status is spelled out
   rather than shown as a bare word. "requested" used to appear whether or not
   anything had been dialled, which is the one thing it must never do. */
const CALL_STATUS = {
  scheduled: ["warn", "Booked - Saarthi will dial at the time you chose"],
  requested: ["warn", "Sent to telephony, waiting for Twilio"],
  queued: ["warn", "Twilio accepted it and is dialling"],
  ringing: ["warn", "Ringing now"],
  initiated: ["warn", "Dialling"],
  in_progress: ["", "On the call"],
  completed: ["", "Finished"],
  failed: ["bad", "Not dialled"],
  busy: ["bad", "Line was busy"],
  "no-answer": ["bad", "Nobody answered"],
  canceled: ["bad", "Cancelled"],
  cancelled: ["bad", "Cancelled"],
  /* Asked to stop, but Twilio still has the line. It ends on the next turn of the
     call - a few seconds - and this is shown rather than "Cancelled" so the status
     does not claim the call is over while the farmer can still hear it. */
  cancel_requested: ["warn", "Ending - hangs up at the next turn"],
  simulated: ["warn", "Simulated - nothing was dialled"],
};

let adminCallFilter = "";

/* The call log, which is an ADMINISTRATOR's view.
   -------------------------------------------------------------------------
   It used to sit on the farmer's own Voice Calls page, where it listed one
   farmer's calls back to them - a record they were present for. The person who
   needs to look at calls is the one running the server, across every account,
   which is what /api/calls already returns for an administrator. */
async function renderAdminCalls() {
  const box = $("#adminCallTable");
  if (!box) return;
  const data = await API.get("/api/calls?limit=60");
  const rows = (data.calls || []).filter((c) => {
    const want = (adminCallFilter || "").trim().toLowerCase();
    if (!want) return true;
    return [c.phone, c.direction, c.status, c.last_question]
      .join(" ").toLowerCase().includes(want);
  });
  box.innerHTML = rows.length ? `<table><thead><tr><th>When</th><th>Phone</th>
      <th>Direction</th><th>What happened</th><th>Turns</th><th>Last thing said</th></tr></thead><tbody>
    ${rows.map((c) => {
      const [cls, label] = CALL_STATUS[c.status] || ["", c.status];
      const detail = c.detail || {};
      const why = detail.error ? ` - ${detail.error}` : "";
      const when = detail.dial_at && c.status === "scheduled"
        ? `due ${String(detail.dial_at).replace("T", " ").slice(11, 16)}` : "";
      return `<tr>
      <td>${esc(String(c.created_at || "").replace("T", " ").slice(0, 16))}</td>
      <td>${esc(c.phone)}</td><td>${esc(c.direction)}</td>
      <td><span class="badge ${cls}">${esc(label)}</span>
          ${when ? `<small class="cell-note">${esc(when)}</small>` : ""}
          ${why ? `<small class="cell-note bad">${esc(why.slice(0, 120))}</small>` : ""}</td>
      <td class="num">${esc(c.turn_count || 0)}</td>
      <td>${esc((c.last_question || "").slice(0, 60))}</td></tr>`;
    }).join("")}</tbody></table>`
    : `<p class="hint">No calls match.</p>`;
  const count = $("#adminCallCount");
  if (count) count.textContent = `${rows.length} of ${(data.calls || []).length} shown`;
}

/* The one part of the old call log the FARMER still needs: a call already on
   its way, because asking for a second one is refused and saying so before the
   press is better than after it. */
async function refreshPendingCall() {
  const panel = $("#pendingCall");
  if (!panel) return;
  const data = await API.get("/api/calls?limit=10").catch(() => ({ calls: [] }));
  const pending = (data.calls || []).find((c) => ["scheduled", "requested", "queued",
    "ringing", "initiated", "in_progress"].includes(c.status));
  panel.hidden = !pending;
  if (pending) {
    const [, label] = CALL_STATUS[pending.status] || ["", pending.status];
    panel.innerHTML = `<b>A call is already on its way</b>
      <small>${esc(pending.phone)} - ${esc(label)}. You cannot ask for another until
      this one finishes.</small>`;
  }
  const btn = $("#callBtn");
  if (btn) { btn.disabled = Boolean(pending); btn.textContent = pending ? "Call already requested" : "Request call"; }
}

async function requestCall() {
  const phone = $("#callPhone").value.trim();
  if (!phone) { toast("Enter a phone number"); return; }
  const btn = $("#callBtn"); btn.disabled = true; btn.textContent = "Requesting...";
  try {
    const res = await API.post("/api/calls/outbound", {
      phone, language: $("#callLang").value, topic: $("#callTopic").value,
      farmer_name: state.profileName || "Farmer",
    });
    toast(res.message);
  } catch (e) {
    // The server distinguishes a mistyped number from a call already on its way
    // from telephony refusing it; all three used to look like success.
    toast(e.message);
  }
  btn.disabled = false; btn.textContent = "Request call";
  await refreshPendingCall();
}

/* -------------------------------------------------------------- devices */

RENDERERS.devices = async function renderDevices() {
  const [map, latest, fields] = await Promise.all([
    API.get("/api/sensors/register-map"), API.get("/api/sensors/latest"),
    /* For the sensor-location select. A farmer who has not mapped a plot yet still
       needs the BLE card, so this one is allowed to fail quietly. */
    API.get(asOwner("/api/profile/fields")).catch(() => ({ fields: [] })),
  ]);
  if (fields.fields) state.fields = fields.fields;


  /* The path a farmer cares about ends at this page. The endpoint it posts to is
     real and still in the API response, but it belongs in the docs, not on a strip
     describing where the reading comes from. */
  const path = (map.transport || []).filter((t) => !/^POST /.test(t));
  $("#deviceFlow").innerHTML = path.map((t, i, arr) =>
    `<div class="node">${icon(["soil", "chip", "network", "grid"][i] || "chip", 18)}<b>${esc(t)}</b></div>` +
    (i < arr.length - 1 ? `<div class="arrow">&rarr;</div>` : "")).join("");

  // the parameters the sensor and the validator actually know about.
  state.sensorParams = map.parameters || [];
  const current = (latest.available && latest.reading) || {};

  if (latest.available) {
    const v = latest.validation || {};
    $("#deviceStatus").innerHTML = [
      ["Device", latest.reading.device_id], ["Parcel", latest.reading.parcel_id],
      ["Mode", latest.reading.mode], ["Provenance", latest.reading.provenance],
      ["Recorded", String(latest.reading.recorded_at).replace("T", " ").slice(0, 19)],
      ["Validation", `${v.status} (${(v.completeness * 100).toFixed(0)}% usable)`],
      ["Flags", (v.flags || []).join(", ") || "none"],
      ["Scenario", latest.reading.scenario || "-"], ["Seed", latest.reading.seed ?? "-"],
    ].map(([k, val]) => `<div class="status-row"><span>${esc(k)}</span><b>${esc(val)}</b></div>`).join("");
  } else {
    $("#deviceStatus").innerHTML = `<p class="hint">No packet received yet. Connect the
      HM-10 and press Fetch values, or generate a simulated reading.</p>`;
  }

  renderSensorFields();
  renderBleSupport();
  renderBleBadge();
  renderProvenanceNote();
  /* Seed the location from the stored reading, so re-opening the page shows where the
     last reading was taken rather than an empty form. */
  if (latest.available && latest.reading.latitude !== null
      && latest.reading.latitude !== undefined && !$("#sensorLat").value) {
    $("#sensorLat").value = Number(latest.reading.latitude).toFixed(6);
    $("#sensorLon").value = Number(latest.reading.longitude).toFixed(6);
    if (latest.reading.location_source) {
      $("#sensorLocSource").value = latest.reading.location_source;
    }
  }
  ensureMap("sensor");
  renderGpsStatus();
  appendBleStream();
};


/* ------------------------------------------------------------------ ble link */
/* The HM-10 over Web Bluetooth. The radio belongs to the browser, so none of this
   touches the server until a packet has been fetched and parsed.

   The rule the whole section is built around: the numbers on screen change when the
   farmer presses Fetch, and at no other time. The stream keeps running underneath -
   it has to, because the sketch only speaks every 20 seconds, and a fetch that opened
   its own listener would wait a full cycle every time - but a frame arriving in the
   background updates the stream panel alone. The value table, the mapping table and
   the stored reading all come from one captured frame and then sit still. */
const ble = { fetched: null, lastGps: null, sensorMarker: null, markerAt: "" };

function bleFieldLabel(field) {
  const spec = (state.sensorParams || []).find((p) => p.field === field);
  return spec ? `${spec.label} (${spec.unit})` : field;
}

function renderBleSupport() {
  const note = $("#bleSupport");
  if (!note) return;
  const ok = window.SaarthiBLE && window.SaarthiBLE.supported();
  if (ok) {
    note.textContent = "This browser can talk to the HM-10 directly. Press Connect "
      + "device and pick it from the list - the same module the Serial Bluetooth "
      + "Terminal app pairs with. Close that app first: only one device can hold "
      + "the link at a time.";
    note.className = "hint";
  } else {
    note.textContent = window.SaarthiBLE ? window.SaarthiBLE.unsupportedReason()
      : "The Bluetooth helper did not load.";
    note.className = "hint warn-text";
  }
}

/* What the three buttons may do. The connection state used to be a chip in
   the card head as well; the chip is gone and #bleStatus says it in a
   sentence, which is where a farmer was reading it anyway. */
function renderBleBadge() {
  if (!window.SaarthiBLE || !$("#bleDisconnect")) return;
  const info = window.SaarthiBLE.info();
  $("#bleDisconnect").disabled = !info.connected;
  $("#bleFetch").disabled = !info.connected;
  $("#bleConnect").disabled = info.connected || !info.supported;
}

function appendBleStream() {
  const pre = $("#bleStream");
  if (!pre) return;
  const lines = window.SaarthiBLE.info().rawLines;
  pre.textContent = lines.length ? lines.join("\n") : "waiting for the device...";
  pre.scrollTop = pre.scrollHeight;
}

/* The provenance label is decided here and shown BEFORE anything is stored, because
   at this stage of the hardware the Arduino generates the seven values itself. A
   number that was never measured must not be presented as a measurement. */
function renderProvenanceNote() {
  if (!$("#bleProvenanceNote")) return;
  $("#bleProvenanceNote").textContent = $("#bleProbeAttached").checked
    ? "Readings will be stored as REAL_SENSOR: measured by the probe in the ground."
    : "Readings will be stored as DEVICE_SIMULATED: the packet really does arrive from "
      + "the HM-10, but the Arduino is generating the seven values, so they are not "
      + "measurements. Tick the box above once a soil probe is wired in.";
}

function bleOrder() {
  if ($("#bleFormat").value !== "ordered") return null;
  /* The order is typed in plain words and mapped through the same alias table the
     parser uses, so temp and Temperature both work and an unknown word is dropped
     rather than guessed at. */
  return String($("#bleOrder").value || "").split(/[,\s]+/).filter(Boolean)
    .map((word) => window.BleParse.FIELD_ALIASES[window.BleParse.normaliseKey(word)] || "");
}

function renderBleValues(frame, stored) {
  const params = state.sensorParams || [];
  const received = frame.values || {};
  const rows = params.map((p) => {
    const value = received[p.field];
    const has = value !== undefined && value !== null;
    const band = stored ? (stored.parameters || []).find((x) => x.field === p.field) : null;
    return `<tr class="${has ? "" : "muted-row"}">
      <td>${esc(p.label)}</td>
      <td class="num">${has ? esc(value) : "no value"}</td>
      <td>${has ? esc(p.unit) : ""}</td>
      <td>${has && band ? `<span class="p-band ${esc(band.band)}">${esc(band.band)}</span>` : ""}</td>
      <td>${has ? "" : "<i>not in this packet</i>"}</td></tr>`;
  }).join("");

  const count = Object.keys(received).length;
  $("#bleValues").innerHTML = `<table><thead><tr><th>Parameter</th><th>Value</th>
      <th>Unit</th><th>Band</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="hint">${count} of ${params.length} parameters arrived in this packet.
      The rest read as no value - nothing has been filled in for them.</p>`;

  const mapping = (frame.mapping || []).map((m) => `<tr>
      <td><code>${esc(m.key)}</code></td><td>${esc(bleFieldLabel(m.field))}</td>
      <td class="num">${esc(m.value)}</td><td>${esc(m.note || "")}</td></tr>`).join("");
  const unknown = (frame.unknown || []).map((u) => `<tr class="muted-row">
      <td><code>${esc(u.key)}</code></td><td><i>not recognised, dropped</i></td>
      <td class="num">${esc(u.value)}</td><td>${esc(u.note || "")}</td></tr>`).join("");
  $("#bleMapping").innerHTML = (mapping || unknown)
    ? `<table><thead><tr><th>Key from the device</th><th>Stored as</th><th>Value</th>
        <th>Note</th></tr></thead><tbody>${mapping}${unknown}</tbody></table>`
      + ((frame.unknown || []).length
         ? `<p class="hint warn-text">${frame.unknown.length} key(s) were not recognised,
            so they were dropped rather than guessed at. Send me the exact label the
            sketch prints and it can be added.</p>` : "")
    : `<p class="hint">Nothing in that packet carried a label this page recognises.</p>`;
}

/* One fetch: take one frame, attach the location, store it, show it, stop. */
async function bleFetchOnce() {
  const btn = $("#bleFetch");
  const note = $("#bleFetchNote");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Waiting for a packet...";
  note.textContent = "Listening for the next burst. The sketch sends every 20 seconds, "
    + "so this can take that long.";
  try {
    window.SaarthiBLE.setOrder(bleOrder());
    const frame = await window.SaarthiBLE.capture();
    ble.fetched = frame;
    if (Object.keys(frame.gps || {}).length) ble.lastGps = frame.gps;

    const payload = {
      values: frame.values,
      raw_packet: frame.raw,
      device_id: window.SaarthiBLE.info().device || "HM-10",
      parcel_id: activeParcel() || "field-1",
      probe_attached: $("#bleProbeAttached").checked,
      phone: localStorage.getItem("saarthi.phone") || "",
    };
    Object.assign(payload, sensorLocationPayload());

    const stored = await API.post("/api/sensors/ble", payload);
    renderBleValues(frame, stored);
    renderGpsStatus();
    note.textContent = stored.note
      + " These values stay exactly as they are until you press Fetch again.";
    toast(`Fetched ${stored.received.length} of 7 values (${stored.validation.status})`);
    /* Everything downstream - the soil page, the trend, the agents, the voice agent -
       reads the stored reading, so the parts of the page showing it are refreshed
       once, here, on the fetch that was asked for. Nothing polls. */
    await renderDeviceStatusOnly();
  } catch (error) {
    note.textContent = error.message;
    toast(error.message);
  } finally {
    btn.textContent = original;
    btn.disabled = !window.SaarthiBLE.info().connected;
  }
}

async function renderDeviceStatusOnly() {
  const latest = await API.get("/api/sensors/latest").catch(() => null);
  if (!latest || !latest.available || !$("#deviceStatus")) return;
  const v = latest.validation || {};
  const r = latest.reading;
  const place = (r.latitude === null || r.latitude === undefined)
    ? "not recorded"
    : `${Number(r.latitude).toFixed(5)}, ${Number(r.longitude).toFixed(5)}`
      + ` (${r.location_source || "source unknown"})`;
  $("#deviceStatus").innerHTML = [
    ["Device", r.device_id], ["Parcel", r.parcel_id], ["Mode", r.mode],
    ["Provenance", r.provenance], ["Link", r.link || "-"], ["Location", place],
    ["Recorded", String(r.recorded_at).replace("T", " ").slice(0, 19)],
    ["Validation", `${v.status} (${(v.completeness * 100).toFixed(0)}% usable)`],
    ["Flags", (v.flags || []).join(", ") || "none"],
  ].map(([k, val]) => `<div class="status-row"><span>${esc(k)}</span><b>${esc(val)}</b></div>`).join("");
}

/* ------------------------------------------------------- sensor location */
function sensorLocationPayload() {
  const lat = parseFloat($("#sensorLat").value);
  const lon = parseFloat($("#sensorLon").value);
  if (!isFinite(lat) || !isFinite(lon)) return {};
  const out = { latitude: lat, longitude: lon, location_source: $("#sensorLocSource").value };
  const gps = ble.lastGps || {};
  if (out.location_source === "gps") {
    if (gps.gps_satellites !== undefined) out.gps_satellites = gps.gps_satellites;
    if (gps.gps_fix !== undefined) out.gps_fix = gps.gps_fix;
  }
  return out;
}

function renderGpsStatus() {
  if (!$("#gpsStatus")) return;
  const gps = ble.lastGps;
  const rows = gps && Object.keys(gps).length ? [
    ["Latitude", gps.latitude === undefined ? "no fix yet" : gps.latitude],
    ["Longitude", gps.longitude === undefined ? "no fix yet" : gps.longitude],
    ["Satellites", gps.gps_satellites === undefined ? "-" : gps.gps_satellites],
    ["Fix", gps.gps_fix === undefined ? "not reported" : (gps.gps_fix ? "yes" : "no")],
    ["Speed", gps.gps_speed_kmph === undefined ? "-" : gps.gps_speed_kmph],
  ] : [["GPS", "nothing from the NEO-6M yet"]];
  $("#gpsStatus").innerHTML = rows.map(([k, v]) =>
    `<div class="status-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");

  const lat = parseFloat($("#sensorLat").value), lon = parseFloat($("#sensorLon").value);
  const badge = $("#sensorLocBadge") || {};
  if (isFinite(lat) && isFinite(lon)) {
    badge.textContent = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
    badge.className = "head-note";
  } else {
    badge.textContent = "Not set";
    badge.className = "head-note warn";
  }
  drawSensorMarker();
}

function drawSensorMarker() {
  const entry = maps.sensor;
  const lat = parseFloat($("#sensorLat").value), lon = parseFloat($("#sensorLon").value);
  if (!entry || !window.L || !isFinite(lat) || !isFinite(lon)) return;
  /* Redrawing on every GPS line would re-centre the map under the farmer while they
     are panning it, so nothing moves unless the point itself changed. */
  const at = lat.toFixed(6) + "," + lon.toFixed(6);
  if (ble.markerAt === at) return;
  ble.markerAt = at;
  if (ble.sensorMarker) entry.map.removeLayer(ble.sensorMarker);
  ble.sensorMarker = window.L.circleMarker([lat, lon], {
    radius: 8, color: "#15803d", weight: 2, fillColor: "#22c55e", fillOpacity: 0.85,
  }).addTo(entry.map).bindTooltip("Sensor");
  entry.map.setView([lat, lon], Math.max(entry.map.getZoom(), 15));
}

function setSensorLocation(lat, lon, source, message) {
  $("#sensorLat").value = Number(lat).toFixed(6);
  $("#sensorLon").value = Number(lon).toFixed(6);
  $("#sensorLocSource").value = source;
  $("#sensorLocNote").textContent = message;
  renderGpsStatus();
}

function useGpsFix() {
  const gps = ble.lastGps || {};
  if (gps.latitude === undefined || gps.longitude === undefined) {
    $("#sensorLocNote").textContent = "No coordinates have come from the NEO-6M yet. "
      + "The module needs a clear view of the sky and can take a few minutes for its "
      + "first fix; until then use your own location or type the coordinates in.";
    return;
  }
  if (gps.gps_fix === false) {
    /* Coordinates without a fix are the last known or default position, and plotting
       them as the location of the probe would put it in the wrong field. */
    $("#sensorLocNote").textContent = "The GPS reports no fix, so those coordinates "
      + "cannot be trusted yet. Waiting for a fix.";
    return;
  }
  const sats = gps.gps_satellites ? " on " + gps.gps_satellites + " satellites" : "";
  setSensorLocation(gps.latitude, gps.longitude, "gps",
    "Taken from the NEO-6M" + sats + ".");
}

function useBrowserLocation() {
  if (!navigator.geolocation) {
    $("#sensorLocNote").textContent = "This browser will not share a location.";
    return;
  }
  $("#sensorLocNote").textContent = "Asking the browser for your location...";
  navigator.geolocation.getCurrentPosition(
    (pos) => setSensorLocation(pos.coords.latitude, pos.coords.longitude, "browser",
      "From this device, accurate to about " + Math.round(pos.coords.accuracy) + " m."),
    (err) => { $("#sensorLocNote").textContent = "Could not get a location: " + err.message; },
    { enableHighAccuracy: true, timeout: 10000 });
}

function useFieldCentre() {
  const id = $("#sensorField").value;
  const field = (state.fields || []).find((f) => f.field_id === id);
  if (!field || !field.centre_lat) {
    $("#sensorLocNote").textContent = "That field has no centre saved. Place it on the "
      + "map on the My Fields page first.";
    return;
  }
  setSensorLocation(field.centre_lat, field.centre_lon, "field_centre",
    "The centre of " + field.name + ". That is the plot, not the exact spot of the probe.");
}

function renderSensorFields() {
  const select = $("#sensorField");
  if (!select) return;
  const fields = state.fields || [];
  const chosen = select.value;
  select.innerHTML = fields.map((f) => `<option value="${esc(f.field_id)}">`
    + esc(f.name) + (f.crop ? " - " + esc(f.crop) : "") + "</option>").join("")
    || "<option value=\"field-1\">field-1 (no fields registered yet)</option>";
  if (chosen && fields.some((f) => f.field_id === chosen)) select.value = chosen;
}

/* Wired once, at boot. The listeners live for the whole session because the BLE link
   does: switching pages must not drop the connection or lose the stream. */
function wireBle() {
  if (!$("#bleCard") || !window.SaarthiBLE) return;
  renderBleSupport();
  renderBleBadge();
  renderProvenanceNote();
  renderGpsStatus();

  window.SaarthiBLE.on("line", () => appendBleStream());
  /* The position panel follows the device as it gets a fix. The coordinates are not
     written into the location form by this: that stays a deliberate press of one of
     the three buttons, so a reading is never filed at a place nobody chose. */
  window.SaarthiBLE.on("gps", (gps) => { ble.lastGps = gps; renderGpsStatus(); });
  window.SaarthiBLE.on("status", (s) => {
    $("#bleStatus").textContent = s.text;
    $("#bleStatus").className = "hint" + (s.kind === "warn" ? " warn-text" : "");
    renderBleBadge();
  });
  /* A frame arriving in the background updates the GPS panel and the stream, and
     deliberately NOT the value table: that belongs to the last fetch. */
  window.SaarthiBLE.on("frame", (frame) => {
    if (Object.keys(frame.gps || {}).length) { ble.lastGps = frame.gps; renderGpsStatus(); }
    appendBleStream();
  });

  $("#bleConnect").addEventListener("click", async () => {
    try {
      await window.SaarthiBLE.connect({ anyDevice: $("#bleAnyDevice").checked });
    } catch (error) {
      /* The chooser being dismissed is not a fault worth a red message. */
      const cancelled = /cancel|NotFoundError|User cancelled/i.test(error.message || "");
      $("#bleStatus").textContent = cancelled
        ? "No device chosen. Press Connect device to try again. If the HM-10 is not in "
          + "the list, tick Show all nearby devices."
        : error.message;
      $("#bleStatus").className = "hint warn-text";
    }
    renderBleBadge();
  });
  $("#bleDisconnect").addEventListener("click", async () => {
    await window.SaarthiBLE.disconnect();
    renderBleBadge();
  });
  $("#bleFetch").addEventListener("click", bleFetchOnce);
  $("#bleClearStream").addEventListener("click", () => {
    window.SaarthiBLE.reset();
    appendBleStream();
  });
  $("#bleFormat").addEventListener("change", () => {
    $("#bleOrderWrap").hidden = $("#bleFormat").value !== "ordered";
  });
  $("#bleProbeAttached").addEventListener("change", renderProvenanceNote);
  $("#bleSampleFeed").addEventListener("click", () => {
    const text = $("#bleSampleText").value;
    if (!text.trim()) { toast("Paste a line or two first."); return; }
    window.SaarthiBLE.simulateConnected("sample text");
    renderBleBadge();
    /* Through the real path: buffered, split on newlines, parsed, framed. */
    window.SaarthiBLE.ingest(text.endsWith("\n") ? text : text + "\n");
    appendBleStream();
    toast("Fed into the parser. Press Fetch values to capture it.");
  });

  $("#sensorUseGps").addEventListener("click", useGpsFix);
  $("#sensorUseBrowser").addEventListener("click", useBrowserLocation);
  $("#sensorUseField").addEventListener("click", useFieldCentre);
  $("#sensorLat").addEventListener("change", () => {
    $("#sensorLocSource").value = "manual";
    renderGpsStatus();
  });
  $("#sensorLon").addEventListener("change", () => {
    $("#sensorLocSource").value = "manual";
    renderGpsStatus();
  });
  $("#sensorField").addEventListener("change", () => {
    $("#sensorLocNote").textContent = "Readings fetched from now on are filed against "
      + "this field.";
  });
}


/* ------------------------------------------------- schemes, finance, support */
/* Three things a farmer asks about that are not agronomy, so the fifteen agents do
   not cover them: which scheme pays for what, whether the season pays, and who to
   ring. Every scheme carries the official portal because the amounts do change, and
   a figure quoted here without a way to check it is worth very little. */

RENDERERS.schemes = async function renderSchemes() {
  const data = await API.get("/api/schemes" + (state.schemeFilter
    ? "?category=" + encodeURIComponent(state.schemeFilter) : ""));
  $("#schemeNote").textContent = data.note;

  const cats = ["All", ...(data.categories || [])];
  $("#schemeFilters").innerHTML = cats.map((c) => {
    const active = (c === "All" && !state.schemeFilter) || c === state.schemeFilter;
    return `<button class="chip ${active ? "on" : ""}" data-cat="${esc(c === "All" ? "" : c)}">${esc(c)}</button>`;
  }).join("");

  $("#schemeGrid").innerHTML = (data.schemes || []).map((sc) => `
    <article class="scheme">
      <div class="sc-top">
        <span class="sc-tag">${esc(sc.category_tag || sc.category || "")}</span>
        <span class="sc-benefit">${esc(sc.subsidy_percent || "")}</span>
      </div>
      <h3>${esc(sc.title)}</h3>
      <p class="sc-money">${esc(sc.financial_benefit || "")}</p>
      <p class="sc-about">${esc(sc.objective || "")}</p>
      <dl class="sc-facts">
        <dt>Who can apply</dt><dd>${esc(sc.eligibility || "-")}</dd>
        <dt>Papers needed</dt><dd>${esc(sc.documents || "-")}</dd>
      </dl>
      <a class="btn primary sm" href="${esc(sc.portal_url)}" target="_blank" rel="noopener">
        Apply on ${esc(sc.portal_name || "the official portal")}</a>
    </article>`).join("");
};


/* ----------------------------------------------------------- the ledger */
/* What the farm actually spent, as opposed to the break-even sum next to it. The
   two are deliberately separate: one is a record, the other is arithmetic over a
   figure typed once. */
async function renderLedger() {
  const params = new URLSearchParams();
  const field = ($("#ledgerField") || {}).value || "";
  const season = ($("#ledgerSeason") || {}).value || "";
  if (field) params.set("field_id", field);
  if (season) params.set("season", season);

  const data = await API.get("/api/ledger" + (params.toString() ? "?" + params : ""));
  const sum = data.summary || {};
  state.ledgerCategories = data.categories || [];

  $("#ledgerNote").textContent = sum.note || "";
  $("#ledgerTotals").innerHTML = [
    { label: "Spent", value: sum.spent_inr, cls: "out" },
    { label: "Earned", value: sum.earned_inr, cls: "in" },
    { label: sum.net_inr >= 0 ? "Ahead by" : "Behind by",
      value: Math.abs(sum.net_inr || 0), cls: sum.net_inr >= 0 ? "net-good" : "net-bad" },
  ].map((t) => `<div class="led-total ${t.cls}">
      <small>${esc(t.label)}</small>
      <b>\u20b9${num(t.value || 0, 0)}</b>
    </div>`).join("");

  const cats = Object.entries(sum.by_category || {}).sort((a, b) => b[1] - a[1]);
  const top = cats.length ? cats[0][1] : 0;
  $("#ledgerBars").innerHTML = cats.length
    ? cats.map(([name, value]) => `
        <div class="led-bar">
          <span class="lb-name">${esc(name)}</span>
          <span class="lb-track"><i style="width:${top ? (value / top * 100).toFixed(0) : 0}%"></i></span>
          <span class="lb-value">\u20b9${num(value, 0)}</span>
        </div>`).join("")
    : "";

  const cat = $("#ledCategory");
  if (cat && !cat.innerHTML) {
    cat.innerHTML = state.ledgerCategories.map((c) =>
      `<option value="${esc(c)}">${esc(c)}</option>`).join("");
  }

  const rows = (data.entries || []).map((e) => `<tr>
      <td>${esc(String(e.spent_on).slice(0, 10))}</td>
      <td>${esc(e.category)}</td>
      <td class="num ${e.kind === "income" ? "in" : "out"}">
        ${e.kind === "income" ? "+" : "-"}\u20b9${num(e.amount_inr, 0)}</td>
      <td>${esc(e.note || "")}</td>
      <td>${esc(e.source)}</td>
      <td><button class="link danger-link" data-del="${esc(e.entry_id)}">remove</button></td>
    </tr>`).join("");
  $("#ledgerTable").innerHTML = rows
    ? `<table><thead><tr><th>When</th><th>On what</th><th>Amount</th><th>Note</th>
        <th>Entered</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : `<p class="hint">Nothing recorded yet.</p>`;
}

async function addLedgerEntry() {
  const amount = parseFloat($("#ledAmount").value);
  if (!amount || amount <= 0) { toast("Enter an amount."); return; }
  const btn = $("#ledAdd");
  btn.disabled = true;
  try {
    await API.post("/api/ledger", {
      kind: $("#ledEntryKind").value,
      category: $("#ledCategory").value,
      amount_inr: amount,
      note: $("#ledNote").value,
      field_id: ($("#ledgerField") || {}).value || activeParcel(),
      crop: (state.fields || []).find((f) => f.field_id === activeParcel())?.crop || "",
    });
    $("#ledAmount").value = "";
    $("#ledNote").value = "";
    await renderLedger();
    toast("Recorded.");
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
}

RENDERERS.finance = async function renderFinance() {
  const picker = $("#ledgerField");
  if (picker && !picker.innerHTML) {
    picker.innerHTML = '<option value="">All fields</option>'
      + (state.fields || []).map((f) =>
          `<option value="${esc(f.field_id)}">${esc(f.name)}</option>`).join("");
  }
  await Promise.all([runFinance(), renderLedger()]);
};

async function runFinance() {
  const params = new URLSearchParams();
  const put = (key, sel) => {
    const value = ($(sel).value || "").trim();
    if (value) params.set(key, value);
  };
  put("cost", "#finCost");
  put("yield_quintal", "#finYield");
  put("price", "#finPrice");

  const data = await API.get("/api/finance" + (params.toString() ? "?" + params : ""));
  const e = data.economics || {};
  const rows = [
    ["Price used", e.inputs.price_per_quintal_inr
      ? `\u20b9${num(e.inputs.price_per_quintal_inr, 0)} per quintal` : "not known",
      e.price_source],
    ["Break-even yield", e.break_even_quintal ? `${e.break_even_quintal} quintals` : "--",
      "what you must harvest just to cover the cost"],
    ["Break-even price", e.break_even_price_per_quintal
      ? `\u20b9${num(e.break_even_price_per_quintal, 0)}` : "--",
      "the price below which this season loses money"],
    ["Gross revenue", e.gross_revenue_inr ? `\u20b9${num(e.gross_revenue_inr, 0)}` : "--", ""],
    ["Gross margin", e.gross_margin_inr !== undefined
      ? `\u20b9${num(e.gross_margin_inr, 0)}` : "--",
      e.margin_percent !== undefined && e.margin_percent !== null
        ? `${e.margin_percent}% of revenue` : ""],
  ];

  $("#finResult").innerHTML = `
    <div class="status-list">
      ${rows.map(([k, v, note]) => `<div class="status-row">
        <span>${esc(k)}${note ? `<small>${esc(note)}</small>` : ""}</span>
        <b>${esc(v)}</b></div>`).join("")}
    </div>
    ${(e.missing || []).length
      ? `<p class="hint warn-text">Still needed: ${esc(e.missing.join(", "))}.
          Nothing is assumed for these - a cost of cultivation taken from a national
          average is not yours.</p>`
      : ""}
    <p class="hint">${esc(e.caveat || "")}</p>`;

  const c = data.credit || {};
  const ins = data.insurance || {};
  const inc = data.income_support || {};
  $("#finCredit").innerHTML = `
    <div class="fin-card">
      <h3>Kisan Credit Card</h3>
      <p class="fin-big">${esc(c.effective_if_repaid_on_time_percent)}% <small>a year, if repaid on time</small></p>
      <p class="hint">${esc(c.base_interest_percent)}% base, less a ${esc(c.prompt_repayment_rebate_percent)}%
        rebate for prompt repayment. Up to
        \u20b9${num(c.collateral_free_limit_inr, 0)} without security.</p>
      <p class="hint">${esc(c.note)}</p>
    </div>
    ${ins.title ? `<div class="fin-card">
      <h3>Crop insurance</h3>
      <p class="fin-big">${esc(ins.subsidy_percent || "")}</p>
      <p class="hint">${esc(ins.financial_benefit || "")}</p>
      <a class="btn ghost sm" href="${esc(ins.portal_url)}" target="_blank" rel="noopener">PMFBY portal</a>
    </div>` : ""}
    ${inc.title ? `<div class="fin-card">
      <h3>Income support</h3>
      <p class="fin-big">${esc(inc.financial_benefit || "")}</p>
      <a class="btn ghost sm" href="${esc(inc.portal_url)}" target="_blank" rel="noopener">PM-KISAN portal</a>
    </div>` : ""}`;
}

RENDERERS.help = async function renderHelp() {
  const data = await API.get("/api/support");
  $("#helplineGrid").innerHTML = (data.helplines || []).map((h) => `
    <article class="help-card">
      <span class="help-ico">${icon("phone", 18)}</span>
      <b>${esc(h.name)}</b>
      <a class="help-num" href="tel:${esc(h.number.replace(/[^0-9+]/g, ""))}">${esc(h.number)}</a>
      <small>${esc(h.about)}</small>
      <small class="muted-line">${esc(h.hours)}</small>
    </article>`).join("");
  $("#portalGrid").innerHTML = (data.portals || []).map((x) => `
    <article class="help-card">
      <span class="help-ico">${icon("network", 18)}</span>
      <b>${esc(x.name)}</b>
      <small>${esc(x.about)}</small>
      <a class="btn ghost sm" href="${esc(x.url)}" target="_blank" rel="noopener">Open</a>
    </article>`).join("");
  $("#supportNote").textContent = data.note;
};

/* --------------------------------------------------------------- agents */
/* What each agent is about, so fifteen cards can be grouped instead of listed.
   The grouping is editorial - the agents themselves only declare dependencies - so
   it lives here in the page rather than being invented in the API. */
const AGENT_DOMAIN = {
  1: "The ground", 2: "The ground",
  3: "The crop", 4: "The crop", 10: "The crop",
  5: "Water and weather", 7: "Water and weather", 6: "Water and weather",
  8: "Threats", 9: "Threats", 11: "Threats",
  12: "Money", 13: "Money", 14: "Money",
  15: "The check",
};
const DOMAIN_ORDER = ["The ground", "The crop", "Water and weather", "Threats",
                      "Money", "The check"];
const DOMAIN_ICON = { "The ground": "soil", "The crop": "crop",
                      "Water and weather": "water", "Threats": "bug",
                      "Money": "chart", "The check": "check" };

/* The waves the orchestrator actually runs: an agent goes in the first wave whose
   predecessors are all already placed. Same rule as _execute on the server, so the
   picture is the run rather than a drawing of it. */
function executionWaves(agents) {
  const byId = {};
  agents.forEach((a) => { byId[a.id] = a; });
  const placed = new Set();
  const waves = [];
  let left = agents.map((a) => a.id);

  while (left.length && waves.length < 10) {
    const ready = left.filter((id) =>
      (byId[id].depends_on || []).every((d) => placed.has(d) || !byId[d]));
    const wave = ready.length ? ready : left.slice();     // cycle guard, as on the server
    waves.push(wave.map((id) => byId[id]));
    wave.forEach((id) => placed.add(id));
    left = left.filter((id) => !wave.includes(id));
  }
  return waves;
}


/* ------------------------------------------------------ the architecture */
/* Four layers, drawn as rows. Live/not-live comes from the same capability report
   the System Status card uses, so the diagram cannot claim a service is running
   when the key for it is missing. */
function renderArchitecture() {
  const node = $("#archDiagram");
  if (!node) return;
  const caps = {};
  (state.capabilities || []).forEach((c) => { caps[c.name] = c.configured; });
  const live = (name) => (name in caps ? caps[name] : null);

  const LAYERS = [
    {
      title: "How a farmer reaches it",
      items: [
        { label: "Dashboard", note: "browser", ico: "grid" },
        { label: "Phone call", note: "any handset", ico: "phone",
          on: live("Telephony (Twilio via n8n)") },
        { label: "HM-10 sensor", note: "Web Bluetooth", ico: "chip" },
      ],
    },
    {
      title: "Understanding the question",
      items: [
        { label: "Intent router", note: "rules first, 18 intents", ico: "route" },
        { label: "Conversational AI", note: "memory per farmer", ico: "chat",
          on: live("Sarvam AI (voice)") },
        { label: "RAG retrieval", note: "18 agronomy notes", ico: "doc",
          on: live("Mistral embeddings (RAG)") },
      ],
    },
    {
      title: "Working out the answer",
      items: [
        { label: "Orchestrator", note: "routes, resolves, collects", ico: "network" },
        { label: "15 agents", note: "5 dependency waves", ico: "spark" },
        { label: "Agent 15", note: "validates the other 14", ico: "shield" },
      ],
    },
    {
      title: "What the numbers come from",
      items: [
        { label: "Open-Meteo", note: "weather", ico: "sun", on: live("Open-Meteo weather") },
        { label: "Agmarknet", note: "mandi prices", ico: "chart",
          on: live("data.gov.in Agmarknet") },
        { label: "SoilGrids", note: "soil background", ico: "soil",
          on: live("ISRIC SoilGrids") },
        { label: "Your sensor", note: "the seven values", ico: "flask" },
        { label: "Your records", note: "fields, ledger, calls", ico: "map" },
      ],
    },
  ];

  node.innerHTML = LAYERS.map((layer, i) => `
    <div class="arch-layer">
      <div class="arch-label"><span class="arch-step">${i + 1}</span>${esc(layer.title)}</div>
      <div class="arch-items">
        ${layer.items.map((it) => `
          <div class="arch-item ${it.on === false ? "off" : ""}">
            <span class="ai-ico">${icon(it.ico, 17)}</span>
            <span class="ai-text"><b>${esc(it.label)}</b><small>${esc(it.note)}</small></span>
            ${it.on === null || it.on === undefined ? ""
              : `<span class="ai-dot ${it.on ? "on" : "off"}" title="${
                  it.on ? "live" : "not configured"}"></span>`}
          </div>`).join("")}
      </div>
    </div>`).join('<div class="arch-join" aria-hidden="true"></div>')
    + `<p class="hint">A dot means a service with a key behind it: green is live,
        grey is not configured. Everything without a dot is code in this project.</p>`;
}

/* RENDERERS.agents removed with the page it drew. What each of the fifteen
   agents found lives on Pipeline Results, attached to a run the farmer asked
   for, rather than on a second screen listing the machinery. */
/* Which plot the per-field reports are built from. Empty is the whole farm,
   and the two reports that are not about a plot say so on their own card
   rather than pretending the picker changed them. */
let reportField = "";

RENDERERS.reports = async function renderReports() {
  const data = await API.get("/api/reports"
    + (reportField ? "?field=" + encodeURIComponent(reportField) : ""));
  const counts = data.counts || {};

  const picker = $("#reportField");
  if (picker) {
    const plots = data.fields || [];
    picker.innerHTML = `<option value="">Whole farm</option>`
      + plots.map((f) => `<option value="${esc(f.field_id)}">${esc(f.name)}`
          + (f.crop ? ` - ${esc(f.crop)}` : "") + "</option>").join("");
    picker.value = data.field || "";
    // The server is the authority on whether that field is still this farmer's:
    // a plot deleted in another tab must not leave the page filtering by it.
    reportField = picker.value;
  }

  $("#reportGrid").innerHTML = (data.reports || []).map((r) => {
    const scope = r.per_field
      ? (reportField ? "This field only" : "Whole farm")
      : "Whole farm - this one has no field";
    const q = (r.per_field && reportField)
      ? "&field=" + encodeURIComponent(reportField) : "";
    return `
    <article class="report">
      <span class="r-ico">${icon("doc", 18)}</span>
      <b>${esc(r.title)}</b><small>${esc(r.detail)}</small>
      <small class="r-scope">${esc(scope)}</small>
      <span class="btn-row tight">
        <button class="btn ${r.ready ? "primary" : ""}" data-report="${esc(r.id)}" data-format="pdf" data-query="${esc(q)}" ${r.ready ? "" : "disabled"}>PDF</button>
        <button class="btn" data-report="${esc(r.id)}" data-format="xlsx" data-query="${esc(q)}" ${r.ready ? "" : "disabled"}>Excel</button>
        <button class="btn ghost" data-report="${esc(r.id)}" data-format="json" data-query="${esc(q)}" ${r.ready ? "" : "disabled"}>
        ${r.ready ? "JSON" : esc(r.note || "No data yet")}</button>
      </span>
    </article>`;
  }).join("");
};

async function downloadReport(id, format = "json", query = "") {
  // PDF and Excel are rendered on the server, so the browser just fetches the file.
  // JSON is still built here: it is the same data the page already has.
  // `query` carries the chosen field, and is empty for a whole-farm report and
  // for the two reports that have no field.
  const named = (plot) => {
    const f = (state.fields || []).find((x) => x.field_id === plot);
    return f ? "-" + (f.name || plot).toLowerCase().replace(/[^a-z0-9]+/g, "-") : "";
  };
  const suffix = query ? named(reportField) : "";
  const stamp = new Date().toISOString().slice(0, 10);
  try {
    if (format === "pdf" || format === "xlsx") {
      const response = await fetch(`/api/reports/${id}?format=${format}${query}`,
                                   { credentials: "same-origin" });
      if (!response.ok) throw new Error((await response.text()).slice(0, 160));
      const blob = await response.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `saarthi-${id}${suffix}-${stamp}.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast(format.toUpperCase() + " downloaded");
      return;
    }
    const data = await API.get(`/api/reports/${id}${query ? "?" + query.slice(1) : ""}`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `saarthi-${id}${suffix}-${stamp}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Report downloaded");
  } catch (e) { toast(e.message); }
}

/* ----------------------------------------------------------------- init */
/* ---------------------------------------------------------------- theme */
/* The choice is written onto <html> and remembered per browser. Without a stored
   choice the attribute is left off entirely, so the OS preference in the stylesheet
   decides - setting it to "light" up front would override the farmer's own OS dark
   mode on their first visit. */
const THEME_KEY = "saarthi.theme";

function osPrefersDark() {
  return Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

/* Three looks, in the order the button cycles them. Glass is dark with a
   photograph behind it, so anything that asks "is this dark?" must count it. */
const THEMES = ["light", "dark", "glass"];
const THEME_LABEL = { light: "Light", dark: "Dark", glass: "Glass" };

function isDarkTheme(mode) {
  return mode === "dark" || mode === "glass";
}

/* The attribute is always set to a real theme (the head script does it before first
   paint), so the stylesheet never has to guess. A null `mode` means "follow the OS". */
function applyTheme(mode) {
  const theme = THEMES.includes(mode) ? mode : (osPrefersDark() ? "dark" : "light");
  const dark = isDarkTheme(theme);
  document.documentElement.setAttribute("data-theme", theme);
  const btn = $("#themeBtn");
  if (btn) {
    const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    btn.setAttribute("aria-pressed", String(dark));
    // Say where pressing it goes. "Switch theme" on a three-way switch tells
    // you nothing about which of the other two you are about to get.
    btn.title = `${THEME_LABEL[theme]} - press for ${THEME_LABEL[next].toLowerCase()}`;
  }
  document.body.classList.toggle("is-dark", dark);
  $$("#lookRow .look").forEach((b) => {
    b.classList.toggle("on", b.dataset.look === theme);
    b.setAttribute("aria-pressed", String(b.dataset.look === theme));
  });
  const note = $("#lookNote");
  if (note) {
    note.textContent = theme === "glass"
      ? "The picture behind the glass theme is set for everyone by an administrator."
      : "";
  }
  glassBackdrop(theme === "glass");
  refreshMapTiles();
}

function chooseTheme(mode) {
  try { localStorage.setItem(THEME_KEY, mode); } catch { /* private window */ }
  applyTheme(mode);
}

function initTheme() {
  let stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch { stored = null; }
  applyTheme(stored);
  const btn = $("#themeBtn");
  if (btn) {
    btn.addEventListener("click", () => {
      const now = document.documentElement.getAttribute("data-theme");
      const at = THEMES.indexOf(now);
      chooseTheme(THEMES[(at < 0 ? 0 : at + 1) % THEMES.length]);
    });
  }
  const row = $("#lookRow");
  if (row) {
    row.addEventListener("click", (e) => {
      const pick = e.target.closest("[data-look]");
      if (pick) chooseTheme(pick.dataset.look);
    });
  }
  // Follow the OS while the farmer has not chosen for themselves.
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      let choice = null;
      try { choice = localStorage.getItem(THEME_KEY); } catch { choice = null; }
      if (!choice) applyTheme(null);
    });
  }
}

/* ------------------------------------------------- the glass theme backdrop */
/* The picture, and the slideshow if an administrator set one up. It is fetched
   once per session and only when the glass theme is actually in use: a farmer
   on the light theme should not be downloading a photograph they will not see.

   Two layers, alternating. Swapping one background-image would cut, and a cut
   in the thing behind the whole dashboard reads as the page flickering. */
const glass = { images: [], every: 0, at: 0, timer: null, layer: 0, loaded: false };

async function glassBackdrop(on) {
  const box = $("#glassBg");
  if (!box) return;
  if (!on) {
    if (glass.timer) { clearInterval(glass.timer); glass.timer = null; }
    return;
  }
  if (!glass.loaded) {
    glass.loaded = true;                 // one attempt per page load, not per switch
    try {
      const body = await API.get("/api/appearance");
      glass.images = (body.images || []).filter(Boolean);
      glass.every = Number(body.interval_seconds) || 0;
    } catch (e) {
      // No backdrop is not a broken theme: the frosted panels still work over
      // the plain dark background, which is what the tint is for.
      glass.images = [];
    }
  }
  if (!glass.images.length) return;
  glass.at = 0;
  showBackdrop(glass.images[0]);
  if (glass.timer) clearInterval(glass.timer);
  // One picture has nothing to change to, whatever the interval says.
  if (glass.every > 0 && glass.images.length > 1) {
    glass.timer = setInterval(() => {
      glass.at = (glass.at + 1) % glass.images.length;
      showBackdrop(glass.images[glass.at]);
    }, glass.every * 1000);
  }
}

function showBackdrop(url) {
  const layers = [$("#glassA"), $("#glassB")];
  if (!layers[0] || !layers[1] || !url) return;
  const next = layers[glass.layer];
  const current = layers[1 - glass.layer];
  // CSS url() takes a quoted string; a stored path with a bracket or a quote in
  // it would otherwise break out of the value.
  next.style.backgroundImage = `url("${String(url).replace(/["\\]/g, "")}")`;
  next.classList.add("on");
  current.classList.remove("on");
  glass.layer = 1 - glass.layer;
}

/* -------------------------------------------------------------- profile */

/* Which farmer this browser is. Until a number is entered the server uses a
   placeholder identity; once one is saved, every later request has to carry it or
   the dashboard writes to one row and reads back another - which looked exactly
   like "the profile did not save". */
function ownerPhone() {
  if (state.ownerPhone === undefined) {
    try { state.ownerPhone = localStorage.getItem("saarthi.phone") || ""; }
    catch { state.ownerPhone = ""; }
  }
  return state.ownerPhone;
}

function setOwnerPhone(phone) {
  state.ownerPhone = (phone || "").trim();
  try {
    if (state.ownerPhone) localStorage.setItem("saarthi.phone", state.ownerPhone);
    else localStorage.removeItem("saarthi.phone");
  } catch { /* private window */ }
}

const asOwner = (path) => {
  const phone = ownerPhone();
  if (!phone) return path;
  return path + (path.includes("?") ? "&" : "?") + "phone=" + encodeURIComponent(phone);
};

const PROFILE_FIELDS = {
  pfName: "name", pfFullName: "full_name", pfPhone: "phone", pfAltPhone: "alt_phone",
  pfAge: "age", pfEmail: "email", pfLang: "language", pfVillage: "village", pfTehsil: "tehsil",
  pfDistrict: "district", pfState: "state", pfPincode: "pincode", pfLandmark: "landmark",
  pfAcres: "land_acres", pfSoil: "soil_type", pfIrrigation: "irrigation",
  pfSince: "farming_since", pfType: "farmer_type", pfCallDelay: "call_delay_minutes",
};

RENDERERS.profile = async function renderProfile() {
  const data = await API.get(asOwner("/api/profile"));
  // Opened locked, every time. Leaving it editable after a save means the next
  // person at the keyboard finds a form that looks ready to be typed into.
  if ($("#pfEdit")) lockProfile(true);
  // The voice card draws itself from its own status call; it is not part of the
  // profile record and is not locked with it.
  if (window.SaarthiVoiceEnrol) window.SaarthiVoiceEnrol.refresh();
  state.profile = data;
  const p = data.profile || {};
  state.profileName = p.name || p.full_name || "";

  Object.entries(PROFILE_FIELDS).forEach(([id, key]) => {
    const el = $("#" + id);
    if (!el) return;
    if (id === "pfLang" && !el.innerHTML) {
      el.innerHTML = state.languages.map((l) =>
        `<option value="${esc(l.code)}">${esc(l.native_name)} (${esc(l.english_name)})</option>`).join("");
    }
    el.value = p[key] === null || p[key] === undefined ? "" : String(p[key]);
  });
  if (!p.call_delay_minutes) $("#pfCallDelay").value = "0";
  $("#pfFieldCount").value = `${data.field_count} field(s), ${data.total_acres} acres mapped`;

  const missing = (data.complete || {}).missing || [];
  $("#pfMissing").textContent = missing.length
    ? "Still missing: " + missing.join(", ") + ". The agents fall back to defaults for anything blank."
    : "Every detail the agents use is filled in.";

  const pending = (data.call_settings || {}).pending;
  $("#pfPending").innerHTML = pending
    ? `<div class="status-row"><span>Call waiting</span>
         <b>${esc(pending.phone)} - ${esc(pending.status)}</b></div>`
    : `<div class="status-row"><span>Call waiting</span><b>None</b></div>`;

  renderFacts(data.remembered_facts || []);
  paintSidebarIdentity(p);
};

function paintSidebarIdentity(p) {
  const name = p.name || p.full_name || "Farmer";
  // Initials come from the fullest name available: the call name is often one word
  // ("Ramesh"), and a single-letter avatar reads like a placeholder.
  const initials = (p.full_name || name).split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("") || "F";
  const set = (sel, value) => { const el = $(sel); if (el) el.textContent = value; };
  set("#userName", name);
  set("#userAvatar", initials);
  set("#profileBtn", initials);
  set("#userRole", p.village ? `Farmer - ${p.village}` : "Farmer");
  if (p.village) {
  }
}

function renderFacts(facts) {
  $("#pfFacts").innerHTML = facts.length
    ? facts.map((f) => `<div class="fact">
        <b>${esc(String(f.key).replace(/_/g, " "))}</b>
        <span>${esc(f.value)}</span>
        <small>heard ${esc(String(f.last_heard || "").replace("T", " ").slice(0, 16))}</small>
      </div>`).join("")
    : `<p class="hint">Saarthi has not learned anything from your calls yet.</p>`;
}

/* The profile is read-only until Edit is pressed, and a save is confirmed with a
   one-time code.

   Both halves are deliberate. A form that looks editable and then refuses to
   save is worse than one that plainly is not - the farmer types the whole thing
   before finding out. And the code is checked on the SERVER: a check on the page
   is one anybody can skip, and what is being protected here is which account a
   caller reaches and whose land the weather is read for. */
function lockProfile(locked) {
  const page = $("#page-profile");
  if (!page) return;
  page.classList.toggle("pf-locked", locked);
  page.querySelectorAll("#page-profile input, #page-profile select, #page-profile textarea")
    .forEach((el) => {
      if (el.id === "pfCode" || el.id === "pfFieldCount") return;   // the code box, and a read-only count
      if (el.closest(".pf-code")) return;
      /* The erase-memory card is not part of editing the profile - it has its
         own confirm and its own button, which stays live either way. Greying
         its tick box while leaving its button pressable was the worst of both. */
      if (el.id === "pfKeepName") return;
      el.disabled = locked;
    });
  // The place search writes INTO those boxes, so it is part of editing them.
  // The input is caught by the sweep above; its button is not an input.
  if ($("#pfPlaceGo")) $("#pfPlaceGo").disabled = locked;
  if (locked && $("#pfPlaceResults")) {
    $("#pfPlaceResults").hidden = true;
    $("#pfPlaceResults").innerHTML = "";
  }
  $("#pfEdit").hidden = !locked;
  $("#pfSave").hidden = locked;
  $("#pfCancel").hidden = locked;
  $("#pfCodeRow").hidden = locked;
  if (locked) {
    $("#pfCode").value = "";
    $("#pfCodeNote").textContent = "";
    $("#pfCodeField").hidden = true;
    $("#pfResend").hidden = true;
    $("#pfSendCode").hidden = false;
  }
  // Nothing can be saved until a code has been asked for and typed in.
  $("#pfSave").disabled = true;
}

async function askProfileCode() {
  $("#pfCodeNote").textContent = "Sending a code...";
  $("#pfSendCode").disabled = true;
  try {
    const body = await API.post("/api/profile/edit-code", {});
    $("#pfCodeNote").textContent = body.sent
      ? "Code sent to " + (body.sent_to || "your phone and email") + "."
      : (body.reason || "Could not send a code.");
    if (body.sent) {
      $("#pfSendCode").hidden = true;
      $("#pfCodeField").hidden = false;
      $("#pfResend").hidden = false;
      $("#pfCode").focus();
    }
  } catch (e) {
    $("#pfCodeNote").textContent = e.message;
  }
  $("#pfSendCode").disabled = false;
}

async function saveProfile() {
  const payload = {};
  Object.entries(PROFILE_FIELDS).forEach(([id, key]) => {
    const el = $("#" + id);
    if (el) payload[key] = el.value.trim ? el.value.trim() : el.value;
  });
  payload.code = ($("#pfCode").value || "").trim();
  // The number typed here becomes this browser's identity from now on.
  if (payload.phone) setOwnerPhone(payload.phone);
  const btn = $("#pfSave"); btn.disabled = true; btn.textContent = "Saving...";
  try {
    await API.post("/api/profile", payload);
    $("#pfSaveNote").textContent = "Saved. These details now drive the agents and your calls.";
    lockProfile(true);
    await RENDERERS.profile();
    /* The header, the sidebar chip and the greeting are painted from the record, so
       an edit has to repaint them or the old name sits there until the next reload. */
    await paintIdentity();
    await paintFieldPickers();
  } catch (e) {
    $("#pfSaveNote").textContent = e.message;
  }
  btn.disabled = false; btn.textContent = "Save profile";
}

async function eraseMemory() {
  const keep = $("#pfKeepName").checked;
  const warning = keep
    ? "Erase everything Saarthi remembers from your calls? Your name and the details you typed are kept. This cannot be undone."
    : "Erase everything, including your name? This cannot be undone.";
  if (!window.confirm(warning)) return;
  const btn = $("#pfErase"); btn.disabled = true; btn.textContent = "Erasing...";
  try {
    const res = await API.del(asOwner(`/api/profile/memory?keep_name=${keep}`));
    toast(res.message);
    await RENDERERS.profile();
  } catch (e) { toast(e.message); }
  btn.disabled = false; btn.textContent = "Erase memory";
}

/* --------------------------------------------------------------- fields */
const PLOT_HALF_SIZE = 0.0006;   // ~65 m: one fixed plot box, drawn where you click

function plotBounds(lat, lon) {
  return { north: +(lat + PLOT_HALF_SIZE).toFixed(6), south: +(lat - PLOT_HALF_SIZE).toFixed(6),
           east: +(lon + PLOT_HALF_SIZE).toFixed(6), west: +(lon - PLOT_HALF_SIZE).toFixed(6) };
}

/* Standard OpenStreetMap tiles: no key, no account, no per-domain registration.
   (Carto's hosted basemaps were tried first and served "MAP KEY REQUIRED" tiles.)
   Dark mode inverts the tiles in CSS rather than pulling in a second provider. */
/* Two maps share this code: the editable one on My Fields, and a read-only one in the
   overview card. They are kept in `maps` by name so the zoom and full-screen buttons
   can address either with a data attribute instead of duplicated handlers. */
const maps = Object.create(null);


/* Locate and home, on every map. "Home" is the registered land, which is the one a
   farmer wants when the GPS has wandered or they are checking from the house. */
async function mapLocate(name) {
  const entry = maps[name];
  if (!entry) return;
  try {
    const here = await browserPosition();
    entry.map.setView([Number(here.lat), Number(here.lon)], 16);
    if (entry.meMarker) entry.map.removeLayer(entry.meMarker);
    entry.meMarker = window.L.circleMarker([Number(here.lat), Number(here.lon)], {
      radius: 7, color: "#2f80d6", weight: 2, fillColor: "#6aa9ec", fillOpacity: .9,
    }).addTo(entry.map).bindTooltip("You are here");
  } catch (e) {
    toast("Could not get your location.");
  }
}

/* ------------------------------------------------------- finding a place */
/* Type a village, get the map there. The lookup is on the server - a geocoding
   key in a page is a key anybody can spend - and it answers through Google
   where the account allows it and OpenStreetMap where it does not, which is the
   same source as the tiles underneath. */
let placeTimer = null;

function placeRow(place) {
  const where = [place.village, place.district, place.state].filter(Boolean).join(", ");
  return `<button class="place-hit" type="button"
    data-lat="${esc(place.latitude)}" data-lon="${esc(place.longitude)}"
    data-village="${esc(place.village)}" data-tehsil="${esc(place.tehsil)}"
    data-district="${esc(place.district)}" data-state="${esc(place.state)}"
    data-pincode="${esc(place.pincode)}">
    <b>${esc(where || place.label)}</b>
    <small>${esc(place.label)}</small></button>`;
}

async function findPlaces(query) {
  const box = $("#placeResults");
  if (!box) return;
  const text = (query || "").trim();
  if (text.length < 2) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `<p class="hint">Looking…</p>`;
  try {
    const data = await API.get("/api/geo/search?q=" + encodeURIComponent(text));
    const places = data.places || [];
    box.innerHTML = places.length
      ? places.map(placeRow).join("")
        + `<p class="hint place-src">From ${esc(places[0].source)}</p>`
      : `<p class="hint">${esc(data.note || "No place by that name.")}</p>`;
  } catch (e) {
    box.innerHTML = `<p class="hint">${esc(e.message)}</p>`;
  }
}

/* The profile's own copy. Picking a result fills the four address boxes rather
   than moving a map - the district on this page decides which mandi's prices
   the farmer is shown, and a typo here is a wrong price later. */
function wireProfilePlaceSearch() {
  const input = $("#pfPlaceQuery");
  const box = $("#pfPlaceResults");
  if (!input || !box) return;
  let timer = null;
  const run = async () => {
    const text = (input.value || "").trim();
    if (text.length < 2) { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;
    box.innerHTML = `<p class="hint">Looking…</p>`;
    try {
      const data = await API.get("/api/geo/search?q=" + encodeURIComponent(text));
      const places = data.places || [];
      box.innerHTML = places.length
        ? places.map(placeRow).join("")
          + `<p class="hint place-src">From ${esc(places[0].source)}</p>`
        : `<p class="hint">${esc(data.note || "No place by that name.")}</p>`;
    } catch (e) {
      box.innerHTML = `<p class="hint">${esc(e.message)}</p>`;
    }
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 450); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(timer); run(); }
  });
  const go = $("#pfPlaceGo");
  if (go) go.addEventListener("click", run);

  box.addEventListener("click", (e) => {
    const hit = e.target.closest(".place-hit");
    if (!hit) return;
    // Only what the lookup actually knows. An empty district left as it was
    // beats an empty district written over the one the farmer typed.
    const fill = [["#pfVillage", "village"], ["#pfTehsil", "tehsil"],
                  ["#pfDistrict", "district"], ["#pfState", "state"],
                  ["#pfPincode", "pincode"]];
    let written = 0;
    fill.forEach(([sel, key]) => {
      const value = hit.dataset[key] || "";
      const el = $(sel);
      if (el && value) { el.value = value; written += 1; }
    });
    const lat = Number(hit.dataset.lat), lon = Number(hit.dataset.lon);
    if ($("#pfLat") && Number.isFinite(lat)) $("#pfLat").value = lat.toFixed(6);
    if ($("#pfLon") && Number.isFinite(lon)) $("#pfLon").value = lon.toFixed(6);
    box.hidden = true;
    input.value = "";
    toast(written ? `Filled in ${written} field(s). Press Save to keep them.`
                  : "That place had no address details.");
  });
}

function wirePlaceSearch() {
  wireProfilePlaceSearch();
  const input = $("#placeQuery");
  const box = $("#placeResults");
  if (!input || !box) return;
  const run = () => findPlaces(input.value);
  input.addEventListener("input", () => {
    // Debounced: a lookup per keystroke is a lookup per keystroke of somebody
    // else's quota.
    clearTimeout(placeTimer);
    placeTimer = setTimeout(run, 450);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(placeTimer); run(); }
  });
  const go = $("#placeGo");
  if (go) go.addEventListener("click", run);

  box.addEventListener("click", (e) => {
    const hit = e.target.closest(".place-hit");
    if (!hit) return;
    const lat = Number(hit.dataset.lat);
    const lon = Number(hit.dataset.lon);
    const entry = maps.fields;
    if (entry && Number.isFinite(lat) && Number.isFinite(lon)) {
      entry.map.setView([lat, lon], 15);
    }
    box.hidden = true;
    input.value = hit.querySelector("b").textContent;
    toast("Map moved. Press Add field, then click where the plot is.");
  });
}

/* What is under a dropped pin. Used to fill the profile from the map rather
   than asking a farmer to type their own district into a box. */
async function placeAt(lat, lon) {
  try {
    const data = await API.get(`/api/geo/reverse?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
    return data.place || null;
  } catch (e) {
    return null;
  }
}

function mapHome(name) {
  const entry = maps[name];
  if (!entry) return;
  const field = (state.fields || []).find((f) => f.field_id === activeParcel())
    || (state.fields || [])[0];
  if (!field || field.centre_lat === null || field.centre_lat === undefined) {
    toast("No field is placed on the map yet.");
    return;
  }
  entry.map.setView([Number(field.centre_lat), Number(field.centre_lon)], 16);
}

/* The ground under the field boxes.
   -------------------------------------------------------------------------
   Google draws it, through its own library, underneath Leaflet - see
   googlemap.js for why it cannot be a tile layer. OpenStreetMap is kept only
   as the fallback for when Google will not load, which is a key problem, a
   referrer problem or a billing problem and is fixed in the Google console
   rather than here. A farmer who cannot see the ground cannot place a field,
   so there is always something drawn.

   The dark filter is for OSM only. Google's hybrid view is satellite imagery
   and is already dark; inverting it would make a photograph of a field look
   like a negative. */
/* The ground under the field boxes, and the only source of it.
   -------------------------------------------------------------------------
   Google draws it through its own library, underneath Leaflet - see
   googlemap.js for why it cannot be an `L.tileLayer`. There is no tile
   fallback any more: OpenStreetMap was removed on 2026-11-02 at the user's
   request, so if Google does not load there is nothing behind the field boxes.

   That makes it worth saying so out loud. A blank grey rectangle looks like a
   bug in Saarthi; the three things that actually cause it are all fixed in the
   Google Cloud console - a key that is not set, a key whose HTTP-referrer
   allowlist does not name this hostname, and a project with billing off - and
   none of them stops the farmer registering a field by typing the coordinates.

   `tiles-dark` is removed rather than toggled: it inverts tile colours for the
   dark theme, which is right for a street map and wrong for a photograph. */
function refreshMapTiles() {
  Object.values(maps).forEach((entry) => {
    if (!entry || !entry.map || !window.L) return;
    const container = entry.map.getContainer();
    if (container) container.classList.remove("tiles-dark");

    if (!window.SaarthiGoogleMap) { mapUnavailable(entry); return; }
    if (window.SaarthiGoogleMap.attached(entry.map)) return;
    // Backing off between attempts, so a key that is genuinely wrong is not
    // asked about on every page change for the whole visit.
    if (entry.retryAt && Date.now() < entry.retryAt) return;
    window.SaarthiGoogleMap.attach(entry.map).then((ok) => {
      if (ok) {
        entry.retryAt = 0;
        const hint = (entry.spec || {}).hint ? $(entry.spec.hint) : null;
        if (hint) hint.textContent = "";
        entry.map.getContainer().classList.remove("map-unavailable");
      } else {
        mapUnavailable(entry);
      }
    });
  });
}

/* Say what happened, in the place this screen already keeps that message. */
function mapUnavailable(entry) {
  /* Not for ever. The library load can fail for a reason that has passed by the
     next time the farmer opens a page with a map on it - a dropped request, a
     throttle - and before this the first failure of a visit was the last word
     on it, because nothing ever asked again. */
  entry.retryAt = Date.now() + 15000;
  const spec = entry.spec || {};
  const hint = spec.hint ? $(spec.hint) : null;
  if (hint) {
    hint.textContent = spec.editable
      ? "The map could not be loaded just now. Type the latitude and longitude "
        + "into the form instead - everything else still works."
      : "The map could not be loaded just now.";
  }
  const container = entry.map.getContainer();
  if (container) container.classList.add("map-unavailable");
}

/* Full screen on the map's WRAPPER, not the map element: the zoom and full-screen
   buttons are siblings of the map, and taking only the map would leave them behind on
   the page underneath.

   Two mechanisms, because the first is not always allowed. The native Fullscreen API
   is tried first; where it is refused - an embedded view, an iframe without the
   fullscreen permission, some locked-down browsers - the wrapper is instead pinned
   over the viewport with CSS, which looks the same to the farmer and always works. */
async function toggleMapFullscreen(name) {
  const entry = maps[name];
  if (!entry) return;
  const target = entry.wrap;
  const resize = () => setTimeout(() => entry.map && entry.map.invalidateSize(), 120);

  if (target.classList.contains("map-maximised") || document.fullscreenElement) {
    target.classList.remove("map-maximised");
    if (document.fullscreenElement) {
      try { await document.exitFullscreen(); } catch { /* already gone */ }
    }
    resize();
    return;
  }

  try {
    if (!target.requestFullscreen) throw new Error("not supported");
    await target.requestFullscreen();
  } catch {
    target.classList.add("map-maximised");          // the fallback, not an error
  }
  resize();
}

// Escape leaves the CSS fallback, the way it leaves real full screen.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const open = document.querySelector(".map-wrap.map-maximised");
  if (!open) return;
  open.classList.remove("map-maximised");
  Object.values(maps).forEach((m) => m && m.map && setTimeout(() => m.map.invalidateSize(), 120));
});

RENDERERS.fields = async function renderFields() {
  const data = await API.get(asOwner("/api/profile/fields"));
  state.fields = data.fields || [];
  $("#fieldsSummary").textContent = data.count
    ? `${data.count} plot(s) mapped, ${data.total_acres} acres in total`
    : "Nothing mapped yet - add your first field";

  const plots = data.fields || [];
  const crops = new Set(plots.map((f) => f.crop).filter(Boolean));
  const located = plots.filter((f) => f.centre_lat !== null && f.centre_lat !== undefined).length;

  if (state.vision && state.vision.crops) {
    $("#cropOptions").innerHTML = state.vision.crops.map((c) =>
      `<option value="${esc(c.replace(/_/g, " "))}">`).join("");
  }
  renderFieldList();
  ensureMap();
};

function renderFieldList() {
  const list = state.fields || [];
  $("#fieldList").innerHTML = list.length ? list.map((f) => `
    <div class="field-card">
      <div class="field-card-head">
        <b>${esc(f.name)}</b>
        <span class="chip-static">${esc(f.crop || "no crop set")}</span>
      </div>
      <div class="field-card-rows">
        <span><small>Size</small><b>${f.area_acres ? esc(f.area_acres) + " acres" : "--"}</b></span>
        <span><small>Soil</small><b>${esc(f.soil_type || "--")}</b></span>
        <span><small>Irrigation</small><b>${esc(f.irrigation || "--")}</b></span>
        <span><small>Sown</small><b>${esc(f.sowing_date || "--")}</b></span>
      </div>
      ${f.notes ? `<p class="hint">${esc(f.notes)}</p>` : ""}
      <div class="btn-row tight">
        <button class="btn ghost sm" data-edit-field="${esc(f.field_id)}">Edit</button>
        <button class="btn ghost sm" data-locate-field="${esc(f.field_id)}">Show on map</button>
        <button class="btn danger sm" data-del-field="${esc(f.field_id)}">Delete</button>
      </div>
    </div>`).join("")
    : `<p class="hint">No fields registered. Press "Add field" and click where your plot is.</p>`;
}

function ensureMap(name = "fields") {
  const spec = name === "overview"
    ? { node: "overviewLeaflet", fallback: "#overviewMapFallback", hint: "#overviewMapHint",
        editable: false }
    : name === "sensor"
    ? { node: "sensorMap", fallback: null, hint: null, editable: false }
    : { node: "fieldLeaflet", fallback: "#mapFallback", hint: "#mapHint", editable: true };

  const node = document.getElementById(spec.node);
  if (!node) return;

  if (!window.L) {                       // the CDN did not load
    const fallback = spec.fallback ? $(spec.fallback) : null;
    if (fallback) fallback.hidden = false;
    const hint = spec.hint ? $(spec.hint) : null;
    if (hint && spec.editable) {
      hint.textContent = "Map tiles could not be loaded. Type the latitude and longitude "
        + "into the form instead - everything else still works.";
    }
    return;
  }

  if (maps[name]) {
    maps[name].map.invalidateSize();
    // Another attempt at the basemap, if the last one did not work out.
    if (!window.SaarthiGoogleMap || !window.SaarthiGoogleMap.attached(maps[name].map)) {
      refreshMapTiles();
    }
    drawFieldShapes();
    return;
  }

  const first = (state.fields || [])[0];
  const centre = first && first.centre_lat
    ? [first.centre_lat, first.centre_lon] : [27.4924, 77.6737];
  /* No attribution control. Leaflet draws no tiles on this map - Google draws
     the ground and puts its own attribution on the canvas - so the "Leaflet"
     badge credited a basemap that is not there. The controls on the right are
     this project's own buttons, not Leaflet's. */
  const map = window.L.map(spec.node, { zoomControl: false, attributionControl: false })
    .setView(centre, 16);
  // The spec travels with the map so that a basemap that fails LATER - the
  // Google library loads on demand, after this function has returned - can
  // still find this screen's hint element to explain itself in.
  maps[name] = { map, spec, wrap: node.closest(".map-wrap") || node,
                 layer: window.L.layerGroup().addTo(map), tileLayer: null };
  if (name === "fields") state.fieldMap = map;     // the editable one, for locate/centre

  refreshMapTiles();

  if (spec.editable) {
    map.on("click", (e) => {
      if (!state.addingField) return;
      placePlot(e.latlng.lat, e.latlng.lng);
    });
  }
  drawFieldShapes();
  setTimeout(() => map.invalidateSize(), 60);
}

/* One click puts down a fixed-size plot box and fills the coordinates in the form.
   The box is deliberately a constant size: a farmer on a phone cannot trace a field
   boundary accurately, and a box of the right rough size in the right place is more
   honest than a precise-looking polygon nobody checked. */
function placePlot(lat, lon) {
  state.draftPlot = { lat, lon, bounds: plotBounds(lat, lon) };
  $("#fieldLat").value = lat.toFixed(6);
  $("#fieldLon").value = lon.toFixed(6);
  drawFieldShapes();
  $("#mapNote").textContent = "Plot placed. Fill in the details and save.";
  if ($("#fieldName").value.trim() === "") {
    $("#fieldName").value = `Field ${(state.fields || []).length + 1}`;
  }
  $("#fieldName").focus();
}

/* Draws the saved plots onto every live map. The draft plot and its draggable marker
   belong only to the editable map - the overview is a view, not an editor. */
function drawFieldShapes() {
  const L = window.L;
  if (!L) return;
  Object.entries(maps).forEach(([name, entry]) => {
    if (!entry || !entry.layer) return;
    entry.layer.clearLayers();
    (state.fields || []).forEach((f) => {
      const b = f.bounds || {};
      if (b.north === undefined) return;
      L.rectangle([[b.south, b.west], [b.north, b.east]], {
        color: "#1f9d4d", weight: 2, fillColor: "#35c46a", fillOpacity: 0.3,
      }).bindTooltip(`${f.name}${f.crop ? " - " + f.crop : ""}`, { permanent: false })
        .addTo(entry.layer);
    });

    const draft = state.draftPlot;
    if (draft && name === "fields") {
      const b = draft.bounds;
      L.rectangle([[b.south, b.west], [b.north, b.east]], {
        color: "#d9930c", weight: 2, dashArray: "5 4", fillColor: "#e8a93a", fillOpacity: 0.25,
      }).bindTooltip("New field - not saved yet").addTo(entry.layer);
      L.marker([draft.lat, draft.lon], { draggable: true })
        .on("dragend", (e) => {
          const pos = e.target.getLatLng();
          placePlot(pos.lat, pos.lng);
        }).addTo(entry.layer);
    }
  });

  // Frame whatever exists, so the overview opens on the farm rather than on a default.
  const overview = maps.overview;
  const saved = (state.fields || []).filter((f) => (f.bounds || {}).north !== undefined);
  if (overview && saved.length) {
    const bounds = L.latLngBounds(saved.map((f) =>
      [[f.bounds.south, f.bounds.west], [f.bounds.north, f.bounds.east]]).flat());
    overview.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 16 });
  }
}

async function saveField(event) {
  event.preventDefault();
  const lat = parseFloat($("#fieldLat").value);
  const lon = parseFloat($("#fieldLon").value);
  const payload = {
    field_id: $("#fieldId").value || "",
    name: $("#fieldName").value.trim(),
    crop: $("#fieldCrop").value.trim(),
    area_acres: $("#fieldArea").value,
    soil_type: $("#fieldSoil").value,
    irrigation: $("#fieldIrrigation").value,
    sowing_date: $("#fieldSowing").value,
    notes: $("#fieldNotes").value.trim(),
    centre_lat: Number.isFinite(lat) ? lat : null,
    centre_lon: Number.isFinite(lon) ? lon : null,
    bounds: Number.isFinite(lat) && Number.isFinite(lon) ? plotBounds(lat, lon) : {},
  };
  if (!payload.name) { toast("Give the field a name"); return; }
  if (ownerPhone()) payload.phone = ownerPhone();
  try {
    await API.post("/api/profile/fields", payload);
    toast(payload.field_id ? "Field updated" : "Field saved");
    resetFieldForm();
    await RENDERERS.fields();
  } catch (e) { toast(e.message); }
}

function resetFieldForm() {
  $("#fieldForm").reset();
  $("#fieldId").value = "";
  state.draftPlot = null;
  state.addingField = false;
  $("#fieldFormTitle").textContent = "Register a field";
  $("#fieldCancel").hidden = true;
  $("#addFieldBtn").textContent = "Add field";
  $("#mapNote").textContent = 'Press "Add field", then click the map where the plot is';
  drawFieldShapes();
}

function editField(id) {
  const f = (state.fields || []).find((x) => x.field_id === id);
  if (!f) return;
  $("#fieldId").value = f.field_id;
  $("#fieldName").value = f.name || "";
  $("#fieldCrop").value = f.crop || "";
  $("#fieldArea").value = f.area_acres || "";
  $("#fieldSoil").value = f.soil_type || "";
  $("#fieldIrrigation").value = f.irrigation || "";
  $("#fieldSowing").value = f.sowing_date || "";
  $("#fieldNotes").value = f.notes || "";
  $("#fieldLat").value = f.centre_lat || "";
  $("#fieldLon").value = f.centre_lon || "";
  $("#fieldFormTitle").textContent = "Edit " + (f.name || "field");
  $("#fieldCancel").hidden = false;
  if (f.centre_lat && state.fieldMap) state.fieldMap.setView([f.centre_lat, f.centre_lon], 17);
}

function initFields() {
  $("#fieldForm").addEventListener("submit", saveField);
  $("#fieldCancel").addEventListener("click", resetFieldForm);
  $("#addFieldBtn").addEventListener("click", () => {
    state.addingField = !state.addingField;
    $("#addFieldBtn").textContent = state.addingField ? "Click the map..." : "Add field";
    $("#mapNote").textContent = state.addingField
      ? "Now click the map where this plot is"
      : 'Press "Add field", then click the map where the plot is';
  });
  $("#locateBtn").addEventListener("click", locateMe);

  // One handler for both maps' zoom and full-screen buttons.
  document.addEventListener("click", (e) => {
    const zoom = e.target.closest("[data-map-zoom]");
    if (zoom) {
      const entry = maps[zoom.dataset.map];
      if (entry) entry.map[zoom.dataset.mapZoom === "in" ? "zoomIn" : "zoomOut"]();
      return;
    }
    const full = e.target.closest("[data-map-full]");
    if (full) toggleMapFullscreen(full.dataset.mapFull);
  });

  $("#fieldList").addEventListener("click", async (e) => {
    const edit = e.target.closest("[data-edit-field]");
    if (edit) { editField(edit.dataset.editField); return; }
    const show = e.target.closest("[data-locate-field]");
    if (show) {
      const f = (state.fields || []).find((x) => x.field_id === show.dataset.locateField);
      if (f && f.centre_lat && state.fieldMap) state.fieldMap.setView([f.centre_lat, f.centre_lon], 17);
      return;
    }
    const del = e.target.closest("[data-del-field]");
    if (del) {
      const f = (state.fields || []).find((x) => x.field_id === del.dataset.delField);
      if (!window.confirm(`Delete ${f ? f.name : "this field"}? This cannot be undone.`)) return;
      try { await API.del("/api/profile/fields/" + del.dataset.delField); toast("Field deleted"); }
      catch (err) { toast(err.message); }
      await RENDERERS.fields();
    }
  });
}

function locateMe() {
  if (!navigator.geolocation) { toast("This browser cannot report your location"); return; }
  const btn = $("#locateBtn"); btn.disabled = true; btn.textContent = "Locating...";
  navigator.geolocation.getCurrentPosition((pos) => {
    const { latitude, longitude, accuracy } = pos.coords;
    if (state.fieldMap) {
      state.fieldMap.setView([latitude, longitude], 17);
      if (state.youMarker) state.fieldMap.removeLayer(state.youMarker);
      state.youMarker = window.L.circle([latitude, longitude], {
        radius: Math.max(accuracy, 12), color: "#2f80d6", fillColor: "#2f80d6", fillOpacity: 0.18,
      }).addTo(state.fieldMap).bindTooltip(`You are here (±${Math.round(accuracy)} m)`);
    }
    $("#fieldLat").value = latitude.toFixed(6);
    $("#fieldLon").value = longitude.toFixed(6);
    btn.disabled = false; btn.textContent = "My location";
  }, (err) => {
    toast(err.code === 1 ? "Location permission was refused" : "Could not get your location");
    btn.disabled = false; btn.textContent = "My location";
  }, { enableHighAccuracy: true, timeout: 10000 });
}

/* ---------------------------------------------------------------- admin */
/* Every account on the server. The link into this page is hidden from farmers, but
   that is only tidiness - the API refuses them either way, which is where the rule
   actually lives. */
/**
 * An administrator sees the server, not a farm.
 *
 * These accounts have no fields, no readings and no crop, so every farmer page
 * renders an empty state and the chain has nothing to reason about. Showing
 * them anyway suggests the account is simply new, which sends an administrator
 * looking for data that was never going to be there. The API refuses either
 * way; this is about not asking the question.
 */
function applyAdminShell() {
  const admin = Boolean(state.isAdmin);
  if (admin) {
    /* "Farmer - Rampur" under an administrator account is simply wrong: the
       account has no village and does not farm. */
    const name = $("#userName");
    const role = $("#userRole") || $(".user small");
    const avatar = $("#userAvatar");
    if (name) name.textContent = "Administrator";
    if (role) role.textContent = "Server administration";
    if (avatar) avatar.textContent = "AD";
  }
  $("#adminNav").hidden = !admin;
  const knowledgeNav = $("#knowledgeNav");
  if (knowledgeNav) knowledgeNav.hidden = !admin;
  document.body.classList.toggle("admin-only", admin);
  /* The avatar opens My Profile, which an administrator has no use for. */
  const avatarLink = $("#userAvatar");
  if (avatarLink) avatarLink.style.pointerEvents = admin ? "none" : "";
  ["#navFarmer", "#navFarmerTop"].forEach((sel) => {
    const group = $(sel);
    if (group) group.hidden = admin;
  });
  if (admin) {
    const label = $("#userRoleLabel") || $(".user small");
    if (label) label.textContent = "Administrator";
  }
}

/** Minutes, hours or days, whichever reads. */
function sinceLabel(iso) {
  if (!iso) return "never";
  const then = new Date(String(iso).endsWith("Z") || String(iso).includes("+")
    ? iso : iso + "Z");
  if (Number.isNaN(then.getTime())) return String(iso);
  const mins = Math.floor((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + " min ago";
  if (mins < 1440) return Math.floor(mins / 60) + " h ago";
  return Math.floor(mins / 1440) + " days ago";
}

/** The local clock, so a server timestamp is not read five hours out. */
function localStamp(iso) {
  if (!iso) return "never";
  const then = new Date(String(iso).endsWith("Z") || String(iso).includes("+")
    ? iso : iso + "Z");
  if (Number.isNaN(then.getTime())) return String(iso);
  return then.toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true,
  });
}

/**
 * Each service on its own row.
 *
 * A single "healthy" light would go out for any one of these and tell an
 * administrator nothing about which. Anything the server cannot reach is named,
 * because the fix differs: a missing key is a deployment problem, an
 * unreachable mandi feed is somebody else's outage.
 */
/**
 * The top-bar weather for an account with no land.
 *
 * The widget reads the farm's coordinates, and an administrator has none, so it
 * sat on "Loading" for ever. Their own position is the only sensible stand-in,
 * and it is labelled as such rather than passed off as a farm reading.
 */
async function paintAdminWeather() {
  const chip = $("#topWeather") || $(".top-weather");
  if (!chip) return;

  /* The pill is a sun glyph, a figure and a caption. Replacing its text
     wholesale would throw the glyph away, so each part is set on its own. */
  const show = (value, caption, title) => {
    const figure = chip.querySelector("b");
    const note = chip.querySelector("small");
    if (figure) figure.textContent = value;
    if (note) note.textContent = caption;
    chip.title = title || caption;
  };

  if (!navigator.geolocation) {
    show("--", "no location");
    return;
  }

  navigator.geolocation.getCurrentPosition(async (pos) => {
    const lat = pos.coords.latitude.toFixed(4);
    const lon = pos.coords.longitude.toFixed(4);
    try {
      const data = await API.get(`/api/weather?lat=${lat}&lon=${lon}&days=1`);
      const now = (data && data.current) || {};
      if (now.temperature_c === undefined || now.temperature_c === null) {
        show("--", "not available");
        return;
      }
      show(`${Math.round(now.temperature_c)}\u00b0C`,
           now.condition || "where you are",
           "Where you are now, not a registered farm");
    } catch (err) {
      show("--", "not available");
    }
  }, () => {
    show("--", "location not shared");
  }, { timeout: 8000, maximumAge: 600000 });
}

async function renderAdminHealth() {
  const box = $("#adminHealth");
  if (!box) return;
  box.innerHTML = `<p class="hint">Checking...</p>`;

  const [health, keys] = await Promise.all([
    API.get("/api/health").catch(() => null),
    API.get("/api/keys/status").catch(() => null),
  ]);

  const rows = [];
  rows.push({
    label: "Dashboard API",
    ok: Boolean(health),
    value: health ? "reachable" : "not answering",
  });

  const services = (keys && (keys.keys || keys.services || keys)) || {};
  Object.entries(services).forEach(([name, raw]) => {
    if (raw === null || typeof raw === "object" && raw === null) return;
    const configured = typeof raw === "boolean" ? raw
      : typeof raw === "object" ? Boolean(raw.configured ?? raw.ok ?? raw.available)
      : String(raw).toLowerCase() !== "missing" && String(raw).toLowerCase() !== "not set";
    const note = typeof raw === "object" && raw.note ? raw.note : "";
    rows.push({
      label: name.replace(/_/g, " "),
      ok: configured,
      value: configured ? (note || "configured") : (note || "not configured"),
    });
  });

  box.innerHTML = rows.map((r) => `
    <div class="health-row ${r.ok ? "ok" : "bad"}">
      <span class="health-dot" aria-hidden="true"></span>
      <span class="health-name">${esc(r.label)}</span>
      <span class="health-value">${esc(r.value)}</span>
    </div>`).join("");
}

/* The knowledge console, inside the administrator shell.
 *
 * It is the page served at /kb/, in an iframe, rather than a second
 * implementation of it. Same origin and the same session cookie, so nothing is
 * passed across and nothing extra is trusted; and there is one upload form, one
 * live chain diagram and one query log in the codebase rather than two that
 * would have drifted apart by the second change.
 *
 * `?embed=1` tells the console it is inside this shell, so it drops its own
 * brand bar and sign-out - the shell around it already has both.
 *
 * The frame is created once, on the first visit, and then left alone. Recreating
 * it on every visit would reload the console, losing a half-filled upload form
 * and whatever was on the live chain.
 */
RENDERERS.knowledge = async function renderKnowledge() {
  if (!state.isAdmin) {
    $("#kbFrame").innerHTML = `<div class="kb-frame-loading">`
      + `This page is for administrators.</div>`;
    return;
  }
  const host = $("#kbFrame");
  if (host.querySelector("iframe")) return;

  const frame = document.createElement("iframe");
  frame.src = "/kb/?embed=1";
  frame.title = "Saarthi knowledge console";
  // The console is first-party, same-origin and admin-only. It is still worth
  // naming what it may do, so a future change to that page cannot quietly start
  // doing more from inside the dashboard than it does on its own.
  frame.setAttribute("allow", "clipboard-write");
  frame.addEventListener("load", () => {
    const loading = $("#kbLoading");
    if (loading) loading.remove();
  });
  frame.addEventListener("error", () => {
    const loading = $("#kbLoading");
    if (loading) {
      loading.textContent = "The console did not load. Open it full screen instead.";
    }
  });
  host.appendChild(frame);
};

RENDERERS.admin = async function renderAdmin() {
  if (!state.isAdmin) {
    $("#adminTable").innerHTML = `<p class="hint">This page is for administrators.</p>`;
    return;
  }
  const [people, otp] = await Promise.all([
    API.get("/api/admin/users"),
    API.get("/api/admin/otp-status").catch(() => null),
  ]);


  const active = people.users.filter((u) => u.status === "active").length;
  const pending = people.users.filter((u) => u.status === "pending").length;
  const locked = people.users.filter((u) => u.status === "locked").length;
  $("#adminTiles").innerHTML = [
    ["Accounts", people.count, "all roles"],
    ["Active", active, "verified and able to sign in"],
    ["Awaiting a code", pending, "signed up, not yet verified"],
    ["Locked", locked, "blocked by an administrator"],
  ].map(([label, value, sub]) => `
    <article class="metric">
      <div class="m-top"><span class="m-ico">${icon("user", 15)}</span><b>${esc(label)}</b></div>
      <div class="m-val">${esc(value)}</div>
      <div class="m-sub muted">${esc(sub)}</div>
    </article>`).join("");

  $("#adminTable").innerHTML = people.users.length ? `<table><thead><tr>
      <th>Account</th><th>Phone</th><th>Role</th><th>Status</th>
      <th>Fields</th><th>Acres</th><th>Facts</th><th>Calls</th><th>Last seen</th><th></th>
    </tr></thead><tbody>
    ${people.users.map((u) => `<tr data-admin-row="${esc(
      [u.full_name, u.email, u.phone, u.role, u.status].join(" ").toLowerCase())}">
      <td data-label="Account"><b>${esc(u.full_name || "-")}</b><small class="cell-note">${esc(u.email || "")}</small></td>
      <td data-label="Phone">${esc(u.phone || "-")}</td>
      <td data-label="Role"><span class="badge ${u.role === "admin" ? "" : "warn"}">${esc(u.role)}</span></td>
      <td data-label="Status"><span class="badge ${u.status === "active" ? "" : u.status === "locked" ? "bad" : "warn"}">${esc(u.status)}</span></td>
      <td class="num" data-label="Fields">${esc(u.fields)}</td><td class="num" data-label="Acres">${esc(u.acres)}</td>
      <td class="num" data-label="Facts">${esc(u.remembered_facts)}</td><td class="num" data-label="Calls">${esc(u.calls)}</td>
      <td data-label="Last seen"><span class="cell-when">${esc(localStamp(u.last_login))}</span>
          <small class="cell-note">${esc(sinceLabel(u.last_login))}</small></td>
      <td class="row-actions">
        <button class="btn ghost sm" data-admin-open="${esc(u.user_id)}">Open</button>
        ${u.user_id === state.userId ? `<span class="self-note">This is you</span>` : `
        <button class="btn ${u.status === "locked" ? "ghost" : "danger"} sm"
          data-admin-status="${esc(u.user_id)}"
          data-next="${u.status === "locked" ? "active" : "locked"}">${u.status === "locked" ? "Unlock" : "Lock"}</button>
        <button class="btn danger sm" data-admin-delete="${esc(u.user_id)}"
          data-label="${esc(u.full_name || u.email || u.phone || "this account")}">Delete</button>`}
      </td></tr>`).join("")}</tbody></table>`
    : `<p class="hint">No accounts yet.</p>`;

  /* Totals across every account, so an administrator can see the shape of the
     server without opening each one. */
  const totals = people.users.reduce((acc, u) => ({
    fields: acc.fields + Number(u.fields || 0),
    acres: acc.acres + Number(u.acres || 0),
    facts: acc.facts + Number(u.remembered_facts || 0),
    calls: acc.calls + Number(u.calls || 0),
    mapped: acc.mapped + (Number(u.fields || 0) > 0 ? 1 : 0),
  }), { fields: 0, acres: 0, facts: 0, calls: 0, mapped: 0 });

  const farmStats = $("#adminFarmStats");
  if (farmStats) {
    farmStats.innerHTML = [
      ["Fields mapped", totals.fields],
      ["Land under advice", `${totals.acres.toFixed(1)} acres`],
      ["Accounts with a field", `${totals.mapped} of ${people.users.length}`],
      ["Facts remembered from calls", totals.facts],
      ["Calls placed", totals.calls],
    ].map(([k, v]) =>
      `<div class="status-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
  }

  const ragBox = $("#adminRag");
  if (ragBox) {
    const rag = await API.get("/api/rag/stats").catch(() => null);
    ragBox.innerHTML = rag ? [
      ["Documents", rag.documents ?? rag.docs ?? "not reported"],
      ["Passages indexed", rag.chunks ?? rag.passages ?? "not reported"],
      ["Embedding model", rag.model || rag.embedding_model || "not reported"],
      ["Last indexed", localStamp(rag.indexed_at || rag.built_at)],
    ].map(([k, v]) =>
      `<div class="status-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")
      : `<p class="hint">The knowledge base did not report its state.</p>`;
  }

  renderAdminHealth().catch(() => {});
  paintAdminWeather().catch(() => {});

  $("#adminOtp").innerHTML = otp ? [
    ["Delivery configured", otp.configured ? "yes" : "no"],
    ["Endpoint", otp.endpoint || "not set"],
    ["Shared secret", otp.secret_set ? "set" : "missing"],
    ["Note", otp.note || "Codes go out by email and SMS through n8n."],
  ].map(([k, v]) => `<div class="status-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")
    : `<p class="hint">Could not read the delivery status.</p>`;

  renderBackdrops().catch((e) => { $("#bgNote").textContent = e.message; });
  renderAdminCalls().catch(() => {});
};

/* ------------------------------------------- the glass backdrop, for everyone */
/* An administrator picks the picture; every account that chooses the glass theme
   sees it. The list on screen is the DRAFT - uploading stores a file and adding
   a link stages it, and neither reaches anybody until Save. That split is on
   purpose: this changes what every farmer on the box is looking at, and an
   upload that took effect the moment it finished would give nobody a chance to
   look at it first. */
const backdrop = { images: [], every: 45, stored: [] };

async function renderBackdrops() {
  const [live, stored] = await Promise.all([
    API.get("/api/appearance"),
    API.get("/api/appearance/stored").catch(() => ({ stored: [], shipped: [] })),
  ]);
  backdrop.images = (live.images || []).slice();
  backdrop.every = Number(live.interval_seconds) || 0;
  // Everything choosable: what ships with the dashboard, what has been
  // uploaded, and whatever links are already in use.
  const choices = [];
  const add = (url) => { if (url && !choices.includes(url)) choices.push(url); };
  (stored.shipped || []).forEach(add);
  (stored.stored || []).forEach((x) => add(x.image));
  backdrop.images.forEach(add);
  backdrop.stored = choices;
  paintBackdrops();
}

function paintBackdrops() {
  const grid = $("#bgGrid");
  if (!grid) return;
  grid.innerHTML = backdrop.stored.length ? backdrop.stored.map((url) => {
    const at = backdrop.images.indexOf(url);
    return `<button class="bg-tile${at >= 0 ? " on" : ""}" type="button"
      data-bg="${esc(url)}" aria-pressed="${at >= 0}">
      <img src="${esc(url)}" alt="" loading="lazy">
      ${at >= 0 ? `<span class="bg-order">${at + 1}</span>` : ""}
    </button>`;
  }).join("") : `<p class="hint">No pictures yet. Upload one, or paste a link.</p>`;

  const slider = $("#bgInterval");
  if (slider) {
    slider.value = String(Math.min(300, backdrop.every));
    $("#bgIntervalLabel").textContent = backdrop.every
      ? `${backdrop.every} seconds`
      : "never - one picture stays up";
  }
  const note = $("#bgNote");
  if (note && !note.dataset.sticky) {
    note.textContent = backdrop.images.length > 1 && !backdrop.every
      ? `${backdrop.images.length} chosen, but the slideshow is off, so only the first is used.`
      : `${backdrop.images.length} picture(s) chosen.`;
  }
}

function wireBackdrops() {
  const grid = $("#bgGrid");
  if (!grid) return;
  grid.addEventListener("click", (e) => {
    const tile = e.target.closest("[data-bg]");
    if (!tile) return;
    const url = tile.dataset.bg;
    const at = backdrop.images.indexOf(url);
    // Clicking adds it to the end of the order, or takes it out. The number on
    // the tile is the position, because with a slideshow the order matters.
    if (at >= 0) backdrop.images.splice(at, 1); else backdrop.images.push(url);
    paintBackdrops();
  });
  $("#bgInterval").addEventListener("input", (e) => {
    backdrop.every = Number(e.target.value) || 0;
    $("#bgIntervalLabel").textContent = backdrop.every
      ? `${backdrop.every} seconds` : "never - one picture stays up";
  });
  $("#bgRefresh").addEventListener("click", () => {
    $("#bgNote").dataset.sticky = "";
    renderBackdrops().catch((e) => { $("#bgNote").textContent = e.message; });
  });
  $("#bgUpload").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const note = $("#bgNote");
    note.dataset.sticky = "1";
    note.textContent = "Uploading…";
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/appearance/upload",
                              { method: "POST", body: form, credentials: "same-origin" });
      if (!res.ok) throw new Error((await res.text()).slice(0, 180));
      const body = await res.json();
      backdrop.stored.unshift(body.image);
      backdrop.images.push(body.image);
      note.textContent = "Uploaded. Press Save to put it on everyone's dashboard.";
      paintBackdrops();
    } catch (err) {
      note.textContent = err.message;
    } finally {
      e.target.value = "";
    }
  });
  $("#bgAddLink").addEventListener("click", () => {
    const box = $("#bgLink");
    const url = (box.value || "").trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) {
      $("#bgNote").dataset.sticky = "1";
      $("#bgNote").textContent = "A link has to start with http:// or https://";
      return;
    }
    if (!backdrop.stored.includes(url)) backdrop.stored.unshift(url);
    if (!backdrop.images.includes(url)) backdrop.images.push(url);
    box.value = "";
    $("#bgNote").dataset.sticky = "";
    paintBackdrops();
  });
  $("#bgSave").addEventListener("click", async () => {
    const note = $("#bgNote");
    note.dataset.sticky = "1";
    try {
      const body = await API.put("/api/appearance", {
        images: backdrop.images,
        interval_seconds: backdrop.every,
      });
      backdrop.images = body.images || [];
      backdrop.every = Number(body.interval_seconds) || 0;
      note.textContent = "Saved. Every account sees this behind the glass theme.";
      // Including this one, right now, if it is on the glass theme.
      glass.loaded = false;
      if (document.documentElement.getAttribute("data-theme") === "glass") {
        glassBackdrop(true);
      }
      paintBackdrops();
    } catch (e) {
      note.textContent = e.message;
    }
  });
}

async function openAdminUser(userId) {
  const data = await API.get("/api/admin/users/" + userId);
  const p = (data.profile && data.profile.profile) || {};
  const fields = (data.profile && data.profile.fields) || [];
  const facts = (data.profile && data.profile.remembered_facts) || [];
  $("#adminDetailName").textContent =
    `${data.user.full_name || data.user.email || "Account"} (${data.user.role})`;
  $("#adminDetailCard").hidden = false;
  $("#adminDetail").innerHTML = `
    <div class="cols">
      <div>
        <h3 class="mini-title">Profile</h3>
        <div class="status-list">
          ${["full_name", "email", "phone", "village", "district", "state", "pincode",
             "land_acres", "soil_type", "irrigation"].map((k) =>
            `<div class="status-row"><span>${esc(k.replace(/_/g, " "))}</span>
               <b>${esc(p[k] === null || p[k] === undefined ? "-" : p[k])}</b></div>`).join("")}
        </div>
      </div>
      <div>
        <h3 class="mini-title">Fields (${fields.length})</h3>
        ${fields.length ? fields.map((f) => `<div class="status-row">
            <span>${esc(f.name)}</span><b>${esc(f.crop || "-")} - ${esc(f.area_acres || "?")} acres</b>
          </div>`).join("") : `<p class="hint">No fields mapped.</p>`}
        <h3 class="mini-title">What Saarthi remembers (${facts.length})</h3>
        ${facts.length ? facts.map((f) => `<div class="status-row">
            <span>${esc(String(f.key).replace(/_/g, " "))}</span><b>${esc(f.value)}</b>
          </div>`).join("") : `<p class="hint">Nothing learned from calls yet.</p>`}
      </div>
    </div>
    <h3 class="mini-title">Recent calls (${(data.calls || []).length})</h3>
    ${(data.calls || []).length ? `<div class="table"><table><thead><tr>
        <th>Started</th><th>Turns</th><th>Summary</th></tr></thead><tbody>
      ${data.calls.map((c) => `<tr>
        <td>${esc(String(c.started_at || "").replace("T", " ").slice(0, 16))}</td>
        <td class="num">${esc(c.turns)}</td>
        <td>${esc(c.summary || "")}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="hint">No calls.</p>`}`;
  $("#adminDetailCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function initAdmin() {
  $("#adminRefresh").addEventListener("click", () => RENDERERS.admin());
  const health = $("#adminHealthRefresh");
  if (health) health.addEventListener("click", () => renderAdminHealth().catch(() => {}));
  wireBackdrops();
  const callSearch = $("#adminCallSearch");
  if (callSearch) {
    callSearch.addEventListener("input", (e) => {
      adminCallFilter = e.target.value;
      renderAdminCalls().catch(() => {});
    });
  }
  const callRefresh = $("#adminCallRefresh");
  if (callRefresh) {
    callRefresh.addEventListener("click", () => renderAdminCalls().catch(() => {}));
  }

  /* Filters the rows already on screen rather than asking the server again:
     the list is small, and a round trip per keystroke would make typing lag. */
  const search = $("#adminSearch");
  if (search) {
    search.addEventListener("input", () => {
      const needle = search.value.trim().toLowerCase();
      $$("#adminTable tr[data-admin-row]").forEach((row) => {
        row.hidden = Boolean(needle) && !row.dataset.adminRow.includes(needle);
      });
    });
  }
  $("#adminCloseDetail").addEventListener("click", () => { $("#adminDetailCard").hidden = true; });
  $("#adminTable").addEventListener("click", async (e) => {
    const open = e.target.closest("[data-admin-open]");
    if (open) { openAdminUser(open.dataset.adminOpen).catch((x) => toast(x.message)); return; }

    const status = e.target.closest("[data-admin-status]");
    if (status) {
      const next = status.dataset.next;
      if (next === "locked" &&
          !window.confirm("Lock this account? They are signed out immediately.")) return;
      try {
        await API.post(`/api/admin/users/${status.dataset.adminStatus}/status`,
                       { status: next });
        toast("Account " + next);
        RENDERERS.admin();
      } catch (x) { toast(x.message); }
    }
  });
}

/* ----------------------------------------------------------------- auth */
/* The dashboard is for one signed-in farmer. Nothing is fetched until we know who
   that is: the API would refuse anyway, and a page full of 401s looks like a broken
   release rather than a locked door. */
async function requireSignIn() {
  let me = null;
  try {
    me = await API.get("/api/auth/me");
  } catch {
    me = null;                                  // offline: fall through to the site
  }
  if (!me || !me.signed_in) {
    location.href = "/sign-in.html";
    return null;
  }
  state.me = me.user;
  state.isAdmin = Boolean(me.is_admin);
  /* Locking or deleting your own account locks everyone out of this page,
     and there is no second administrator to undo it. */
  state.userId = me.user_id || (me.user && me.user.user_id) || "";
  // The account's phone is the identity every profile and field call is scoped to.
  if (me.user && me.user.phone) setOwnerPhone(me.user.phone);
  return me;
}

async function signOut() {
  try { await API.post("/api/auth/logout", {}); } catch { /* going anyway */ }
  try { localStorage.removeItem("saarthi.phone"); } catch { /* private window */ }
  location.href = "/sign-in.html";
}


/* Who this is, painted once at boot.

   These were only ever set by the Overview and Profile renderers, so after signing in
   the header, the sidebar chip and the greeting showed the demo farmer until you
   happened to open one of those two pages. Identity should not depend on which page
   you land on. */
async function paintIdentity() {
  const body = await API.get("/api/farm").catch(() => null);
  const farm = (body && body.farm) || {};
  const name = farm.farmer_name || "Farmer";
  const village = farm.village || "";
  const setText = (sel, value) => { const el = $(sel); if (el) el.textContent = value; };
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join("") || "S";
  setText("#userAvatar", initials);
  setText("#profileBtn", initials);
  setText("#userName", name);
  setText("#userRole", village ? `Farmer - ${village}` : "Farmer");
  const hour = new Date().getHours();
  setText("#greeting", `${hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening"}, ${name}!`);
  if (farm.phone) localStorage.setItem("saarthi.phone", farm.phone);
}

async function boot() {
  /* Left to right, whatever the language. There is no mirrored stylesheet for
     this dashboard, so flipping the document for Urdu moves the sidebar to the
     other side of a layout built around it having been on the left. Urdu text
     still reads right to left inside its own runs, which is the part that
     matters to the reader. */
  document.documentElement.dir = "ltr";
  paintIcons();
  initTheme();

  // Before anything else: who is this?
  const me = await requireSignIn();
  if (!me) return;                              // redirecting to the sign-in page
  applyAdminShell();
  await paintIdentity();
  await paintFieldPickers();
  await loadAskSpecs();
  renderNotifications();
  checkForChanges().catch(() => {});
  /* The saved language, applied before the farmer sees the page. */
  if (state.language && state.language !== "en") {
    applyLanguage(state.language).catch(() => {});
  }
  $("#signOutBtn").addEventListener("click", signOut);
  initAdmin();

  // navigation
  $("#nav").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-page]");
    if (b) go(b.dataset.page);
  });
  document.addEventListener("click", (e) => {
    const goEl = e.target.closest("[data-go]");
    if (goEl) { go(goEl.dataset.go); return; }
    const play = e.target.closest("[data-play]");
    if (play) { playAudio(play.dataset.play); return; }
    const rep = e.target.closest("[data-report]");
    if (rep && !rep.disabled) {
      downloadReport(rep.dataset.report, rep.dataset.format, rep.dataset.query || "");
    }
  });
  $("#menuBtn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
  $("#bellBtn").addEventListener("click", () => {
    const panel = $("#notifPanel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) markNotificationsSeen();
  });
  $("#notifClear").addEventListener("click", () => {
    localStorage.removeItem(NOTIF_KEY);
    renderNotifications();
  });
  document.addEventListener("click", (e) => {
    const panel = $("#notifPanel");
    if (!panel || panel.hidden) return;
    if (!e.target.closest("#notifPanel") && !e.target.closest("#bellBtn")) {
      panel.hidden = true;
    }
  });

  // languages + capabilities
  try {
    const [langs, keys] = await Promise.all([API.get("/api/languages"), API.get("/api/keys/status")]);
    state.languages = langs.languages || [];
    state.capabilities = keys.capabilities || [];
    state.speech = keys.speech || { configured: false };
    state.telephony = keys.telephony || { configured: false };
    fillLanguageSelects();
    await setLanguage(state.language);
    const ragNote = keys.rag && keys.rag.provider === "offline"
      ? "RAG is using the offline embedder (no Mistral key)." : "";
    const llm = state.capabilities.find((c) => c.id === "llm");
    $("#chatHint").textContent = [llm && !llm.configured ? llm.without_it : "", ragNote]
      .filter(Boolean).join(" ");
    $("#chatMode").textContent = `Conversational AI + RAG + 15 agents - ${llm && llm.configured ? "Mistral connected" : "rule engine (no Mistral key)"}`;
    $("#fabMode").textContent = llm && llm.configured ? "Mistral + RAG + agents" : "rule engine + RAG";
    applySpeechAvailability();
  } catch (e) { toast("Backend not reachable: " + e.message); }

  ["#langSidebar", "#fabLang"].forEach((sel) =>
    $(sel).addEventListener("change", (e) => setLanguage(e.target.value, true)));

  // assistant
  $("#chatForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#chatInput"), text = input.value;
    input.value = "";
    sendMessage(text, {
      container: $("#messages"), ragEl: $("#useRag"), agentsEl: $("#runAgents"),
      speakEl: $("#speakReply"), showTrace: true, convEl: $("#convMode"),
    });
  });
  $("#convMode").addEventListener("change", (e) => {
    const on = e.target.checked;
    $("#useRag").disabled = on;
    $("#runAgents").disabled = on;
    $("#chatMode").textContent = on
      ? "Conversation mode - remembers you, replies in about a second, agents run behind it"
      : "Conversational AI + RAG + 15 agents";
  });
  $("#micBtn").addEventListener("click", () => toggleMic($("#micBtn"), $("#chatInput")));
  try {
    const qp = await API.get("/api/chat/quick-prompts");
    $("#quickPrompts").innerHTML = (qp.prompts || []).map((p) =>
      `<button class="quick" data-prompt="${esc(p.text)}">${esc(p.label)}</button>`).join("");
  } catch { /* non-fatal */ }
  $("#quickPrompts").addEventListener("click", (e) => {
    const b = e.target.closest("[data-prompt]");
    if (!b) return;
    sendMessage(b.dataset.prompt, {
      container: $("#messages"), ragEl: $("#useRag"), agentsEl: $("#runAgents"),
      speakEl: $("#speakReply"), showTrace: true, convEl: $("#convMode"),
    });
  });
  bubble($("#messages"), "bot",
    "Hello! I am Saarthi. Ask me about soil, irrigation, fertiliser, weather, pests, weeds, seed, market prices or crop residue - in your own language.");

  // floating chatbot
  $("#fabBtn").addEventListener("click", () => {
    const box = $("#chatbot");
    box.hidden = !box.hidden;
    if (!box.hidden && !$("#fabMessages").children.length) {
      bubble($("#fabMessages"), "bot", "Ask me anything about your farm. I use RAG and the 15 agents.");
    }
  });
  $("#fabClose").addEventListener("click", () => { $("#chatbot").hidden = true; });
  $("#fabForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#fabInput"), text = input.value;
    input.value = "";
    sendMessage(text, {
      container: $("#fabMessages"), ragEl: $("#fabRag"), agentsEl: $("#fabAgents"),
      speakEl: $("#fabSpeak"), showTrace: false,
    });
  });
  $("#fabMic").addEventListener("click", () => toggleMic($("#fabMic"), $("#fabInput")));

  // page-specific controls
  $$(".run-pipe").forEach((b) => b.addEventListener("click", () => runPipeline()));
  $("#stopPipeline").addEventListener("click", stopPipeline);
  $("#heroFetch").addEventListener("click", heroFetch);
  $("#reportField").addEventListener("change", (e) => {
    reportField = e.target.value;
    RENDERERS.reports();
  });
  $("#reportsClear").addEventListener("click", async () => {
    /* Irreversible, so it says what goes and what stays before asking. */
    const ok = window.confirm(
      "Delete every stored advisory, call log and chat session?\n\n"
      + "This cannot be undone. Your sensor readings, fields and profile are NOT "
      + "touched.");
    if (!ok) return;
    try {
      const res = await API.del("/api/history?what=all");
      const gone = Object.entries(res.deleted || {})
        .map(([k, v]) => `${v} ${k}`).join(", ");
      toast("Deleted " + (gone || "nothing"));
      await RENDERERS.reports();
    } catch (e) { toast(e.message); }
  });
  document.addEventListener("click", (e) => {
    const loc = e.target.closest("[data-map-locate]");
    if (loc) { mapLocate(loc.dataset.mapLocate); return; }
    const home = e.target.closest("[data-map-home]");
    if (home) mapHome(home.dataset.mapHome);
  });
  $("#heroField").addEventListener("change", async (e) => {
    setActiveParcel(e.target.value);
    /* The crop belongs to the field, so a remembered one from the previous plot
       would be carried onto this one and quietly answered for it. */
    delete pipelineAnswers.crop;
    delete pipelineAnswers.sowing_date;
    const other = $("#sensorField");
    if (other) other.value = e.target.value;
    await RENDERERS.overview();
    toast("Showing " + (e.target.selectedOptions[0] || {}).textContent);
  });
  $("#weatherWhere").addEventListener("change", () => RENDERERS.weather());
  $("#schemeFilters").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-cat]");
    if (!chip) return;
    state.schemeFilter = chip.dataset.cat;
    RENDERERS.schemes();
  });
  $("#finRun").addEventListener("click", () => runFinance().catch((err) => toast(err.message)));
  $("#ledAdd").addEventListener("click", addLedgerEntry);
  $("#ledgerField").addEventListener("change", () => renderLedger().catch(() => {}));
  $("#ledgerSeason").addEventListener("change", () => renderLedger().catch(() => {}));
  $("#ledgerTable").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-del]");
    if (!btn) return;
    if (!window.confirm("Remove this entry? It cannot be undone.")) return;
    try {
      await API.del("/api/ledger/" + encodeURIComponent(btn.dataset.del));
      await renderLedger();
    } catch (err) { toast(err.message); }
  });
  $("#flowShowResult").addEventListener("click", () => go("pipeline"));
  $("#agentMatrix").addEventListener("click", (e) => {
    const cell = e.target.closest("[data-agent]");
    if (!cell) return;
    if (["needs_input", "unavailable"].includes(cell.dataset.status)
        && openAskModal(cell.dataset.agent)) return;
    showAgentDetail(cell.dataset.agent);
  });
  $("#chartConfidence").addEventListener("click", (e) => {
    const bar = e.target.closest("[data-agent]");
    if (bar) showAgentDetail(bar.dataset.agent);
  });
  $("#pipelineQuestions").addEventListener("click", (e) => {
    const chip = e.target.closest("[data-ask]");
    if (!chip) return;
    go("assistant");
    $("#chatInput").value = chip.dataset.ask;
    $("#chatInput").focus();
  });
  $("#toggleAnswer").addEventListener("click", () => {
    const p = $("#pipelineAnswer");
    p.classList.toggle("clamped");
    $("#toggleAnswer").textContent = p.classList.contains("clamped")
      ? "Show full text" : "Show less";
  });
  ["#waveBoard", "#agentDomains"].forEach((sel) => {
    const node = $(sel);
    if (node) node.addEventListener("click", (e) => {
      const hit = e.target.closest("[data-agent]");
      if (hit) showAgentDetail(hit.dataset.agent);
    });
  });
  $("#findingList").addEventListener("click", (e) => {
    const row = e.target.closest("[data-agent]");
    if (!row) return;
    if (row.classList.contains("stalled") && openAskModal(row.dataset.agent)) return;
    showAgentDetail(row.dataset.agent);
  });
  ["#planList", "#scoreCard"].forEach((sel) => {
    const node = $(sel);
    if (node) node.addEventListener("click", (e) => {
      const hit = e.target.closest("[data-agent]");
      if (hit) showAgentDetail(hit.dataset.agent);
    });
  });
  $("#agentFlow").addEventListener("click", (e) => {
    const step = e.target.closest("[data-agent]");
    if (!step) return;
    const stalled = step.classList.contains("needs_input")
      || step.classList.contains("unavailable");
    /* Some agents are waiting on a reading rather than on an answer - agent 1 wants
       the sensor, not a typed figure - so a click that has no form behind it must
       still go somewhere useful instead of doing nothing at all. */
    if (stalled && openAskModal(step.dataset.agent)) return;
    if (Number(step.dataset.agent) === 1 && stalled) { go("devices"); return; }
    showAgentDetail(step.dataset.agent);
  });
  wireAskModal();
  watchForNewText();
  /* Drawn once at boot so the chain is on the page before anything is run. */
  drawFlow({ stage: "idle", done: [], running: [] });
  wirePlaceSearch();
  $("#refreshSoil").addEventListener("click", () => RENDERERS.soil());
  $("#trendParam").addEventListener("change", (e) => drawSoilTrend(e.target.value));
  $("#marketCrop").addEventListener("change", () => {
    $("#marketDistrict").value = "";          // a district belongs to one crop's records
    RENDERERS.market();
  });
  $("#marketDistrict").addEventListener("change", () => RENDERERS.market());
  $("#marketState").addEventListener("change", () => RENDERERS.market());
  $("#callBtn").addEventListener("click", requestCall);
  $("#callCancel").addEventListener("click", cancelCall);
  $("#taskBook").addEventListener("click", bookTask);
  $("#taskSaid").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); bookTask(); }
  });
  // The examples ARE the documentation for what the parser understands, so tapping
  // one fills the box rather than booking blind.
  $$("#taskExamples .chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      $("#taskSaid").value = chip.textContent.trim();
      $("#taskSaid").focus();
    }));

  // profile + fields
  $("#pfSave").addEventListener("click", saveProfile);
  $("#pfEdit").addEventListener("click", () => {
    // Unlock only. The code is asked for when they are ready to save.
    lockProfile(false);
    $("#pfSaveNote").textContent = "Make your changes, then send a code to save them.";
    const first = $("#page-profile input:not([disabled]):not(#pfCode)");
    if (first) first.focus();
  });
  $("#pfSendCode").addEventListener("click", askProfileCode);
  // Save turns on once six digits are in. A Save button that is pressable and
  // then refuses is a trip the farmer makes for nothing.
  $("#pfCode").addEventListener("input", (e) => {
    $("#pfSave").disabled = (e.target.value || "").trim().length < 4;
  });
  $("#pfCancel").addEventListener("click", async () => {
    lockProfile(true);
    $("#pfSaveNote").textContent = "Nothing was changed.";
    await RENDERERS.profile();
  });
  $("#pfResend").addEventListener("click", askProfileCode);
  if (window.SaarthiVoiceEnrol) window.SaarthiVoiceEnrol.wire();
  $("#pfErase").addEventListener("click", eraseMemory);
  $("#pfRefreshMemory").addEventListener("click", () => RENDERERS.profile());
  // One way into the profile: the avatar in the top-right corner.
  $("#profileBtn").addEventListener("click", () => go("profile"));
  initFields();

  // manual sensor values
  initPests();
  wireBle();

  go((location.hash || "#overview").slice(1));
}

window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1);
  if (page && page !== currentPage) go(page);
});
/* The deliberate surface other scripts on this page may use.
 *
 * Everything in this file is inside a closure, which is right - the dashboard
 * has no business exporting four thousand lines of internals. But wake.js needs
 * three things to do its job: a way to navigate, a way to call the API with the
 * session cookie, and a way to say something to the farmer. Those three are
 * named here rather than the closure being opened up, so the next script to
 * need something has to add to this list on purpose.
 */
window.Saarthi = {
  /* So agent mode can follow the language the server answered in. It has no
     access to the picker otherwise, which is why a spoken switch used to last
     exactly one turn. */
  followLanguage: followReplyLanguage,
  go,
  api: API,
  toast,
  get language() { return state.language; },
  get page() { return currentPage; },
  /* The conversation's own id. Agent mode reads it so a follow-up - "aur gehu
     ka?" - lands in the same session as the question before it, and writes it
     back so the next page load continues where the talking left off. */
  get sessionId() { return state.sessionId; },
  set sessionId(id) {
    if (!id) return;
    state.sessionId = id;
    try { localStorage.setItem("saarthi.session", id); } catch (e) { /* private */ }
  },
};

boot();
})();
