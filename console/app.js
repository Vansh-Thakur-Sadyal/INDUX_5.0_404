/* Saarthi Knowledge Console
 *
 * One page, five tabs, no framework. The whole thing is a thin client over
 * /api/kb - every decision about what is allowed, what state a document is in and
 * what the chain did lives on the server, and this file only draws it.
 *
 * Two things here are worth knowing before reading:
 *
 *   - Nothing is cached across a refresh. A knowledge console that shows a stale
 *     document list is worse than one that is slow, because the whole point of it
 *     is to tell you what the bots can currently see.
 *   - The live chain reads a server-sent event stream, not a poll. Stages arrive
 *     as they happen; the answer arrives a word at a time.
 */
'use strict';

const $  = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* Embedded in the Saarthi dashboard's administrator shell, rather than standing
   on its own at /kb/. The page is identical either way; what changes is that the
   shell around it already carries a brand, a sign-out and a way back, so the
   console drops its own rather than showing two of each. */
const EMBEDDED = new URLSearchParams(location.search).has("embed")
  || (() => { try { return window.self !== window.top; } catch (e) { return true; } })();

const state = {
  user: null,
  capabilities: null,
  documents: [],
  jobsTimer: null,
  identifier: '',
};

/* ------------------------------------------------------------------- fetch */
async function api(path, options) {
  const opts = Object.assign({ credentials: 'include', headers: {} }, options || {});
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch('/api' + path, opts);
  const type = response.headers.get('content-type') || '';
  const body = type.includes('json') ? await response.json().catch(() => ({}))
                                     : await response.text();
  if (!response.ok) {
    const detail = (body && body.detail) || body || ('HTTP ' + response.status);
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return body;
}

function toast(message, kind) {
  const node = el('div', 'toast' + (kind ? ' ' + kind : ''), message);
  $('toasts').appendChild(node);
  setTimeout(() => node.remove(), kind === 'bad' ? 7000 : 4200);
}

/* Dates are stored UTC and read by a person in their own day. "3 min ago" is what
   someone watching an ingestion actually wants; the clock time is for the log. */
function ago(iso) {
  if (!iso) return '';
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
  if (isNaN(then)) return '';
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 50) return 'just now';
  if (seconds < 3600) return Math.round(seconds / 60) + ' min ago';
  if (seconds < 86400) return Math.round(seconds / 3600) + ' h ago';
  if (seconds < 7 * 86400) return Math.round(seconds / 86400) + ' d ago';
  return then.toLocaleDateString();
}

function bytes(n) {
  if (!n) return '-';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

/* ====================================================================== gate */
async function loadCaptcha() {
  try {
    const challenge = await api('/auth/captcha');
    $('gateCaptchaQ').textContent = challenge.question;
    $('gateForm').dataset.challenge = challenge.challenge_id;
  } catch (err) {
    $('gateCaptchaQ').textContent = 'The question could not be loaded. Reload the page.';
  }
}

function gateError(message) {
  const node = $('gateError');
  node.textContent = message;
  node.hidden = !message;
}

async function submitGate(event) {
  event.preventDefault();
  gateError('');
  const button = $('gateSubmit');
  button.disabled = true;
  try {
    if ($('gateStep2').hidden) {
      const reply = await api('/auth/login', { method: 'POST', body: {
        identifier: $('gateUser').value.trim(),
        password: $('gatePass').value,
        challenge_id: $('gateForm').dataset.challenge,
        captcha: $('gateCaptcha').value.trim(),
      }});
      state.identifier = reply.identifier || $('gateUser').value.trim();
      $('gateStep1').hidden = true;
      $('gateStep2').hidden = false;
      $('gateSentTo').textContent = reply.message
        || 'Enter the code we just sent you.';
      $('gateSubmit').textContent = 'Sign in';
      $('gateCode').focus();
    } else {
      await api('/auth/login-verify', { method: 'POST', body: {
        identifier: state.identifier, code: $('gateCode').value.trim(),
      }});
      await start();
      return;
    }
  } catch (err) {
    gateError(err.message);
    if (err.status === 428 || $('gateStep2').hidden) loadCaptcha();
    $('gateCaptcha').value = '';
  } finally {
    button.disabled = false;
  }
}

/* ===================================================================== boot */
async function start() {
  let me = null;
  try {
    me = await api('/auth/me');
  } catch (err) {
    me = { signed_in: false };
  }

  if (!me.signed_in) return showGate();
  if (!me.is_admin) {
    showGate('This console is for administrator accounts. ' +
             (me.user ? me.user.email || '' : '') + ' is a farmer account.');
    return;
  }

  state.user = me.user;
  $('gate').hidden = true;
  $('shell').hidden = false;
  document.body.dataset.embed = EMBEDDED ? '1' : '';
  $('whoName').textContent = me.user.full_name || me.user.email || 'Administrator';

  state.capabilities = await api('/kb/capabilities');
  paintCapabilities();
  await Promise.all([refreshStats(), refreshLibrary(), refreshJobs(), refreshActivity()]);
  paintStageNotes();
  startJobPolling();
}

function showGate(message) {
  // Inside the dashboard the visitor is already signed in, so reaching the gate
  // means the session went away under them - a timeout, or a sign-out in another
  // tab. A second sign-in form inside a frame is a confusing place to recover
  // from that, so the whole window goes to the real one.
  if (EMBEDDED) {
    try {
      if (window.top && window.top !== window.self) {
        window.top.location.href = '/app/';
        return;
      }
    } catch (e) { /* cross-origin: fall through to the form */ }
  }
  $('shell').hidden = true;
  $('gate').hidden = false;
  if (message) gateError(message);
  loadCaptcha();
}

function paintCapabilities() {
  const caps = state.capabilities;
  ['upAudience', 'txAudience', 'urlAudience', 'qAudience'].forEach((id) => {
    const select = $(id);
    select.innerHTML = '';
    caps.audiences.forEach((a) => {
      const option = el('option', null, a.label);
      option.value = a.id;
      option.title = a.note;
      select.appendChild(option);
    });
  });
  const strategy = $('qStrategy');
  strategy.innerHTML = '';
  caps.rewrite_strategies.forEach((s) => {
    const option = el('option', null, s.label);
    option.value = s.id;
    option.title = s.note;
    strategy.appendChild(option);
  });

  const formats = $('formats');
  formats.innerHTML = '';
  caps.formats.forEach((f) => {
    const row = el('div');
    row.appendChild(el('b', null, f.kinds + ': '));
    row.appendChild(document.createTextNode(f.extensions + ' - ' + f.note));
    formats.appendChild(row);
  });
  $('dropHint').textContent = 'Up to ' + Math.round(caps.max_bytes / 1048576)
    + ' MB each. Several at once is fine.';
}

function paintStageNotes() {
  const host = $('healthStages');
  host.innerHTML = '';
  const add = (heading, stages) => {
    const title = el('div');
    title.appendChild(el('b', null, heading));
    title.appendChild(el('span', null, ''));
    host.appendChild(title);
    stages.forEach((s) => {
      const row = el('div');
      row.appendChild(el('b', null, s.label));
      row.appendChild(el('span', null, s.note));
      host.appendChild(row);
    });
  };
  add('Indexing a document', state.capabilities.ingest_stages);
  add('Answering a question', state.capabilities.query_stages);
}

/* ================================================================= feed tab */
function tile(value, label, note) {
  const node = el('div', 'tile');
  node.appendChild(el('b', null, String(value)));
  node.appendChild(el('span', null, label));
  if (note) node.appendChild(el('small', null, note));
  return node;
}

async function refreshStats() {
  let stats;
  try {
    stats = await api('/kb/stats');
  } catch (err) {
    toast('Could not read the knowledge base: ' + err.message, 'bad');
    return;
  }
  const docs = stats.documents;
  const index = stats.index;
  const retrieval = stats.retrieval;

  const feed = $('feedTiles');
  feed.innerHTML = '';
  feed.appendChild(tile(docs.documents || 0, 'Documents'));
  feed.appendChild(tile(index.chunks || 0, 'Searchable passages'));
  feed.appendChild(tile(stats.embeddings.provider, 'Embeddings',
    stats.embeddings.dimensions + ' dimensions'));
  feed.appendChild(tile((docs.by_state.failed || {}).documents || 0, 'Failed',
    'Open the Library to see why'));

  const health = $('healthTiles');
  health.innerHTML = '';
  health.appendChild(tile(index.documents || 0, 'Indexed documents'));
  health.appendChild(tile(index.chunks || 0, 'Passages'));
  health.appendChild(tile(retrieval.queries || 0, 'Questions asked'));
  health.appendChild(tile(
    retrieval.found_nothing_percent === undefined ? '-' : retrieval.found_nothing_percent + '%',
    'Found nothing', 'Each one is a gap worth filling'));
  health.appendChild(tile(retrieval.median_ms === undefined ? '-' : retrieval.median_ms + ' ms',
    'Median retrieval', retrieval.p95_ms !== undefined ? 'p95 ' + retrieval.p95_ms + ' ms' : ''));

  const list = $('healthIndex');
  list.innerHTML = '';
  const rows = [
    ['Embedding provider in use', stats.embeddings.provider],
    ['Provider that built the index', stats.embeddings.index_provider || '-'],
    ['Vector dimensions', index.dims || stats.embeddings.dimensions],
    ['Index file', index.index_file || '-'],
    ['Agreement', stats.embeddings.note],
  ];
  rows.forEach(([k, v]) => {
    const row = el('div');
    row.appendChild(el('span', null, k));
    row.appendChild(el('span', null, String(v)));
    list.appendChild(row);
  });

  const models = $('healthModels');
  if (models) {
    models.innerHTML = '';
    (stats.models || []).forEach((row) => {
      const line = el('div');
      line.appendChild(el('b', null, row.job));
      const right = el('span');
      right.appendChild(el('b', null, row.provider + '  ' + row.model));
      right.appendChild(document.createElement('br'));
      right.appendChild(document.createTextNode(row.why));
      line.appendChild(right);
      models.appendChild(line);
    });
  }

  const owners = $('healthOwners');
  owners.innerHTML = '';
  (docs.by_contributor || []).forEach((owner) => {
    const row = el('div');
    row.appendChild(el('span', null, owner.name));
    row.appendChild(el('span', null, owner.documents + ' document(s)'));
    owners.appendChild(row);
  });
  if (!(docs.by_contributor || []).length) {
    owners.appendChild(el('div', 'empty', 'Nobody has uploaded anything yet.'));
  }
}

/* ---------------------------------------------------------------- uploading */
function uploadMeta(prefix) {
  return {
    audience: $(prefix + 'Audience').value,
    topics: $(prefix + 'Topics').value.trim(),
    crops: prefix === 'up' ? $('upCrops').value.trim() : '',
    notes: prefix === 'up' ? $('upNotes').value.trim() : '',
  };
}

async function uploadFiles(files) {
  const meta = uploadMeta('up');
  for (const file of files) {
    const form = new FormData();
    form.append('file', file);
    form.append('title', file.name);
    form.append('audience', meta.audience);
    form.append('topics', meta.topics);
    form.append('crops', meta.crops);
    form.append('notes', meta.notes);
    try {
      const reply = await api('/kb/documents/upload', { method: 'POST', body: form });
      if (reply.ok) {
        toast(file.name + ' accepted - indexing now.');
      } else {
        toast(reply.detail || 'Already in the knowledge base.', 'warn');
      }
    } catch (err) {
      toast(file.name + ': ' + err.message, 'bad');
    }
  }
  await Promise.all([refreshJobs(), refreshLibrary(), refreshStats(), refreshActivity()]);
}

function wireUpload() {
  const drop = $('drop');
  const input = $('file');
  drop.addEventListener('click', () => input.click());
  drop.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  input.addEventListener('change', () => {
    if (input.files.length) uploadFiles(Array.from(input.files));
    input.value = '';
  });
  ['dragenter', 'dragover'].forEach((name) => {
    drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.add('over'); });
  });
  ['dragleave', 'drop'].forEach((name) => {
    drop.addEventListener(name, (e) => { e.preventDefault(); drop.classList.remove('over'); });
  });
  drop.addEventListener('drop', (e) => {
    const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    if (files.length) uploadFiles(files);
  });

  $('txAdd').addEventListener('click', async () => {
    const text = $('txBody').value.trim();
    if (text.length < 40) {
      return toast('Write at least a short paragraph - under 40 characters there is '
                   + 'nothing to retrieve.', 'warn');
    }
    try {
      await api('/kb/documents/text', { method: 'POST', body: Object.assign(
        { title: $('txTitle').value.trim(), text }, uploadMeta('tx')) });
      $('txBody').value = ''; $('txTitle').value = '';
      toast('Note added - indexing now.');
      await Promise.all([refreshJobs(), refreshLibrary(), refreshStats(), refreshActivity()]);
    } catch (err) { toast(err.message, 'bad'); }
  });

  $('urlAdd').addEventListener('click', async () => {
    const url = $('urlInput').value.trim();
    if (!url) return toast('Paste an address first.', 'warn');
    const button = $('urlAdd');
    button.disabled = true; button.textContent = 'Fetching...';
    try {
      const reply = await api('/kb/documents/url', { method: 'POST', body: Object.assign(
        { url }, uploadMeta('url')) });
      if (reply.ok) { $('urlInput').value = ''; toast('Page fetched - indexing now.'); }
      else toast(reply.detail, 'warn');
      await Promise.all([refreshJobs(), refreshLibrary(), refreshStats(), refreshActivity()]);
    } catch (err) {
      toast(err.message, 'bad');
    } finally {
      button.disabled = false; button.textContent = 'Fetch and index';
    }
  });
}

/* --------------------------------------------------------------------- jobs */
function paintJobs(jobs) {
  const host = $('jobs');
  host.innerHTML = '';
  const live = jobs.filter((j) => j.state === 'running').length;
  const badge = $('jobsBadge');
  badge.textContent = live ? live + ' running' : 'idle';
  badge.className = 'badge' + (live ? ' live' : '');

  if (!jobs.length) {
    host.appendChild(el('div', 'empty', 'Nothing has been ingested yet.'));
    return;
  }

  jobs.forEach((job) => {
    const card = el('div', 'job');
    const top = el('div', 'job-top');
    top.appendChild(el('b', null, job.title || job.doc_id));
    top.appendChild(el('span', 'job-who',
      (job.actor_name || 'someone') + ' - ' + ago(job.started_at)));
    const badgeClass = { running: 'live', done: 'ok', failed: 'bad' }[job.state] || '';
    top.appendChild(el('span', 'badge ' + badgeClass, job.state));
    card.appendChild(top);

    // The rail is the ingest chain in miniature: one segment per stage, coloured
    // by what that stage did. It is the same information the Live chain tab draws
    // in full, and it has to stay readable at this size, so it carries no text.
    const stages = job.stages && job.stages.length ? job.stages
      : state.capabilities.ingest_stages.map((s) => ({ name: s.id, state: 'pending' }));
    const rail = el('div', 'rail');
    const labels = el('div', 'rail-labels');
    stages.forEach((stage) => {
      rail.appendChild(el('span', 'seg ' + stage.state));
      labels.appendChild(el('span', null, stage.name));
    });
    card.appendChild(rail);
    card.appendChild(labels);

    const running = stages.find((s) => s.state === 'running');
    const failed = stages.find((s) => s.state === 'failed');
    const detail = failed ? (failed.name + ': ' + failed.detail)
      : running ? (running.name + ': ' + (running.detail || 'working'))
      : stages.filter((s) => s.state === 'done')
              .map((s) => s.name + ' ' + s.ms + 'ms').join('  ');
    const line = el('div', 'job-detail' + (failed ? ' bad' : ''), detail || '');
    card.appendChild(line);
    host.appendChild(card);
  });
}

async function refreshJobs() {
  try {
    const reply = await api('/kb/jobs?limit=12');
    paintJobs(reply.jobs || []);
    return (reply.jobs || []).some((j) => j.state === 'running');
  } catch (err) {
    return false;
  }
}

/* While something is ingesting the page polls every second; when nothing is, every
   fifteen. Polling hard forever would be a lot of requests to say "still idle". */
function startJobPolling() {
  if (state.jobsTimer) clearTimeout(state.jobsTimer);
  // Embedded in the dashboard, this page stays loaded while the administrator is
  // on some other page of the shell - a hidden iframe still runs its timers. A
  // zero-height document is one nobody is looking at, so it checks back slowly
  // and catches up the moment it is on screen again.
  const onScreen = () => document.documentElement.clientHeight > 0
    && !document.hidden;
  const tick = async () => {
    if (!onScreen()) {
      state.jobsTimer = setTimeout(tick, 5000);
      return;
    }
    const busy = await refreshJobs();
    if (busy) { await Promise.all([refreshLibrary(), refreshStats()]); }
    state.jobsTimer = setTimeout(tick, busy ? 1000 : 15000);
  };
  state.jobsTimer = setTimeout(tick, 1000);
}

async function refreshActivity() {
  try {
    const reply = await api('/kb/activity?limit=25');
    const host = $('activity');
    host.innerHTML = '';
    if (!reply.events.length) {
      host.appendChild(el('div', 'empty', 'No changes recorded yet.'));
      return;
    }
    reply.events.forEach((event) => {
      const row = el('div');
      const time = el('time', null, ago(event.at));
      time.dateTime = event.at;
      row.appendChild(time);
      row.appendChild(el('em', null, event.kind.replace(/_/g, ' ')));
      row.appendChild(el('span', null,
        (event.detail || '') + (event.actor_name ? '  -  ' + event.actor_name : '')));
      host.appendChild(row);
    });
  } catch (err) { /* the trail is not worth a toast */ }
}

/* ============================================================== library tab */
async function refreshLibrary() {
  const params = new URLSearchParams();
  if ($('libState').value) params.set('state', $('libState').value);
  if ($('libAudience').value) params.set('audience', $('libAudience').value);
  if ($('libSearch').value.trim()) params.set('search', $('libSearch').value.trim());
  let reply;
  try {
    reply = await api('/kb/documents?' + params.toString());
  } catch (err) {
    return toast('Could not list the documents: ' + err.message, 'bad');
  }
  state.documents = reply.documents;

  const tiles = $('libTiles');
  tiles.innerHTML = '';
  const byState = reply.counts.by_state || {};
  tiles.appendChild(tile((byState.indexed || {}).documents || 0, 'Indexed',
    ((byState.indexed || {}).chunks || 0) + ' passages'));
  tiles.appendChild(tile((byState.failed || {}).documents || 0, 'Failed'));
  tiles.appendChild(tile((byState.disabled || {}).documents || 0, 'Disabled',
    'Kept, but not retrievable'));
  tiles.appendChild(tile(bytes(reply.counts.bytes || 0), 'Material held'));

  const host = $('library');
  host.innerHTML = '';
  const head = el('div', 'tr head-row lib-row');
  ['Document', 'State', 'Audience', 'Passages', 'Added by', ''].forEach((h) => {
    head.appendChild(el('div', h === 'Passages' ? 'num' : null, h));
  });
  host.appendChild(head);

  if (!reply.documents.length) {
    host.appendChild(el('div', 'empty',
      'Nothing matches. Upload a file on the Feed data tab.'));
    return;
  }

  reply.documents.forEach((doc) => {
    const row = el('div', 'tr lib-row clickable');

    const name = el('div');
    name.appendChild(el('b', null, doc.title));
    name.appendChild(el('small', null,
      doc.source_name + (doc.topics ? '  -  ' + doc.topics : '')));
    row.appendChild(name);

    const cls = { indexed: 'ok', failed: 'bad', disabled: '', queued: 'live' }[doc.state];
    const stateCell = el('div');
    stateCell.appendChild(el('span', 'badge ' + (cls || ''), doc.state));
    row.appendChild(stateCell);

    row.appendChild(el('div', null, { both: 'Both bots', chat: 'Chatbot',
                                      call: 'Calling bot' }[doc.audience] || doc.audience));
    row.appendChild(el('div', 'num', String(doc.chunks)));

    const who = el('div');
    who.appendChild(el('b', null, doc.owner_name || '-'));
    who.appendChild(el('small', null, ago(doc.updated_at)));
    row.appendChild(who);

    const acts = el('div', 'acts');
    const stop = (fn) => (e) => { e.stopPropagation(); fn(); };

    if (doc.state === 'indexed') {
      const off = el('button', 'btn ghost sm', 'Disable');
      off.addEventListener('click', stop(() => setDocState(doc, 'disabled')));
      acts.appendChild(off);
    } else if (doc.state === 'disabled' || doc.state === 'failed') {
      const on = el('button', 'btn ghost sm', 'Re-index');
      on.addEventListener('click', stop(() => reindex(doc)));
      acts.appendChild(on);
    }
    const remove = el('button', 'btn ghost sm danger', 'Delete');
    remove.addEventListener('click', stop(() => deleteDoc(doc)));
    acts.appendChild(remove);
    row.appendChild(acts);

    row.addEventListener('click', () => openDoc(doc.doc_id));
    host.appendChild(row);
  });
}

async function setDocState(doc, wanted) {
  try {
    await api('/kb/documents/' + doc.doc_id + '/state',
              { method: 'POST', body: { state: wanted } });
    toast(doc.title + ' ' + (wanted === 'disabled'
      ? 'is no longer retrievable.' : 'is being indexed again.'));
    await Promise.all([refreshLibrary(), refreshStats(), refreshJobs(), refreshActivity()]);
  } catch (err) { toast(err.message, 'bad'); }
}

async function reindex(doc) {
  try {
    const path = doc.state === 'disabled'
      ? '/kb/documents/' + doc.doc_id + '/state'
      : '/kb/documents/' + doc.doc_id + '/reindex';
    const body = doc.state === 'disabled' ? { state: 'indexed' } : {};
    await api(path, { method: 'POST', body });
    toast('Re-indexing ' + doc.title + '.');
    await Promise.all([refreshJobs(), refreshLibrary(), refreshActivity()]);
  } catch (err) { toast(err.message, 'bad'); }
}

/* Deleting takes a document out of every future answer, so it asks first and says
   what will go. Disabling is offered in the same breath, because it is what most
   people actually want. */
async function deleteDoc(doc) {
  const ok = window.confirm(
    'Delete "' + doc.title + '"?\n\n' +
    'Its ' + doc.chunks + ' passage(s) are removed from the index and the text is '
    + 'not kept. The bots stop being able to quote it immediately.\n\n'
    + 'To stop it answering but keep it, use Disable instead.');
  if (!ok) return;
  try {
    const reply = await api('/kb/documents/' + doc.doc_id, { method: 'DELETE' });
    toast('Deleted. ' + reply.removed_passages + ' passage(s) removed.');
    $('docCard').hidden = true;
    await Promise.all([refreshLibrary(), refreshStats(), refreshActivity()]);
  } catch (err) { toast(err.message, 'bad'); }
}

async function openDoc(docId) {
  let reply;
  try {
    reply = await api('/kb/documents/' + docId);
  } catch (err) { return toast(err.message, 'bad'); }

  const doc = reply.document;
  $('docCard').hidden = false;
  $('docTitle').textContent = doc.title;
  $('docSub').textContent = doc.source_name + '  -  added by '
    + (doc.owner_name || 'unknown') + '  -  ' + ago(doc.created_at);

  const host = $('docDetail');
  host.innerHTML = '';

  const facts = el('div', 'status-list');
  const pairs = [
    ['State', doc.state + (doc.error ? ' - ' + doc.error : '')],
    ['Who may use it', { both: 'Both bots', chat: 'Chatbot only',
                         call: 'Calling bot only' }[doc.audience]],
    ['Passages', doc.chunks],
    ['Characters indexed', doc.characters],
    ['Embedded by', doc.provider || '-'],
    ['Size', bytes(doc.bytes)],
    ['Topics', doc.topics || 'none'],
    ['Crops', doc.crops || 'any'],
    ['Note', doc.notes || '-'],
  ];
  pairs.forEach(([k, v]) => {
    const row = el('div');
    row.appendChild(el('span', null, k));
    row.appendChild(el('span', null, String(v)));
    facts.appendChild(row);
  });
  host.appendChild(facts);

  // Changing the audience here rather than only on upload, because the common case
  // is realising afterwards that something is too long for the phone line.
  const change = el('div', 'row');
  const label = el('label', 'field');
  label.appendChild(el('span', null, 'Change who may use it'));
  const select = el('select');
  state.capabilities.audiences.forEach((a) => {
    const option = el('option', null, a.label);
    option.value = a.id;
    option.selected = a.id === doc.audience;
    select.appendChild(option);
  });
  label.appendChild(select);
  change.appendChild(label);
  const save = el('button', 'btn sm', 'Save');
  save.addEventListener('click', async () => {
    try {
      await api('/kb/documents/' + docId,
                { method: 'PATCH', body: { audience: select.value } });
      toast('Updated. It takes effect on the next question.');
      await Promise.all([refreshLibrary(), refreshActivity()]);
    } catch (err) { toast(err.message, 'bad'); }
  });
  const saveWrap = el('label', 'field');
  saveWrap.appendChild(el('span', null, ' '));
  saveWrap.appendChild(save);
  change.appendChild(saveWrap);
  host.appendChild(change);

  const heading = el('div', 'card-head');
  const headingText = el('div');
  headingText.appendChild(el('h2', null, 'The passages, exactly as retrieval sees them'));
  headingText.appendChild(el('p', null,
    'A bad answer is nearly always a bad passage. This is where to look first.'));
  heading.appendChild(headingText);
  host.appendChild(heading);

  const passages = el('div', 'passages');
  if (!reply.passages.length) {
    passages.appendChild(el('div', 'empty',
      doc.state === 'failed' ? ('Nothing was indexed: ' + doc.error)
                             : 'This document has no passages in the index.'));
  }
  reply.passages.forEach((passage) => {
    const node = el('div', 'passage');
    const top = el('div', 'passage-top');
    top.appendChild(el('b', null, '#' + (passage.position + 1)
      + (passage.section ? '  -  ' + passage.section : '')));
    top.appendChild(el('span', 'score', passage.characters + ' chars'));
    node.appendChild(top);
    node.appendChild(el('p', null, passage.text));
    passages.appendChild(node);
  });
  host.appendChild(passages);
  $('docCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ============================================================ live chain tab */
function blockFor(stage, position) {
  const node = el('div', 'block');
  node.dataset.stage = stage.id;
  node.appendChild(el('span', 'n', 'Step ' + position));
  node.appendChild(el('b', 't', stage.label));
  node.appendChild(el('div', 'd', stage.note));
  node.appendChild(el('span', 'ms', ''));
  return node;
}

function resetChain() {
  const host = $('chain');
  host.innerHTML = '';
  // The diagram is built from what the server says the stages are, in the order
  // the server runs them. A hard-coded copy here would eventually describe a
  // pipeline that no longer exists.
  state.capabilities.query_stages.forEach((stage, index) => {
    host.appendChild(blockFor(stage, index + 1));
  });
  $('answer').innerHTML = '';
  $('notes').innerHTML = '';
  $('passages').innerHTML = '';
  $('verifyBadge').hidden = true;
  $('chainSummary').textContent = 'Running...';
}

function markStage(event) {
  const block = $('chain').querySelector('[data-stage="' + event.stage + '"]');
  if (!block) return;
  block.className = 'block ' + (event.state || '');
  if (event.detail) block.querySelector('.d').textContent = event.detail;
  if (event.ms !== undefined) block.querySelector('.ms').textContent = event.ms + ' ms';
}

/* Citations are rendered as chips so the eye can separate "[2]" the reference from
   "[2]" a number that happened to be in the source text. */
function renderAnswer(text, streaming) {
  const host = $('answer');
  host.innerHTML = '';
  const parts = String(text || '').split(/(\[\d{1,2}\])/g);
  parts.forEach((part) => {
    if (/^\[\d{1,2}\]$/.test(part)) {
      host.appendChild(el('span', 'cite', part.slice(1, -1)));
    } else if (part) {
      host.appendChild(document.createTextNode(part));
    }
  });
  if (streaming) host.appendChild(el('span', 'caret'));
}

function paintResult(result) {
  renderAnswer(result.answer || '', false);
  if (!result.answer) {
    $('answer').appendChild(el('span', 'muted', 'No answer was written.'));
  }

  const badge = $('verifyBadge');
  const verification = result.verification || {};
  if (result.blocked) {
    badge.hidden = false; badge.className = 'badge bad'; badge.textContent = 'refused';
  } else if (verification.grounded === true) {
    badge.hidden = false; badge.className = 'badge ok'; badge.textContent = 'grounded';
  } else if (result.answer) {
    badge.hidden = false; badge.className = 'badge warn';
    badge.textContent = 'check the figures';
  }

  const notes = $('notes');
  notes.innerHTML = '';
  (result.notes || []).forEach((note) => notes.appendChild(el('div', 'note', note)));
  if (verification.note && verification.grounded === false) {
    notes.appendChild(el('div', 'note', verification.note));
  }

  const confidence = result.confidence || {};
  $('chainSummary').textContent =
    (result.duration_ms || 0) + ' ms total  -  ' + (result.passages || []).length
    + ' passage(s)  -  match confidence ' + (confidence.level || '-')
    + (confidence.note ? '  -  ' + confidence.note : '');

  const host = $('passages');
  host.innerHTML = '';
  const cited = new Set((result.citations || []).map((c) => c.citation));
  $('passSub').textContent = (result.citations || []).length
    ? (result.citations.length + ' of ' + result.passages.length + ' were actually cited')
    : 'What the answer was allowed to use';

  if (!(result.passages || []).length) {
    host.appendChild(el('div', 'empty',
      'Nothing was retrieved. Either the knowledge base does not cover this, or the '
      + 'document that does is disabled or set to the other audience.'));
    return;
  }
  result.passages.forEach((passage) => {
    const node = el('div', 'passage');
    const top = el('div', 'passage-top');
    top.appendChild(el('span', 'cite', String(passage.citation)));
    top.appendChild(el('b', null, passage.title
      + (passage.section ? '  -  ' + passage.section : '')));
    if (cited.has(passage.citation)) top.appendChild(el('span', 'badge ok', 'cited'));
    top.appendChild(el('span', 'score', 'score ' + (passage.score || 0).toFixed(3)));
    node.appendChild(top);
    node.appendChild(el('p', null, passage.text));
    const bits = [passage.source];
    if (passage.judge_score !== undefined && passage.judge_score !== null) {
      bits.push('model score ' + passage.judge_score + '/10');
    }
    if (passage.found_by) bits.push('matched by ' + passage.found_by + ' quer(y/ies)');
    if (passage.note) bits.push(passage.note);
    node.appendChild(el('div', 'src', bits.filter(Boolean).join('  -  ')));
    host.appendChild(node);
  });
}

async function runChain() {
  const question = $('q').value.trim();
  if (!question) return toast('Type a question first.', 'warn');
  const button = $('run');
  button.disabled = true;
  resetChain();

  let answer = '';
  try {
    const response = await fetch('/api/kb/query/stream', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        audience: $('qAudience').value,
        strategy: $('qStrategy').value,
        top_k: Number($('qTopK').value) || 5,
        rerank_with_model: $('qJudge').checked,
        generate: $('qGenerate').checked,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || ('HTTP ' + response.status));
    }

    // Server-sent events, read off the body stream. Not EventSource, because that
    // cannot POST and the question does not belong in a URL.
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop();
      for (const frame of frames) {
        const line = frame.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        let event;
        try { event = JSON.parse(line.slice(5).trim()); } catch (err) { continue; }
        if (event.stage) markStage(event);
        else if (event.delta !== undefined) { answer += event.delta; renderAnswer(answer, true); }
        else if (event.result) paintResult(event.result);
        else if (event.error) toast(event.error, 'bad');
      }
    }
  } catch (err) {
    toast('The chain stopped: ' + err.message, 'bad');
    $('chainSummary').textContent = 'The run did not finish.';
  } finally {
    button.disabled = false;
    refreshTraffic();
  }
}

/* ============================================================== traffic tab */
async function refreshTraffic() {
  let reply;
  try {
    reply = await api('/kb/queries?limit=60');
  } catch (err) { return; }

  const health = reply.health || {};
  const tiles = $('trafficTiles');
  tiles.innerHTML = '';
  tiles.appendChild(tile(health.queries || 0, 'Questions'));
  tiles.appendChild(tile(health.found_nothing || 0, 'Found nothing',
    'Gaps in the knowledge base'));
  tiles.appendChild(tile(health.grounded || 0, 'Answers fully grounded'));
  tiles.appendChild(tile(health.blocked || 0, 'Refused',
    'Instruction-shaped questions'));

  const host = $('traffic');
  host.innerHTML = '';
  const head = el('div', 'tr head-row traffic-row');
  ['Question', 'Channel', 'Hits', 'Top score', 'Took', 'When'].forEach((h) => {
    head.appendChild(el('div', ['Hits', 'Top score', 'Took'].includes(h) ? 'num' : null, h));
  });
  host.appendChild(head);

  if (!reply.queries.length) {
    host.appendChild(el('div', 'empty', 'Nothing has been asked yet.'));
    return;
  }
  reply.queries.forEach((row) => {
    const node = el('div', 'tr traffic-row');
    const q = el('div');
    q.appendChild(el('b', null, row.question));
    const sub = [];
    if (row.blocked) sub.push('refused: ' + row.blocked);
    if (row.rewritten) {
      // A HyDE rewrite is a whole hypothetical paragraph. Showing all of it
      // buries the question the row is actually about.
      const short = row.rewritten.length > 110
        ? row.rewritten.slice(0, 110).trim() + '...' : row.rewritten;
      sub.push('also searched: ' + short);
    }
    if (!row.hits && !row.blocked) sub.push('nothing matched - worth an upload');
    if (sub.length) q.appendChild(el('small', null, sub.join('  -  ')));
    node.appendChild(q);
    node.appendChild(el('div', null, row.channel));
    node.appendChild(el('div', 'num', String(row.hits)));
    node.appendChild(el('div', 'num', (row.top_score || 0).toFixed(3)));
    node.appendChild(el('div', 'num', row.duration_ms + ' ms'));
    node.appendChild(el('div', null, ago(row.at)));
    host.appendChild(node);
  });
}

/* ===================================================================== wire */
function wireTabs() {
  $('tabs').addEventListener('click', (e) => {
    const button = e.target.closest('button[data-tab]');
    if (!button) return;
    document.querySelectorAll('.tabs button').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
    button.classList.add('active');
    const panel = $('tab-' + button.dataset.tab);
    panel.classList.add('active');
    if (button.dataset.tab === 'chain' && !$('chain').children.length) resetChain();
    if (button.dataset.tab === 'traffic') refreshTraffic();
    if (button.dataset.tab === 'health') refreshStats();
    if (button.dataset.tab === 'library') refreshLibrary();
  });
}

function wire() {
  $('gateForm').addEventListener('submit', submitGate);
  wireTabs();
  wireUpload();

  $('run').addEventListener('click', runChain);
  $('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') runChain(); });

  let searchTimer = null;
  $('libSearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refreshLibrary, 250);
  });
  $('libState').addEventListener('change', refreshLibrary);
  $('libAudience').addEventListener('change', refreshLibrary);
  $('docClose').addEventListener('click', () => { $('docCard').hidden = true; });

  $('trafficRefresh').addEventListener('click', refreshTraffic);
  $('healthRefresh').addEventListener('click', refreshStats);

  $('signOut').addEventListener('click', async () => {
    try { await api('/auth/logout', { method: 'POST' }); } catch (err) { /* going anyway */ }
    if (state.jobsTimer) clearTimeout(state.jobsTimer);
    state.user = null;
    $('gateStep1').hidden = false;
    $('gateStep2').hidden = true;
    $('gateSubmit').textContent = 'Continue';
    $('gatePass').value = '';
    showGate();
  });
}

wire();
start();
