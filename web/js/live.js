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
  <div class="sheet-head">
    <h1>live<span class="lv-eng" id="lv-eng"></span></h1>
    <div class="lv-badge" id="lv-badge"><i></i><span id="lv-badge-t">off</span></div>
    <button class="sheet-x" id="lv-x" aria-label="Close live mode">×</button>
  </div>
  <div class="sheet-body lv-body">
    <div class="lv-view" id="lv-view">
      <video id="lv-cam" playsinline muted autoplay></video>
      <canvas id="lv-skel"></canvas>
      <div class="lv-cover" id="lv-cover" hidden>
        <div class="lv-card">
          <div class="lv-card-t" id="lv-card-t"></div>
          <div class="lv-card-m" id="lv-card-m"></div>
          <div class="lv-card-b" id="lv-card-b"></div>
        </div>
      </div>
    </div>
    <div class="lv-read">
      <span class="lv-rlab">reading</span>
      <span class="lv-rguess dim" id="lv-guess">—</span>
      <span class="lv-rdist" id="lv-dist"></span>
    </div>
    <div class="lv-why" id="lv-why"></div>
    <div class="lv-act" id="lv-act"><i></i><span id="lv-act-t"></span></div>
    <div class="lv-pend" id="lv-pend" hidden></div>
    <div class="lv-hold">
      <div class="lv-hold-bar"><i id="lv-hold-i"></i></div>
      <div class="lv-hint" id="lv-hint">hold an open palm to listen · palms together to stop</div>
    </div>
  </div>`;
const $ = id => sheet.querySelector('#' + id);
const video = $('lv-cam'), skel = $('lv-skel'), cover = $('lv-cover'),
      cardT = $('lv-card-t'), cardM = $('lv-card-m'), cardB = $('lv-card-b'),
      engEl = $('lv-eng'), badge = $('lv-badge'), badgeT = $('lv-badge-t'),
      guessEl = $('lv-guess'), distEl = $('lv-dist'), whyEl = $('lv-why'),
      actEl = $('lv-act'), actT = $('lv-act-t'), pendEl = $('lv-pend'),
      holdI = $('lv-hold-i'), hint = $('lv-hint'), xbtn = $('lv-x'),
      viewEl = $('lv-view');

const HINT_DEF = 'hold an open palm to listen · palms together to stop';
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
    const body = { t_ms: Math.round(t), points: pts };
    if (this.session) body.session = this.session;
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
    card('daemon has no live engine',
      'this wfd predates the merge — update the laptop daemon (git pull && npm run wrap) and live mode comes up.',
      [mkBtn('close', () => App.back())]);
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

/* ---------- reading strip ---------- */
const fmt = v => (typeof v === 'number' ? +v.toFixed(3) : v);
function showMatch(m, viaPoll) {
  if (!m) return;
  const id = (m.at_ms || m.at || '') + '|' + (m.gloss || '') + '|' + (m.accepted ? 'A' : 'R');
  if (id === S.seenMatch) return;
  S.seenMatch = id;
  const label = m.label || m.gloss || '';
  if (m.accepted && label) {
    guessEl.textContent = label;
    guessEl.classList.remove('dim');
    const d = m.dist != null ? m.dist : m.d;
    distEl.textContent = d == null ? '' : 'd=' + fmt(d);
    const bits = [];
    if (m.second) bits.push('next: ' + m.second + (m.margin != null ? ' +' + fmt(m.margin) : ''));
    if (m.reason) bits.push(m.reason);
    whyEl.textContent = bits.join(' · ');
    // last-action flash comes from the same row — pending/routed/fired
    const a = m.action || {};
    if (m.mode) {
      actFlash(m.label === 'listening' ? 'mode · listening' : 'mode · ' + (m.label || m.gloss));
    } else if (a.fired || a.routed) {
      actFlash('fired · ' + (a.label || m.label || m.gloss) + (a.pending ? ' — held for confirm' : ''));
    } else if (a.routed === false && !m.mode) {
      actFlash('matched · ' + (m.label || m.gloss) + ' — no action bound');
    }
  } else if (label || m.reason) {
    guessEl.textContent = label || '—';
    guessEl.classList.add('dim');
    const d = m.dist != null ? m.dist : m.d;
    distEl.textContent = d == null ? '' : 'd=' + fmt(d);
    whyEl.textContent = m.reason || 'rejected';
  }
  if (!viaPoll) App.pollState();
}
function actFlash(txt) {
  actT.textContent = txt;
  actEl.classList.add('show');
  App.buzz();
  clearTimeout(actEl._t);
  actEl._t = setTimeout(() => actEl.classList.remove('show'), 2600);
}
function renderState(j) {
  const listening = !!(j.listening || j.mode === 'listening' || j.state === 'listening');
  setBadge(listening ? 'listen' : '', listening ? 'listening' : 'idle');
  if (j.last_match) showMatch(j.last_match, true);
  const pend = Array.isArray(j.pending) ? j.pending : [];
  renderPending(pend);
  if (!listening && !S.seenMatch) { guessEl.textContent = '—'; guessEl.classList.add('dim'); distEl.textContent = ''; whyEl.textContent = ''; }
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
    t.textContent = (p.label || p.gloss || 'action') + ' held for confirm';
    const yes = mkBtn('run', async () => {
      const r = await App.api.post('/act/confirm', { id: p.id }).catch(() => null);
      if (r && r.ok) actFlash('fired · ' + (p.label || p.gloss)); else App.toast('confirm failed', true);
      App.pollState();
    }, true);
    const no = mkBtn('cancel', async () => {
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
  hint.textContent = listening ? 'listening — sign a command' : HINT_DEF;
}

/* ---------- kp response handler (matches/teach ride the POST response) ---------- */
function onKpResp(j) {
  if (j.mode) {
    const on = j.mode === 'PALM-OPEN' || /palm/i.test(j.mode);
    setBadge(on ? 'listen' : '', on ? 'listening' : 'idle');
    hint.textContent = on ? 'listening — sign a command' : HINT_DEF;
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
      let tag = viewEl.querySelector('.lv-replay');
      if (!tag) {
        tag = document.createElement('div');
        tag.className = 'lv-replay';
        viewEl.appendChild(tag);
      }
      tag.textContent = 'replayed keypoints · devtools';
      tag.hidden = false;
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
    viewEl.classList.remove('replay');
  } catch (e) { /* probe failed — camera path */ }
  card('camera', 'asking for the camera…');
  try {
    const c = await Pose.openCamera(video, true);
    S.stream = c.stream; S.front = c.front;
    video.classList.toggle('rear', !S.front);
  } catch (e) { camFail(e); return; }
  if (!S.open) { stopCam(); return; }
  card('pose', 'loading the on-device pose model… (one-time, ~34 MB vendored — then fully offline)');
  try {
    S.engine = await Pose.load(p => card('pose', (p.detail || p.phase) + '…'));
  } catch (e) {
    card('pose model unavailable', Pose.error || 'the vendored mediapipe files did not load',
      [mkBtn('retry', boot, true), laptopBtn()]);
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
    card('camera blocked',
      'grant camera access for this page, then retry — the video never leaves this device, only keypoints do.',
      [mkBtn('retry', boot, true), laptopBtn()]);
  else if (e && e.name === 'NoAPI' || !secure)
    card('camera needs a secure context',
      'browsers only open the camera on https or localhost — this page is plain http on the LAN. ' +
      'pair over usb (adb reverse tcp:8741 + open localhost:8741) or the wrap tunnel, or use the laptop camera.',
      [laptopBtn()].filter(Boolean));
  else if (e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError'))
    card('no camera here', 'this device has no usable camera.', [laptopBtn()].filter(Boolean));
  else
    card('camera failed', (e && e.message) || 'unknown error',
      [mkBtn('retry', boot, true), laptopBtn()]);
  setBadge('err', 'camera');
}
function canLaptop() { return S.eng.up && S.eng.laptopCam; }
function laptopBtn() {
  return canLaptop() ? mkBtn('use the laptop camera', useLaptop, true) : null;
}
async function useLaptop() {
  S.remote = true;
  stopCam(); S.link.stop();
  if (S.engine) { try { S.engine.close(); } catch (e) {} S.engine = null; }
  card('laptop camera',
    'the laptop is watching its own webcam — sign at your laptop. ' +
    'the reading strip still shows what the engine sees.',
    [mkBtn('back to phone camera', () => { S.remote = false; boot(); })]);
  /* no source-switch endpoint exists on wfd.py today — when the rust engine
     reports a laptop cam it already owns the source; we just stop ours. */
}

/* ---------- orchestration ---------- */
async function open() {
  S.open = true;
  S.seenAct = undefined; S.seenMatch = undefined;
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

xbtn.onclick = () => App.back();
addEventListener('keydown', e => { if (e.key === 'Escape' && S.open) App.back(); });
addEventListener('pagehide', () => { if (S.open) close(); });
addEventListener('pageshow', e => {
  if (e.persisted && S.open && !S.remote && (!S.stream || !S.stream.active)) boot();
});

App.route('live', { el: sheet, open, close });
