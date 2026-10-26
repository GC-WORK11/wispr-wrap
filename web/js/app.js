// app.js — shared kernel: auth, transport, status, router, toast, mounts.
// Every lane module imports `App` from here. Keep it dependency-free,
// framework-free, stdlib of the web platform only.

// ---------- pairing key ----------
// arrives once in the URL (?k=), then lives in localStorage
const store = (() => { try { return localStorage; } catch (e) { return sessionStorage; } })();
const K = new URLSearchParams(location.search).get('k') || store.getItem('wfk') || '';
if (K) { store.setItem('wfk', K); if (location.search) history.replaceState(null, '', location.pathname + location.hash); }

// ---------- transport ----------
// All daemon endpoints share X-WF-Key auth (hmac-compared laptop-side).
// api.post(path, body) where body = object (JSON) | Blob | FormData.
async function req(method, path, body, extraHeaders) {
  const headers = { 'X-WF-Key': K, ...(extraHeaders || {}) };
  const opt = { method, headers };
  if (body !== undefined) {
    if (body instanceof Blob || body instanceof FormData) {
      opt.body = body; // caller sets Content-Type for blobs (sniffed laptop-side)
    } else {
      headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
  }
  const r = await fetch(path, opt);
  let j = null;
  /* Older wfd builds print Python's bare Infinity/NaN (a one-sign bank has no
     runner-up, so margin=Infinity). That is not JSON: JSON.parse throws and
     the live reading strip silently froze. Parse strictly first, then retry
     with those literals turned into null. */
  let txt = '';
  try { txt = await r.text(); } catch (e) { /* network cut mid-body */ }
  if (txt) {
    try { j = JSON.parse(txt); }
    catch (e) {
      try { j = JSON.parse(txt.replace(/([:\[,]\s*)-?(?:Infinity|NaN)(?=\s*[,}\]])/g, '$1null')); }
      catch (e2) { /* endpoints may return plain text */ }
    }
  }
  if (r.status === 403) App.emit('unpaired');
  return { ok: r.ok, status: r.status, json: j };
}

// ---------- tiny event bus ----------
const listeners = {};
function on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); return () => off(evt, fn); }
function off(evt, fn) { const l = listeners[evt] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
function emit(evt, data) { (listeners[evt] || []).slice().forEach(fn => { try { fn(data); } catch (e) { console.error('[app]', evt, e); } }); }

// ---------- status state ----------
// One object the statusbar renders; lanes read it for context.
// Sources (the real daemon contract — there is no /status):
//   GET /health      open      3s   — laptop link
//   GET /engine      gated     on connect + 15s — engine name, bank size
//   GET /live/state  gated     ~1s idle / ~6Hz while live sheet open — listening,
//                                 last match, pending confirms, teach session
const state = {
  online: false,
  paired: !!K,
  engine: 'none',          // 'python-dtw' | 'rust-kp53' | 'none'
  liveApi: false,          // daemon serves the live surface (not pre-merge)
  focus: '',               // active window on the laptop (when the daemon reports it)
  live: { on: false, listening: false, signing: false, lastMatch: null },
  lastSend: null,          // {t_ms, ok, into, kind}
  confirms: [],            // tier>=2 actions parked for confirm
  bankSize: null,
};

const linkDot = document.getElementById('link-dot');
const statusText = document.getElementById('status-text');
const statusSub = document.getElementById('status-sub');
const statusLive = document.getElementById('status-live');

function renderStatus() {
  linkDot.className = 'dot ' + (state.online ? 'on' : 'off');
  let head, sub = '';
  if (!state.online) { head = 'Laptop offline'; sub = 'run: wrap start'; }
  else if (!state.paired) { head = 'Laptop found'; sub = 'needs its code'; }
  else {
    head = 'Laptop connected';
    if (state.focus) sub = 'typing into ' + state.focus;
    else if (state.lastSend && state.lastSend.into) sub = 'last into ' + state.lastSend.into;
  }
  statusText.textContent = head;
  if (statusSub) statusSub.textContent = sub;
  statusText.title = head + (sub ? ' · ' + sub : '');
  statusLive.hidden = !(state.live.listening);
  emit('statusbar', state);
}

async function pollHealth() {
  try { await fetch('/health', { cache: 'no-store' }); setOnline(true); }
  catch (e) { setOnline(false); }
}
// /live/state is the status surface: listening, last match (with the engine's
// own reason), pending confirms, focused window when the act layer reports it.
async function pollState() {
  if (!state.online) return;
  if (!K) { state.paired = false; renderStatus(); return; }
  const r = await req('GET', '/live/state').catch(() => null);
  if (!r) return;
  if (r.status === 403) { state.paired = false; renderStatus(); return; }
  state.paired = true;
  if (r.status === 404 || r.status === 405) {          // pre-merge daemon
    if (state.liveApi) { state.liveApi = false; emit('liveapi', false); }
    renderStatus(); return;
  }
  if (!r.ok || !r.json) return;
  if (!state.liveApi) { state.liveApi = true; emit('liveapi', true); }
  const j = r.json;
  const listening = !!(j.listening || j.mode === 'listening' || j.state === 'listening');
  const prev = state.live.listening;
  state.live.listening = listening;
  state.live.signing = !!j.signing;
  if (j.last_match !== undefined) state.live.lastMatch = j.last_match;
  const focus = j.focus || j.focused || j.wm_class ||
    (j.focused_app && (j.focused_app.wm_class || j.focused_app.title)) || '';
  if (focus !== state.focus) state.focus = focus;
  if (typeof j.bank_size === 'number') state.bankSize = j.bank_size;
  const pend = Array.isArray(j.pending) ? j.pending : [];
  if (pend.length !== state.confirms.length ||
      pend.some((p, i) => p.id !== (state.confirms[i] || {}).id)) {
    state.confirms = pend;
    emit('confirms', pend);
  }
  if (listening !== prev) emit('listening', listening);
  emit('livestate', j);
  renderStatus();
}
async function pollEngine() {
  if (!state.online || !K) return;
  const r = await req('GET', '/engine').catch(() => null);
  if (r && r.ok && r.json) {
    state.engine = r.json.engine || 'none';
    if (typeof r.json.bank_size === 'number') state.bankSize = r.json.bank_size;
    if (r.json.listening !== undefined) state.live.listening = !!r.json.listening;
    renderStatus();
  }
}
function setOnline(v) {
  if (v === state.online) return;
  state.online = v;
  emit(v ? 'online' : 'offline');
  if (v) { pollEngine(); }
  renderStatus();
}
pollHealth();
setInterval(pollHealth, 3000);
setInterval(pollState, 1200);
setInterval(pollEngine, 15000);

// ---------- router: hash routes = sheets ----------
// Routes: '' or '#/' = compose, '#/live', '#/teach', '#/signs'
// A sheet module registers {el, open(), close()}; the router hides others.
const routes = {};
let current = '';
function route(name, mod) {
  routes[name] = mod;
  // deep-link race: applyRoute() already ran (empty registry) before this
  // module loaded — if the hash wants THIS route, open it now.
  if (name === parseHash() && mod.el) {
    mod.el.hidden = false;
    if (mod.open) Promise.resolve(mod.open()).catch(e => console.error('[route]', name, e));
  }
}
function parseHash() {
  const h = location.hash.replace(/^#\/?/, '');
  return h === 'live' || h === 'teach' || h === 'signs' ? h : '';
}
async function applyRoute() {
  const name = parseHash();
  if (name === current) return;
  const prev = routes[current];
  if (prev && prev.close) await prev.close();
  current = name;
  for (const k of Object.keys(routes)) {
    const m = routes[k];
    if (m.el) m.el.hidden = (k !== name);
  }
  const next = routes[name];
  if (next && next.open) await next.open();
  emit('route', name);
}
addEventListener('hashchange', applyRoute);
function nav(name) {
  const h = name ? '#/' + name : '#/';
  if (location.hash === h) applyRoute(); else location.hash = h;
}
function back() { nav(''); }

// ---------- toast ----------
const toastEl = document.getElementById('toast');
let toastT;
function toast(msg, isErr) {
  toastEl.textContent = msg;
  toastEl.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastT);
  toastT = setTimeout(() => toastEl.classList.remove('show'), 2000);
}

const buzz = () => { try { navigator.vibrate && navigator.vibrate(18); } catch (e) {} };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ---------- shared keypoint envelope ----------
// One canonical frame shape the app produces — every lane that sends
// keypoints (live, teach) uses this so the daemon sees one format.
//   {t_ms, seq, n:42, points:[{x,y,z,score}...], hands:2}
// points: LEFT hand landmarks 0-20 then RIGHT landmarks 21-41 (MediaPipe
// order), normalized image coords [0,1], score 0..1 (0 = absent hand).
// This matches the daemon's signbank/endpointer kp42 contract exactly.
let kpSeq = 0;
function kpFrame(points) {
  return { t_ms: Math.round(performance.now()), seq: kpSeq++, n: points.length, hands: 2, points };
}

// ---------- pending-confirm strip ----------
// tier>=2 actions park laptop-side; the daemon's /live/state exposes them
// and POST /act/confirm {id} releases one. The composer shows them honestly.
const confirmBar = document.getElementById('confirms');
function renderConfirms(rows) {
  if (!confirmBar) return;
  confirmBar.textContent = '';
  if (!rows.length) { confirmBar.hidden = true; return; }
  confirmBar.hidden = false;
  for (const p of rows) {
    const row = document.createElement('div');
    row.className = 'confirm-row';
    const txt = document.createElement('span');
    txt.className = 'confirm-txt';
    txt.textContent = (p.label || p.gloss || 'action') + ' — held for confirm';
    const yes = document.createElement('button');
    yes.className = 'confirm-yes'; yes.textContent = 'run it';
    const no = document.createElement('button');
    no.className = 'confirm-no'; no.textContent = 'cancel';
    yes.onclick = async () => {
      yes.disabled = no.disabled = true;
      const r = await req('POST', '/act/confirm', { id: p.id }).catch(() => null);
      if (r && r.ok && r.json && r.json.fired) toast('fired ' + (p.label || p.gloss));
      else if (r && r.ok) toast('done');
      else toast('confirm failed' + (r ? ' (' + r.status + ')' : ''), true);
      pollState();
    };
    no.onclick = async () => {
      yes.disabled = no.disabled = true;
      await req('POST', '/act/confirm', { id: p.id, cancel: true }).catch(() => null);
      pollState();
    };
    row.append(txt, yes, no);
    confirmBar.appendChild(row);
  }
}
on('confirms', renderConfirms);

// ---------- exports ----------
export const App = {
  key: K,
  store,
  api: { get: p => req('GET', p), post: (p, b, h) => req('POST', p, b, h), del: p => req('DELETE', p), put: (p, b) => req('PUT', p, b) },
  on, off, emit,
  state, renderStatus, pollState, pollEngine,
  route, nav, back, routes, parseHash,
  toast, buzz, sleep, raise,
  kpFrame,
  el: {
    pill: document.getElementById('pill'),
    stage: document.getElementById('stage'),
    toast: toastEl,
    statusText, statusLive,
    sheetLive: document.getElementById('sheet-live'),
    sheetTeach: document.getElementById('sheet-teach'),
    sheetSigns: document.getElementById('sheet-signs'),
    sheetActions: document.getElementById('sheet-actions'),
    sheetMacro: document.getElementById('sheet-macro'),
  },
  // Wispr dictation bridge (Android app injects window.WF)
  wispr: {
    has() { try { return !!(window.WF && window.WF.hasWispr && window.WF.hasWispr()); } catch (e) { return false; } },
    focusField() { try { window.WF && window.WF.focusField && window.WF.focusField(); } catch (e) {} },
    unpair() { try { window.WF && window.WF.unpair && window.WF.unpair(); } catch (e) {} },
  },
};

// ---------- first run: connect to the laptop ----------
// Laptop-local browser: /pair-info (loopback-only, refuses proxied requests)
// hands over the key — zero typing. A phone enters the 8-char code instead.
const onb = document.getElementById('onb'), onbCode = document.getElementById('onb-code'),
      onbGo = document.getElementById('onb-go'), onbErr = document.getElementById('onb-err');
function showOnboarding(msg) {
  onb.hidden = false;
  onbErr.textContent = msg || '';
  setTimeout(() => onbCode.focus(), 250);
}
async function autoPair() {
  try {
    const r = await fetch('/pair-info', { cache: 'no-store' });
    if (!r.ok) return false;
    const j = await r.json();
    if (!j || !j.key) return false;
    store.setItem('wfk', j.key);
    if (j.name) store.setItem('wfname', j.name);
    return true;
  } catch (e) { return false; }
}
async function pairWithCode() {
  const code = onbCode.value.trim();
  if (code.length < 4) { onbErr.textContent = 'Type the code your laptop is showing.'; return; }
  onbGo.disabled = true; onbGo.textContent = 'Connecting…'; onbErr.textContent = '';
  try {
    const r = await fetch('/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }) });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.key) {
      store.setItem('wfk', j.key);
      onbGo.textContent = 'Connected ✓';
      setTimeout(() => location.replace(location.pathname + location.hash), 350);
      return;
    }
    onbErr.textContent = r.status === 429 ? 'One try per second — give it a moment.'
      : r.status === 403 ? 'That code isn’t right. Check your laptop screen.'
      : 'Couldn’t pair (' + r.status + ').';
  } catch (e) { onbErr.textContent = 'Can’t reach your laptop from here.'; }
  onbGo.disabled = false; onbGo.textContent = 'Connect';
}
onbGo.onclick = pairWithCode;
onbCode.addEventListener('keydown', e => { if (e.key === 'Enter') pairWithCode(); });
onbCode.addEventListener('input', () => { onbCode.value = onbCode.value.toUpperCase().replace(/[^A-Z0-9]/g, ''); onbErr.textContent = ''; });
const onbSkip = document.getElementById('onb-skip');
onbSkip.onclick = () => { onb.hidden = true; };
async function firstRun() {
  if (await autoPair()) { location.replace(location.pathname + location.hash); return; }
  showOnboarding('');
}
if (!K) firstRun();

// a 403 can also mean "this route is LAN/USB-only", so a rejected key never
// wipes the stored pairing — it offers the code screen once, dismissibly.
let offered403 = false;
on('unpaired', () => {
  App.wispr.unpair();
  state.paired = false;
  renderStatus();
  if (offered403 || !K) return;
  offered403 = true;
  showOnboarding('This laptop doesn’t recognise this phone any more. Enter its new code to reconnect.');
  onbSkip.hidden = false;
});

// rail: Go Live toggles the live view; compose/teach/signs are nav items.
// LIVE "off" while the view is closed is handled by live.js close().
document.getElementById('tray-live').onclick = () => nav(current === 'live' ? '' : 'live');
document.getElementById('tray-compose').onclick = () => nav('');
document.getElementById('tray-teach').onclick = () => nav('teach');
document.getElementById('tray-signs').onclick = () => nav('signs');
const goSub = document.getElementById('golive-sub');
on('route', name => {
  const live = document.getElementById('tray-live');
  live.classList.toggle('on', name === 'live');
  live.setAttribute('aria-pressed', name === 'live' ? 'true' : 'false');
  if (goSub) goSub.textContent = name === 'live' ? 'live now · tap to end' : 'sign → laptop acts';
  document.getElementById('tray-compose').classList.toggle('on', name === '');
  document.getElementById('tray-teach').classList.toggle('on', name === 'teach');
  document.getElementById('tray-signs').classList.toggle('on', name === 'signs');
});

// overlay pickers (actions/macro) share z-40 — a nested pick (macro's
// "pick action" opens the actions sheet) must paint above its parent,
// so the last-opened .sheet-lower always gets the top slot.
function raise(el) {
  let top = 39;
  for (const s of document.querySelectorAll('.sheet-lower'))
    if (!s.hidden) top = Math.max(top, parseInt(s.style.zIndex || '40', 10));
  el.style.zIndex = top + 1;
}

// rail foot: honest engine + bank info once the daemon reports it
const railEng = document.getElementById('rail-eng');
const railCount = document.getElementById('rail-count');
const DEV = /[?&]dev\b/.test(location.search) || store.getItem('wfdev') === '1';
if (DEV) document.body.classList.add('dev');
on('statusbar', s => {
  if (railCount) railCount.textContent = s.online && s.paired && s.bankSize ? String(s.bankSize) : '';
  if (!railEng) return;
  // engine names are developer detail — only with ?dev or localStorage.wfdev=1
  railEng.textContent = DEV && s.online && s.paired && s.engine && s.engine !== 'none' ? s.engine : '';
});

// service worker: vendored-shell offline + near-instant warm load
if ('serviceWorker' in navigator) {
  addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

applyRoute();
