// live.js — LIVE mode: camera -> in-browser kp42 -> daemon -> actions.
// Video never leaves this device; ~1 KB of keypoints per frame at ~12 Hz.
//
// Wire contract (all authed X-WF-Key; polling only — no websockets):
//   POST /live/kp     {t_ms, points[42], session?}  -> {ok, session,
//                     mode?, match?, action?, teach?}   match/teach ride the
//                     SAME response — actions surface the frame they fire on
//   GET  /live/state  {listening, last_match:{gloss,dist,accepted,reason,
//                     margin,second,action,pending}, signing, pending[],
//                     bank_size, teach{...}}            polled ~6 Hz open
//   GET  /engine      {engine, bank_size, listening, signbank}
//   POST /act/confirm {id, cancel?}                    release a parked tier>=2
// Palm-open-hold / palms-together are detected BY THE DAEMON's endpointer
// (PALM-OPEN / NAMASTE mode events); the sheet's hold bar is honest local
// feedback, not a second detector.
import { App } from './app.js';
import { Pose } from './pose.js';
import { Skel } from './skeleton.js';

const sheet = App.el.sheetLive;
if (!sheet) throw new Error('live: #sheet-live missing');

/* ---------- DOM (built once — index.html keeps only the mount) ---------- */
sheet.innerHTML = `
  <div class="lv-view" id="lv-view">
    <video id="lv-cam" playsinline muted autoplay></video>
    <canvas id="lv-skel"></canvas>
    <div class="lv-top">
      <button class="lv-rbtn" id="lv-x" aria-label="Close Go Live">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
      <div class="lv-seg" id="lv-seg" role="tablist" aria-label="Go Live mode">
        <button role="tab" id="lv-m-text" data-m="text" aria-selected="false">Text</button>
        <button role="tab" id="lv-m-ctl" data-m="control" aria-selected="true">Control</button>
      </div>
      <button class="lv-rbtn" id="lv-flip" aria-label="Flip camera">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.3-4.6L4 8.5M4 4v4.5h4.5M4 13a8 8 0 0 0 14.3 4.6l1.7-2.1M20 20v-4.5h-4.5"/></svg>
      </button>
    </div>
    <div class="lv-tag" id="lv-tag" hidden>Demo input · recorded hand motion</div>
    <div class="lv-bottom">
      <div class="lv-read">
        <span class="lv-rlab" id="lv-rlab"></span>
        <div class="lv-rguess dim" id="lv-guess"></div>
        <span class="lv-rdist" id="lv-dist"></span>
      </div>
      <div class="lv-why" id="lv-why"></div>
      <div class="lv-act" id="lv-act"><i></i><span id="lv-act-t"></span></div>
      <div class="lv-pend" id="lv-pend" hidden></div>
      <div class="lv-hold">
        <div class="lv-hold-bar"><i id="lv-hold-i"></i></div>
        <div class="lv-hint" id="lv-hint"></div>
      </div>
      <button class="lv-send" id="lv-send" aria-label="Send to laptop" hidden>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>
      </button>
    </div>
    <div class="lv-cover" id="lv-cover" hidden>
      <div class="lv-card">
        <div class="lv-card-t" id="lv-card-t"></div>
        <div class="lv-card-m" id="lv-card-m"></div>
        <div class="lv-card-b" id="lv-card-b"></div>
      </div>
    </div>
    <span class="lv-badge" id="lv-badge"><i></i><span id="lv-badge-t">off</span></span>
    <span class="lv-eng" id="lv-eng"></span>
  </div>`;
const $ = id => sheet.querySelector('#' + id);
const video = $('lv-cam'), skel = $('lv-skel'), cover = $('lv-cover'),
      cardT = $('lv-card-t'), cardM = $('lv-card-m'), cardB = $('lv-card-b'),
      engEl = $('lv-eng'), badge = $('lv-badge'), badgeT = $('lv-badge-t'),
      guessEl = $('lv-guess'), distEl = $('lv-dist'), whyEl = $('lv-why'),
      actEl = $('lv-act'), actT = $('lv-act-t'), pendEl = $('lv-pend'),
      holdI = $('lv-hold-i'), hint = $('lv-hint'), xbtn = $('lv-x'),
      viewEl = $('lv-view'), sendBtn = $('lv-send'), flipBtn = $('lv-flip'),
      segText = $('lv-m-text'), segCtl = $('lv-m-ctl'), tagEl = $('lv-tag');

const HINTS = {
  control: { idle: 'Hold an open palm to start. Then sign a command.', on: 'Listening. Sign a command.' },
  text:    { idle: 'Hold an open palm to start. Then sign words.',   on: 'Listening. Each sign becomes a word.' },
};
let HINT_DEF = HINTS.control.idle;
const KP_MS = 83;        // ~12 Hz keypoint frames (spec: 10-15 Hz)
const ST_MS = 167;       // ~6 Hz state poll while open

/* ---------- shared kp stream (teach.js reuses KpLink) ---------- */
export class KpLink {
  /* start(video, engine, {session, onFrame(pts,t,resp), onResp(j), onStatus(s)})
     detect -> POST /live/kp, latest-wins (a slow link drops, never queues). */
  constructor() { this.reset(); }
  reset() {
    this.session = null; this.inflight = false; this.pend = null;
    this.timer = null; this.video = null; this.engine = null;
    this.onFrame = null; this.onResp = null; this.onStatus = null;
    this.dead = false;    // 404/405 — pre-merge daemon
    this.denied = false;  // 403 — unpaired or proxied
    this._fake = null;
  }
  start(video, engine, opts) {
    this.stop();
    Object.assign(this, opts || {});
    this.video = video; this.engine = engine;
    /* test seam: tests/selftest.html injects window.WFFakeTrack before load —
       a fake kp42 source so a headless browser can drive the real wizard.
       Production pages never define it, so this branch is dead there. */
    if (window.WFFakeTrack && typeof window.WFFakeTrack.start === 'function') {
      this._fake = window.WFFakeTrack.start(video, res => {
        const t = res && res.t_ms != null ? res.t_ms : performance.now();
        const pts = res && res.points;
        if (!pts || pts.length !== 42) return;
        if (this.onFrame) this.onFrame(pts, t);
        this.send(pts, t);
      });
      return;
    }
    this.timer = setInterval(() => this.tick(), KP_MS);
  }
  tick() {
    if (!this.engine) return;
    if (!(this.engine.videoless) &&
        (!this.video || this.video.readyState < 2)) return;
    const t = performance.now();
    let res;
    try { res = this.engine.detectFrame(this.video, t); } catch (e) { return; }
    if (!res || !res.points) return;
    if (this.onFrame) this.onFrame(res.points, t);
    this.send(res.points, t);
  }
  send(pts, t) {
    if (this.inflight) { this.pend = { pts, t }; return; }
    this.inflight = true;
    /* t_ms is wall-clock based, not performance.now(): that restarts at 0 on
       every page load, so after a reload the daemon saw time run BACKWARDS
       (negative gap -> "dup-fire" suppressed the first sign of a gloss it had
       just fired before the reload; older frames can also be ignored).
       timeOrigin + now() only ever grows across reloads. */
    const body = { t_ms: Math.round(performance.timeOrigin + t - 1.78e12), points: pts };
    if (this.session) body.session = this.session;
    // real video aspect lets the daemon map normalized kp to portrait sensors
    // engines assume 16:9 otherwise. Replayed streams have no video.
    if (this.video && this.video.videoWidth)
      body.aspect = +(this.video.videoWidth / this.video.videoHeight).toFixed(4);
    App.api.post('/live/kp', body)
      .then(r => {
        if (r.status === 403) { this.denied = true; this.stop(); if (this.onStatus) this.onStatus('denied'); return; }
        if (r.status === 404 || r.status === 405) { this.dead = true; this.stop(); if (this.onStatus) this.onStatus('dead'); return; }
        if (r.ok && r.json) {
          if (r.json.session && !this.session) this.session = r.json.session;
          if (this.onResp) this.onResp(r.json);
        }
      })
      .catch(() => { if (this.onStatus) this.onStatus('neterr'); })
      .finally(() => {
        this.inflight = false;
        if (this.pend) { const p = this.pend; this.pend = null; this.send(p.pts, p.t); }
      });
  }
  stop() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    if (this._fake) { try { this._fake.stop(); } catch (e) {} this._fake = null; }
    this.inflight = false; this.pend = null;
  }
}

/* ---------- session state ---------- */
const S = {
  stream: null, front: true, engine: null,
  link: new KpLink(), stT: null,
  eng: { up: false, name: '', laptopCam: false },
  seenAct: undefined, seenMatch: undefined,
  remote: false, open: false,
  mode: (() => { try { return localStorage.getItem('wf.livemode') === 'text' ? 'text' : 'control'; } catch (e) { return 'control'; } })(),
  words: [], seenEv: new Set(), evBase: false, fires: {},
};

function setBadge(cls, txt) {
  badge.className = 'lv-badge' + (cls ? ' ' + cls : '');
  badgeT.textContent = txt;
}
function renderEng() {
  engEl.textContent = S.eng.name ? ' · ' + S.eng.name : '';
}

/* ---------- overlay card ---------- */
let lastCard = null;
function card(title, msg, buttons) {
  lastCard = { title, msg, buttons };
  renderCard();
}
function renderCard() {
  const c = lastCard;
  if (!c) return;
  cardT.textContent = c.title || '';
  cardM.textContent = c.msg || '';
  cardB.textContent = '';
  const bs = typeof c.buttons === 'function' ? c.buttons() : c.buttons;
  for (const b of bs || []) if (b) cardB.appendChild(b);
  cardB.parentNode.classList.toggle('busy', !(bs || []).some(Boolean));
  cover.hidden = false;
}
function hideCard() { lastCard = null; cover.hidden = true; }
function mkBtn(label, fn, pri) {
  const el = document.createElement('button');
  el.className = 'lv-btn' + (pri ? ' pri' : '');
  el.textContent = label;
  el.onclick = ev => { ev.stopPropagation(); fn(); };
  return el;
}

/* ---------- engine probe ---------- */
async function probe() {
  const r = await App.api.get('/engine').catch(() => null);
  if (!r) { engDown(); return; }
  if (r.status === 403) return;
  if (r.status === 404 || r.status === 405) {
    S.eng.up = false;
    setBadge('err', 'no live engine');
    card('Laptop needs an update',
      'This laptop runs an older version — update it and Go Live works.',
      [mkBtn('Close', () => App.back())]);
    return;
  }
  if (!r.ok || !r.json) { engDown(); return; }
  S.eng.up = true;
  S.eng.name = r.json.engine || 'engine';
  S.eng.laptopCam = !!(r.json.laptop_cam || r.json.laptopCam ||
    /rust|kp53|webcam/i.test(S.eng.name));
  renderEng();
  if (lastCard && !cover.hidden) renderCard();
}
function engDown() {
  S.eng.up = false;
  engEl.textContent = ' · offline';
  setBadge('err', 'offline');
}

/* ---------- captions: what you signed, what it did ---------- */
const pretty = t => String(t || '').replace(/[-_]+/g, ' ').trim().replace(/^./, c => c.toUpperCase());
const hostOf = u => { try { return new URL(/^[a-z]+:/i.test(u) ? u : 'https://' + u).host.replace(/^www\./, ''); } catch (e) { return u || 'a page'; } };
/* a short, human sentence for a bound action (never argv, never ids) */
function doneText(a) {
  if (!a) return 'Done';
  const t = a.tool || a.type || '', ar = a.args || {};
  if (a.kind === 'app' || t === 'app.open' || t === 'open_app') {
    const nm = String(a.label || '').replace(/^open\s+/i, '') || ar.app || ar.id || a.id || 'the app';
    return 'Opened ' + nm;
  }
  switch (t) {
    case 'type.text': case 'type_text': return 'Typed “' + String(ar.text || '').slice(0, 36) + '”';
    case 'app.focus': case 'focus_app': return 'Switched to ' + (ar.app || a.app || 'the app');
    case 'app.close': case 'close_window': return 'Closed ' + (ar.app || a.app || 'the window');
    case 'open.url': case 'open_url': return 'Opened ' + hostOf(ar.url);
    case 'key.press': case 'key_press': return 'Pressed ' + (ar.keys || 'a key');
    case 'command': case 'run_argv': return 'Ran your command';
    case 'sys.screenshot': return 'Took a screenshot';
    case 'sys.lock': return 'Locked the screen';
    case 'macro': return 'Ran your macro';
  }
  return a.label ? pretty(a.label) : 'Done';
}

function setWords() {
  if (S.mode !== 'text') { sendBtn.hidden = true; return; }
  const txt = S.words.join(' ');
  if (txt) { guessEl.textContent = txt; guessEl.classList.remove('dim'); }
  sendBtn.hidden = !S.words.length;
}
function showMatch(m, viaPoll) {
  if (!m) return;
  const id = (m.at_ms || m.at || '') + '|' + (m.gloss || '') + '|' + (m.accepted ? 'A' : 'R');
  if (id === S.seenMatch) return;
  S.seenMatch = id;
  const label = m.label || m.gloss || '';
  if (m.mode) {                         // palm-open / palms-together events: the hint says it
    if (!viaPoll) App.pollState();
    return;
  }
  if (m.accepted && label) {
    if (S.mode === 'text') {
      S.words.push(label); setWords();
      App.emit('text:append', label);
    } else {
      guessEl.textContent = pretty(label);
      guessEl.classList.remove('dim');
      guessEl.classList.remove('pop'); void guessEl.offsetWidth; guessEl.classList.add('pop');
      const a = m.action || {};
      if (a.fired || a.routed) actFlash(a.pending ? 'Waiting for your OK on the laptop' : doneText(a));
      else if (a.routed === false) actFlash('No action on this sign yet', 'soft');
    }
    // developer detail only with ?dev
    const d = m.dist != null ? m.dist : m.d;
    distEl.textContent = d == null ? '' : 'd=' + fmt(d);
    whyEl.textContent = [m.second ? 'next: ' + m.second : '', m.reason || ''].filter(Boolean).join(' · ');
  } else if (label || m.reason) {
    actFlash('Didn’t catch that', 'soft');
    whyEl.textContent = m.reason || 'rejected';
  }
  if (!viaPoll) App.pollState();
}
function actFlash(txt, kind) {
  actT.textContent = txt;
  actEl.className = 'lv-act show' + (kind ? ' ' + kind : '');
  App.buzz();
  clearTimeout(actEl._t);
  actEl._t = setTimeout(() => actEl.classList.remove('show'), 2800);
}
/* the daemon's own event feed tells us what actually ran */
function feedEvents(j) {
  const evs = Array.isArray(j.feed) ? j.feed : null;
  if (!evs) return;
  for (const ev of evs) {
    const key = ev.type + '|' + ev.t_ms + '|' + (ev.gloss || (ev.glosses || []).join(','));
    if (S.seenEv.has(key)) continue;
    S.seenEv.add(key);
    if (ev.type === 'fire' && ev.action) for (const g of ev.glosses || []) S.fires[g] = ev.action;
    if (!S.evBase) continue;                            // first poll after opening = history, not news
    if (ev.type === 'fired' && S.mode === 'control') {
      const g = (ev.glosses || [])[0], act = S.fires[g];
      const r = ev.result || {};
      if (g) { guessEl.textContent = pretty((act && act.label) || g); guessEl.classList.remove('dim'); guessEl.classList.remove('pop'); void guessEl.offsetWidth; guessEl.classList.add('pop'); }
      if (ev.ok === false || r.fired === false) actFlash('Couldn’t run it' + (r.detail ? ': ' + r.detail : ''), 'soft');
      else actFlash(doneText(act));
    }
  }
  S.evBase = true;
  if (S.seenEv.size > 300) { const keep = [...S.seenEv].slice(-120); S.seenEv.clear(); keep.forEach(k => S.seenEv.add(k)); }
}
function renderState(j) {
  const listening = !!(j.listening || j.mode === 'listening' || j.state === 'listening');
  if (cover.hidden) setBadge(listening ? 'listen' : '', listening ? 'listening' : 'idle');
  viewEl.classList.toggle('listening', listening);
  if (j.last_match) {
    if (!S.matchBase) { S.seenMatch = (j.last_match.at_ms || j.last_match.at || '') + '|' + (j.last_match.gloss || '') + '|' + (j.last_match.accepted ? 'A' : 'R'); }
    else showMatch(j.last_match, true);
  }
  S.matchBase = true;
  feedEvents(j);
  const pend = Array.isArray(j.pending) ? j.pending : [];
  renderPending(pend);
  checkPendingHint(listening);
}
function renderPending(rows) {
  pendEl.textContent = '';
  if (!rows.length) { pendEl.hidden = true; return; }
  pendEl.hidden = false;
  for (const p of rows) {
    const row = document.createElement('div');
    row.className = 'lv-pend-row';
    const t = document.createElement('span');
    t.className = 'lv-pend-t';
    t.textContent = pretty(p.label || p.gloss || 'Action') + ' is waiting for your OK';
    const yes = mkBtn('Run it', async () => {
      const r = await App.api.post('/act/confirm', { id: p.id }).catch(() => null);
      if (r && r.ok) actFlash('Ran · ' + (p.label || p.gloss)); else App.toast('confirm failed', true);
      App.pollState();
    }, true);
    const no = mkBtn('Cancel', async () => {
      await App.api.post('/act/confirm', { id: p.id, cancel: true }).catch(() => null);
      App.pollState();
    });
    row.append(t, yes, no);
    pendEl.appendChild(row);
  }
}

/* ---------- hold-to-listen feedback (local; daemon decides the truth) ------
   palm open ~400ms / palms together ~260ms mirrors the endpointer's own
   thresholds so the bar fills right when the daemon's mode event lands. */
const GM = { palmT: 0, namT: 0, lastT: 0 };
const HOLD = 420, NHOLD = 260;
function present(pts, o) { return pts[o].score > 0.28 && pts[o + 9].score > 0.28; }
function ext(pts, o, tip, pip) {
  const w = pts[o];
  return Math.hypot(pts[o + tip].x - w.x, pts[o + tip].y - w.y) >
         Math.hypot(pts[o + pip].x - w.x, pts[o + pip].y - w.y) * 1.16;
}
function handOpen(pts, o) {
  if (!present(pts, o)) return false;
  let n = 0;
  for (const [t, p] of [[8, 6], [12, 10], [16, 14], [20, 18]]) if (ext(pts, o, t, p)) n++;
  return n >= 3;
}
function palmC(pts, o) {
  let x = 0, y = 0;
  for (const i of [0, 5, 9, 13, 17]) { x += pts[o + i].x; y += pts[o + i].y; }
  return { x: x / 5, y: y / 5 };
}
function feedHold(pts, t) {
  const dt = GM.lastT ? Math.min(300, t - GM.lastT) : 0;
  GM.lastT = t;
  const L = handOpen(pts, 0), R = handOpen(pts, 21);
  let nam = false;
  if (present(pts, 0) && present(pts, 21)) {
    const a = palmC(pts, 0), b = palmC(pts, 21);
    nam = Math.hypot(a.x - b.x, a.y - b.y) < .20;
  }
  if (nam) { GM.namT += dt; GM.palmT = 0; }
  else if (L || R) { GM.palmT += dt; GM.namT = 0; }
  else { GM.palmT = 0; GM.namT = 0; }
  holdI.style.width = Math.min(100, Math.max(GM.palmT / HOLD, GM.namT / NHOLD) * 100) + '%';
}
function checkPendingHint(listening) {
  hint.textContent = listening ? HINTS[S.mode].on : HINT_DEF;
}

/* ---------- kp response handler (matches/teach ride the POST response) ---------- */
function onKpResp(j) {
  if (j.mode) {
    const on = j.mode === 'PALM-OPEN' || /palm/i.test(j.mode);
    setBadge(on ? 'listen' : '', on ? 'listening' : 'idle');
    hint.textContent = on ? HINTS[S.mode].on : HINT_DEF;
    holdI.style.width = '0';
  }
  if (j.match) showMatch(j.match);
  if (!S.eng.up) { S.eng.up = true; }
}

/* ---------- camera ---------- */
async function boot() {
  if (S.remote) { useLaptop(); return; }
  /* dev seam first: a loopback kp42 replay service (tools/demo-rec) may be
     feeding frames. Probe is ~350 ms once; on a real phone it never
     answers and the camera path runs untouched. */
  try {
    const rep = await Pose.replay();
    if (rep) {
      S.engine = rep;
      S.front = false;                      // replayed frames aren't mirrored
      viewEl.classList.add('replay');
      tagEl.hidden = false;
      hideCard();
      setBadge('', 'idle');
      S.link.start(video, S.engine, {
        onFrame: (pts, t) => {
          Skel.draw(skel, pts, { video: null, mirror: false });
          feedHold(pts, t);
        },
        onResp: onKpResp,
        onStatus: s => { if (s === 'dead') probe(); if (s === 'denied') setBadge('err', 'unpaired'); },
      });
      return;
    }
    viewEl.classList.remove('replay'); tagEl.hidden = true;
  } catch (e) { /* probe failed — camera path */ }
  card('Allow the camera', 'Your browser is asking to use it. Video stays on this device. Only hand points are sent.');
  try {
    const c = await Pose.openCamera(video, true);
    S.stream = c.stream; S.front = c.front;
    video.classList.toggle('rear', !S.front);
  } catch (e) { camFail(e); return; }
  if (!S.open) { stopCam(); return; }
  card('Getting ready', 'Loading the hand model onto this device. One time only, then it works offline.');
  try {
    S.engine = await Pose.load(p => card('Getting ready', 'Loading the hand model onto this device. One time only.'));
  } catch (e) {
    card('Hand model unavailable', Pose.error || 'the model files did not load',
      [mkBtn('Try again', boot, true), laptopBtn()]);
    setBadge('err', 'no pose');
    return;
  }
  if (!S.open) { try { S.engine.close(); } catch (e) {} S.engine = null; return; }
  hideCard();
  setBadge('', 'idle');
  S.link.start(video, S.engine, {
    onFrame: (pts, t) => { Skel.draw(skel, pts, { video, mirror: S.front }); feedHold(pts, t); },
    onResp: onKpResp,
    onStatus: s => { if (s === 'dead') probe(); if (s === 'denied') setBadge('err', 'unpaired'); },
  });
}
function stopCam() {
  if (S.stream) for (const t of S.stream.getTracks()) t.stop();
  S.stream = null;
  video.srcObject = null;
}
function camFail(e) {
  const secure = window.isSecureContext;
  if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError'))
    card('Camera is blocked',
      'Allow camera access for this page, then try again. The video never leaves this device; only hand points do.',
      [mkBtn('Try again', boot, true), laptopBtn()]);
  else if (e && e.name === 'NoAPI' || !secure)
    card('Camera needs a secure connection',
      'Browsers only open the camera on https or localhost, and this page is plain http on your network. ' +
      'Connect over USB (wrap bridge, then open localhost:8741), use the wrap tunnel, or use the laptop camera.',
      [laptopBtn()].filter(Boolean));
  else if (e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError'))
    card('No camera found', 'This device has no usable camera.',
      [laptopBtn(), laptopBtn() ? null : mkBtn('Close', hideCard)].filter(Boolean));
  else
    card('Camera failed', (e && e.message) || 'unknown error',
      [mkBtn('Try again', boot, true), laptopBtn()]);
  setBadge('err', 'camera');
}
function canLaptop() { return S.eng.up && S.eng.laptopCam; }
function laptopBtn() {
  return canLaptop() ? mkBtn('Use the laptop camera', useLaptop, true) : null;
}
async function useLaptop() {
  S.remote = true;
  stopCam(); S.link.stop();
  if (S.engine) { try { S.engine.close(); } catch (e) {} S.engine = null; }
  card('Using the laptop camera',
    'Your laptop is watching its own webcam. Sign at your laptop; the reading below still shows what it sees.',
    [mkBtn('Back to this camera', () => { S.remote = false; boot(); })]);
  /* no source-switch endpoint exists on wfd.py today — when the rust engine
     reports a laptop cam it already owns the source; we just stop ours. */
}

/* ---------- orchestration ---------- */
async function open() {
  S.open = true;
  S.seenAct = undefined; S.seenMatch = undefined;
  S.seenEv.clear(); S.evBase = false; S.matchBase = false; S.words = []; setWords();
  guessEl.textContent = ''; guessEl.classList.add('dim'); actEl.classList.remove('show');
  document.body.classList.add('live-open');
  setMode(S.mode, false);
  S.link.reset();
  hideCard();
  probe();
  boot();
  clearInterval(S.stT);
  S.stT = setInterval(() => {
    App.api.get('/live/state').then(r => {
      if (r && r.ok && r.json) renderState(r.json);
    }).catch(() => {});
  }, ST_MS);
  App.api.get('/live/state').then(r => { if (r && r.ok && r.json) renderState(r.json); }).catch(() => {});
}
async function close() {
  S.open = false;
  document.body.classList.remove('live-open');
  // text mode sets the daemon to observe-only: never leave it that way
  if (S.mode === 'text') App.api.post('/live/mode', { mode: 'live' }).catch(() => {});
  S.link.stop();
  clearInterval(S.stT); S.stT = null;
  if (S.engine) { try { S.engine.close(); } catch (e) {} S.engine = null; }
  stopCam();
  GM.palmT = GM.namT = GM.lastT = 0;
  S.remote = false;
  hideCard();
  const x = skel.getContext('2d');
  if (x) x.clearRect(0, 0, skel.width, skel.height);
  setBadge('', 'off');
  hint.textContent = HINT_DEF;
  holdI.style.width = '0';
  whyEl.textContent = '';
}

/* ---------- Text | Control ---------- */
function setMode(m, persist = true) {
  S.mode = m === 'text' ? 'text' : 'control';
  if (persist) { try { localStorage.setItem('wf.livemode', S.mode); } catch (e) {} }
  segText.setAttribute('aria-selected', S.mode === 'text' ? 'true' : 'false');
  segCtl.setAttribute('aria-selected', S.mode === 'control' ? 'true' : 'false');
  viewEl.classList.toggle('mode-text', S.mode === 'text');
  HINT_DEF = HINTS[S.mode].idle;
  hint.textContent = viewEl.classList.contains('listening') ? HINTS[S.mode].on : HINT_DEF;
  if (S.mode !== 'text') { S.words = []; setWords(); }
  // Control: signs fire real actions. Text: observe only (match, never execute); each sign becomes a word.
  App.api.post('/live/mode', { mode: S.mode === 'text' ? 'observe' : 'live' }).catch(() => {});
  if (persist) { guessEl.textContent = ''; guessEl.classList.add('dim'); actEl.classList.remove('show'); }
}
segText.onclick = () => setMode('text');
segCtl.onclick = () => setMode('control');

/* ---------- flip camera ---------- */
let flipping = false;
flipBtn.onclick = async () => {
  if (flipping || S.remote || !S.stream) return;
  flipping = true;
  try {
    const want = !S.front;
    for (const t of S.stream.getTracks()) t.stop();
    const c = await Pose.openCamera(video, want);
    S.stream = c.stream; S.front = c.front;
    video.classList.toggle('rear', !S.front);
  } catch (e) {
    App.toast('No other camera on this device', true);
    try { const c = await Pose.openCamera(video, S.front); S.stream = c.stream; S.front = c.front; video.classList.toggle('rear', !S.front); } catch (e2) { camFail(e2); }
  } finally { flipping = false; }
};

/* Text mode: send what you have signed to the laptop (the composer owns the text) */
sendBtn.onclick = () => { App.emit('text:send'); };
App.on('sent', r => {
  if (!S.open) return;
  if (r && r.ok) { S.words = []; setWords(); guessEl.textContent = ''; guessEl.classList.add('dim'); actFlash('Sent to ' + (r.into || 'your laptop')); }
  else actFlash('Couldn’t send', 'soft');
});

xbtn.onclick = () => App.back();
addEventListener('keydown', e => { if (e.key === 'Escape' && S.open) App.back(); });
addEventListener('pagehide', () => { if (S.open) close(); });
addEventListener('pageshow', e => {
  if (e.persisted && S.open && !S.remote && (!S.stream || !S.stream.active)) boot();
});

App.route('live', { el: sheet, open, close });
