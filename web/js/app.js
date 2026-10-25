// app.js — shared kernel: auth, transport, status, router, toast, mounts.
// Every lane module imports `App` from here. Keep it dependency-free,
// framework-free, stdlib of the web platform only.

// ---------- pairing key ----------
// arrives once in the URL (?k=), then lives in localStorage
const store = (() => { try { return localStorage; } catch (e) { return sessionStorage; } })();
const K = new URLSearchParams(location.search).get('k') || store.getItem('wfk') || '';
if (K) { store.setItem('wfk', K); if (location.search) history.replaceState(null, '', location.pathname); }

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
  try { j = await r.json(); } catch (e) { /* endpoints may return plain text */ }
  if (r.status === 403) App.emit('unpaired');
  return { ok: r.ok, status: r.status, json: j };
}

// ---------- tiny event bus ----------
const listeners = {};
function on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); return () => off(evt, fn); }
function off(evt, fn) { const l = listeners[evt] || []; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); }
function emit(evt, data) { (listeners[evt] || []).slice().forEach(fn => { try { fn(data); } catch (e) { console.error('[app]', evt, e); } }); }

// ---------- status state ----------
// One object the statusbar renders; lanes read it for context
// (e.g. teach knows if live is on). Polled: /health 3s, /status 3s,
// /live/state faster while the live sheet is open (owned by live.js).
const state = {
  online: false,
  engine: 'none',          // 'python-dtw' | 'rust-kp53' | 'none'
  focus: '',               // active window class on the laptop
  live: { on: false, listening: false, lastMatch: null, lastAct: null },
  lastSend: null,          // {t_ms, ok, into, kind}
  confirms: [],            // tier>=2 actions parked for laptop-side confirm
};

const linkDot = document.getElementById('link-dot');
const statusText = document.getElementById('status-text');
const statusLive = document.getElementById('status-live');

function renderStatus() {
  linkDot.className = 'dot ' + (state.online ? 'on' : 'off');
  const bits = [];
  if (!state.online) bits.push('laptop offline — run: wrap start');
  else {
    bits.push('laptop connected');
    if (state.focus) bits.push('→ ' + state.focus);
  }
  statusText.textContent = bits.join('  ·  ');
  statusLive.hidden = !(state.live.on && state.live.listening);
}

async function pollHealth() {
  try { await fetch('/health', { cache: 'no-store' }); setOnline(true); }
  catch (e) { setOnline(false); }
}
async function pollStatus() {
  if (!state.online) return;
  const r = await req('GET', '/status').catch(() => null);
  if (!r || !r.ok || !r.json) return;
  const j = r.json;
  state.engine = j.engine || 'none';
  state.focus = j.focus || '';
  if (j.live) Object.assign(state.live, j.live);
  if (j.last_send) state.lastSend = j.last_send;
  state.confirms = j.confirms || [];
  emit('status', state);
  renderStatus();
}
function setOnline(v) {
  if (v === state.online) return;
  state.online = v;
  emit(v ? 'online' : 'offline');
  renderStatus();
}
pollHealth();
setInterval(pollHealth, 3000);
setInterval(pollStatus, 3000);

// ---------- router: hash routes = sheets ----------
// Routes: '' or '#/' = compose, '#/live', '#/teach', '#/signs'
// A sheet module registers {el, open(), close()}; the router hides others.
const routes = {};
let current = '';
function route(name, mod) { routes[name] = mod; }
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
// points: hand0 landmarks 0-20 then hand1 landmarks 21-41 (MediaPipe order),
// normalized image coords [0,1], z relative to wrist, score 0..1 (0 = absent).
let kpSeq = 0;
function kpFrame(points) {
  return { t_ms: Math.round(performance.now()), seq: kpSeq++, n: points.length, hands: 2, points };
}

// ---------- exports ----------
export const App = {
  key: K,
  store,
  api: { get: p => req('GET', p), post: (p, b, h) => req('POST', p, b, h), del: p => req('DELETE', p), put: (p, b) => req('PUT', p, b) },
  on, off, emit,
  state, renderStatus,
  route, nav, back, routes, parseHash,
  toast, buzz, sleep,
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

on('unpaired', () => {
  App.wispr.unpair();
  toast('unpaired — rescan QR', true);
});

// tray: LIVE toggle goes to the live sheet; teach/signs open their routes.
// LIVE "off" while the sheet is closed is handled by live.js close().
document.getElementById('tray-live').onclick = () => nav(current === 'live' ? '' : 'live');
document.getElementById('tray-teach').onclick = () => nav('teach');
document.getElementById('tray-signs').onclick = () => nav('signs');
on('route', name => {
  document.getElementById('tray-live').classList.toggle('on', name === 'live');
  document.getElementById('tray-teach').classList.toggle('on', name === 'teach');
  document.getElementById('tray-signs').classList.toggle('on', name === 'signs');
});

// service worker: vendored-shell offline + near-instant warm load (P7 lane owns sw.js)
if ('serviceWorker' in navigator) {
  addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

applyRoute();
