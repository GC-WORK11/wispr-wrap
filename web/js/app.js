import { Flower } from './flower.js';
// app.js — shared kernel: auth, transport, status, router, toast, mounts.
// Every lane module imports `App` from here. Keep it dependency-free,
// framework-free, stdlib of the web platform only.

// ---------- pairing key ----------
// arrives once in the URL (?k=), then lives in localStorage
const store = (() => { try { return localStorage; } catch (e) { return sessionStorage; } })();
const K = new URLSearchParams(location.search).get('k') || store.getItem('wfk') || '';
// ?replay opts this browser into the kp42 demo-replay seam (pose.js) — it is
// sticky in localStorage, so the URL cleans itself and judges never see it.
if (new URLSearchParams(location.search).has('replay')) store.setItem('wfreplay', '1');
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

/* Upload a blob with real progress + cancel. fetch(Blob) gives neither, and a
   phone video is 20-200 MB, so the composer needs both. XHR carries the same
   X-WF-Key auth, the same lenient JSON parse (bare Infinity/NaN from older
   daemons), and raises the same 'unpaired' event on 403 as req() above.
   onProgress(fraction 0..1, {loaded, total}) fires while the body uploads;
   abortSignal (an AbortController's .signal) cancels the transfer.
   Resolves {ok, status, json, aborted} — aborted uploads never throw. */
function upload(path, blob, extraHeaders, onProgress, abortSignal) {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    let aborted = false;
    xhr.open('POST', path, true);
    xhr.setRequestHeader('X-WF-Key', K);
    const h = extraHeaders || {};
    for (const k of Object.keys(h)) if (h[k] != null) xhr.setRequestHeader(k, h[k]);
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.total ? e.loaded / e.total : 0, { loaded: e.loaded, total: e.total });
      };
    }
    xhr.onload = () => {
      let j = null; const txt = xhr.responseText || '';
      if (txt) {
        try { j = JSON.parse(txt); }
        catch (e) {
          try { j = JSON.parse(txt.replace(/([:\[,]\s*)-?(?:Infinity|NaN)(?=\s*[,}\]])/g, '$1null')); }
          catch (e2) { /* plain-text reply */ }
        }
      }
      if (xhr.status === 403) App.emit('unpaired');
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, json: j });
    };
    xhr.onerror = () => resolve({ ok: false, status: 0, json: null });
    xhr.ontimeout = () => resolve({ ok: false, status: 0, json: null, timeout: true });
    xhr.onabort = () => { aborted = true; resolve({ ok: false, status: 0, json: null, aborted: true }); };
    if (abortSignal) {
      if (abortSignal.aborted) { try { xhr.abort(); } catch (e) {} }
      else abortSignal.addEventListener('abort', () => { try { xhr.abort(); } catch (e) {} }, { once: true });
    }
    try { xhr.send(blob); }
    catch (e) { resolve({ ok: false, status: 0, json: null }); }
  });
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
  laptop: null,            // GET /status laptop:{connected,name,canAct,signs} (null on older daemons)
  bankSize: null,
};

const linkDot = document.getElementById('link-dot');          // dot on the "Connected laptop" row
const lapSub = document.getElementById('dr-laptop-s');        // its second line
const connHint = document.getElementById('conn-hint');         // "Connect your laptop" on the home screen
const connected = () => !!(state.online && state.paired && (!state.laptop || state.laptop.connected));
const canAct = () => connected() && (!state.laptop || state.laptop.canAct);

function renderStatus() {
  const ok = connected();
  if (linkDot) linkDot.className = 'dr-dot' + (ok ? ' on' : '');
  const sub = ok ? 'Connected' : '';
  if (lapSub) lapSub.textContent = sub;
  // spec: laptop features exist only while a laptop is connected; otherwise one "Connect your laptop" row
  for (const id of ['tray-teach', 'tray-signs', 'tray-live', 'dr-laptop']) { const el = document.getElementById(id); if (el) el.hidden = !ok; }
  const dc = document.getElementById('dr-connect'); if (dc) dc.hidden = ok;
  if (connHint) connHint.hidden = ok || !state.checked || !K;
  emit('statusbar', state);
}

async function pollHealth() {
  try { await fetch('/health', { cache: 'no-store' }); setOnline(true); }
  catch (e) { setOnline(false); }
  if (!state.checked) { state.checked = true; renderStatus(); }
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
  if (v) { pollEngine(); pollStatus(); }
  renderStatus();
}
pollHealth();
setInterval(pollHealth, 3000);
setInterval(pollState, 1200);
setInterval(pollEngine, 15000);
// GET /status: who the laptop is, whether it can act, how many signs it knows
async function pollStatus() {
  if (!state.online || !K) return;
  const r = await req('GET', '/status').catch(() => null);
  if (!r || !r.ok || !r.json || !r.json.laptop) return;       // older daemon: keep the /health-only behaviour
  const l = r.json.laptop;
  state.laptop = { connected: l.connected !== false, name: l.name || '', canAct: l.can_act !== false, signs: l.signs, engine: l.engine };
  if (typeof l.signs === 'number') state.bankSize = l.signs;
  if (l.name) store.setItem('wfname', l.name);
  renderStatus();
}
setInterval(pollStatus, 4000);

// ---------- router: hash routes = sheets ----------
// Routes: '' or '#/' = home, '#/live', '#/teach', '#/signs', '#/laptop', '#/settings'
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
  return ['live', 'teach', 'signs', 'laptop', 'settings'].includes(h) ? h : '';
}
async function applyRoute() {
  const name = parseHash();
  if (name === current) return;
  // laptop-only surfaces: a stale or hand-typed deep-link bounces to the
  // connect page until pairing says connected.
  if (!connected() && ['live', 'teach', 'signs'].includes(name)) {
    if (location.hash !== '#/laptop') { location.hash = '#/laptop'; return; }
  }
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

const buzz = () => { try { if (store.getItem('wf.haptics') !== '0') navigator.vibrate && navigator.vibrate(18); } catch (e) {} };
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

// daemon `error` fields are machine strings ("unpaired", "reserved mode
// sign") — never show them raw. Map the ones we know, fall back generic.
function daemonErr(r, fallback) {
  const e = (r && r.json && r.json.error) || '';
  if (r && r.status === 403 || /unpaired|pair/i.test(e)) return 'Not connected — scan the code again.';
  if (/reserved/.test(e)) return 'That name is taken by a built-in sign.';
  if (/too large|too big/.test(e)) return 'That file is too big to send.';
  if (/rate|slow/.test(e)) return 'Slow down — try again.';
  if (/name required|gloss required|required/.test(e)) return 'Give it a name first.';
  if (/built in/.test(e)) return 'That sign is already built in.';
  return fallback;
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
  api: { get: p => req('GET', p), post: (p, b, h) => req('POST', p, b, h), del: p => req('DELETE', p), put: (p, b) => req('PUT', p, b), upload },
  on, off, emit,
  state, renderStatus, pollState, pollEngine, pollStatus, connected, canAct,
  connect: () => { showOnboarding(''); onbSkip.hidden = false; },
  route, nav, back, routes, parseHash,
  toast, buzz, sleep, raise,
  kpFrame,
  daemonErr,
  el: {
    pill: document.getElementById('pill'),
    stage: document.getElementById('stage'),
    composer: document.getElementById('composer'),
    toast: toastEl,
    sheetLive: document.getElementById('sheet-live'),
    sheetTeach: document.getElementById('sheet-teach'),
    sheetSigns: document.getElementById('sheet-signs'),
    sheetLaptop: document.getElementById('sheet-laptop'),
    sheetSettings: document.getElementById('sheet-settings'),
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

// Go Live = the black waveform button in the composer; everything else lives in the drawer.
const drawer = document.getElementById('drawer'), scrim = document.getElementById('scrim'),
      menuBtn = document.getElementById('menu');
function openDrawer(v) {
  drawer.classList.toggle('open', v);
  drawer.setAttribute('aria-hidden', v ? 'false' : 'true');
  menuBtn.setAttribute('aria-expanded', v ? 'true' : 'false');
  scrim.hidden = !v;
  if (v) { renderStatus(); pollEngine(); }
}
menuBtn.onclick = () => openDrawer(!drawer.classList.contains('open'));
// other lanes (PRO's drawer.js) consume this instead of touching the frame
App.drawer = { el: drawer, nav: document.getElementById('drawer-nav'), open: () => openDrawer(true), close: () => openDrawer(false),
               toggle: () => openDrawer(!drawer.classList.contains('open')), get isOpen() { return drawer.classList.contains('open'); } };
scrim.onclick = () => openDrawer(false);
document.getElementById('dr-close').onclick = () => openDrawer(false);
addEventListener('keydown', e => { if (e.key === 'Escape' && drawer.classList.contains('open')) openDrawer(false); });
const go = name => () => { openDrawer(false); nav(name); };
document.getElementById('tray-live').onclick = () => nav(current === 'live' ? '' : 'live');
document.getElementById('tray-teach').onclick = go('teach');
document.getElementById('tray-signs').onclick = go('signs');
document.getElementById('dr-laptop').onclick = go('laptop');
document.getElementById('dr-connect').onclick = () => { openDrawer(false); App.connect(); };
document.getElementById('dr-settings').onclick = go('settings');
if (connHint) connHint.onclick = () => nav('laptop');
on('route', name => { openDrawer(false); document.body.classList.toggle('sheet-open', name !== ''); });

// overlay pickers (actions/macro) share z-40 — a nested pick (macro's
// "pick action" opens the actions sheet) must paint above its parent,
// so the last-opened .sheet-lower always gets the top slot.
function raise(el) {
  let top = 39;
  for (const s of document.querySelectorAll('.sheet-lower'))
    if (!s.hidden) top = Math.max(top, parseInt(s.style.zIndex || '40', 10));
  el.style.zIndex = top + 1;
}

// "My signs" count in the drawer; engine names are developer detail (?dev only)
const railCount = document.getElementById('rail-count');
const DEV = /[?&]dev\b/.test(location.search) || store.getItem('wfdev') === '1';
if (DEV) document.body.classList.add('dev');
on('statusbar', s => {
  if (railCount) railCount.textContent = s.online && s.paired && s.bankSize ? String(s.bankSize) : '';
});

// service worker: vendored-shell offline + near-instant warm load
if ('serviceWorker' in navigator) {
  addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

applyRoute();

// the lotus: opens when the laptop link is up, closes to a bud when it is not,
// and sleeps while a full-screen page covers it
Flower.init({ field: document.getElementById('field'), anchor: document.getElementById('composer'), top: 64 });
on('statusbar', () => Flower.setOnline(connected() || !state.checked));
on('route', name => { Flower.pause(name !== ''); if (!name) setTimeout(Flower.measure, 60); });

// loading screen (boot.js owns readiness + the fade): when it hands over, the ASCII lotus replays its sprout
// from a bud, so every load and refresh ends in the bloom. Without a splash (or after bfcache restore) replay now.
if (document.getElementById('boot') && !(window.__boot && window.__boot.done)) addEventListener('wl:boot-done', () => Flower.restart(), { once: true });
else Flower.restart();
addEventListener('pageshow', e => { if (e.persisted) Flower.restart(); });   // back/forward restore: bloom again
