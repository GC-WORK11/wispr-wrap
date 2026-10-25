/* live.js — LIVE mode for the composer.
   ◉ LIVE opens a bottom sheet: camera runs in-page (video never leaves the
   device), the vendored MediaPipe bundle turns frames into 42 hand keypoints,
   and only the keypoints cross the wire — ~1 KB per frame.

   kp42 layout: left hand landmarks [0:21], right hand [21:42] in MediaPipe
   order (wrist, thumb 1-4, index 5-8, middle 9-12, ring 13-16, pinky 17-20),
   normalized x,y + score; an absent hand is 21 points of {x:0,y:0,score:0}.
   The skeleton overlay is drawn from the exact array being POSTed (WYSIWYS).

   Wire contract (daemon lane; all POSTs carry X-WF-Key, polling only — no ws):
     POST /engine (GET fallback) -> {engine, laptop_cam?, ...}   engine probe
     POST /live/kp               {t_ms, points[42]}              keypoint frame, ~12Hz
     POST /live/ping             {t_ms}                          500ms heartbeat while live
     POST /live/event            {type:'palm_open'|'namaste'|'source', t_ms, source?}
     GET  /live/state            {listening|mode, guess:{label|gloss,distance|score},
                                  last_action:{label|gloss,at}, engine?}
   Endpoints that 404/405 are marked dead for the session — today's daemon
   predates them and the sheet degrades honestly instead of spamming. */
(() => {
'use strict';
const $ = id => document.getElementById(id);
const btn = $('livebtn'), scrim = $('lv-scrim'), sheet = $('lv-sheet'),
      video = $('lv-cam'), skel = $('lv-skel'),
      cover = $('lv-cover'), cardT = $('lv-card-t'), cardM = $('lv-card-m'), cardB = $('lv-card-b'),
      engEl = $('lv-eng'), badge = $('lv-badge'), badgeT = $('lv-badge-t'),
      guessEl = $('lv-guess'), distEl = $('lv-dist'),
      actEl = $('lv-act'), actT = $('lv-act-t'),
      holdI = $('lv-hold-i'), hint = $('lv-hint'), xbtn = $('lv-x');
if (!btn || !sheet) return;

// the composer's inline script already folded ?k= into storage — reuse, never re-ask
const KEY = (typeof K !== 'undefined' && K) ||
            (() => { try { return localStorage.getItem('wfk'); } catch (e) { return null; } })() || '';
const AH = { 'X-WF-Key': KEY };
const JH = { 'Content-Type': 'application/json', 'X-WF-Key': KEY };
const say  = (typeof flash === 'function') ? flash : () => {};
const vibe = (typeof buzz  === 'function') ? buzz  : () => {};

const HINT_DEF = 'hold an open palm to listen · palms together to stop';
const KP_MS = 83;        // ~12Hz keypoint frames (spec: 10-15Hz)
const ST_MS = 167;       // ~6Hz state poll
const HB_MS = 500;       // heartbeat while live

/* ---------- sheet css: fetch+inject (wfd serves .css as octet-stream) ---------- */
let cssP = null;
function css() {
  if (cssP) return cssP;
  cssP = fetch('live.css').then(r => r.ok ? r.text() : Promise.reject(r.status))
    .then(t => {
      const s = document.createElement('style');
      s.dataset.lv = '1'; s.textContent = t;
      document.head.appendChild(s);
    }).catch(() => { cssP = null; });
  return cssP;
}
css();   // warm at load — the sheet opens already styled

/* ---------- session state ---------- */
const S = {
  open: false, remote: false,
  stream: null, front: true, pose: null,
  eng: { up: false, name: '', laptopCam: false },
  kpT: null, stT: null, hbT: null,
  kpDead: false, stDead: false, hbDead: false,
  inflight: false, pend: null,
  seenAct: undefined, unpaired: false,
};

/* ---------- pose providers: vendored only, feature-detected ----------
   Each provider owns ./vendor/mediapipe/ and returns
     {name, close(), detect(video,tMs) -> [{side:'Left'|'Right', pts:[{x,y,score}*21]}]}
   detection is fetch-probe first (a 404 fetch is console-silent; a missing
   import()/script src is not — keep the console clean for judges).
   Vendored today (tools/fetch-vendor.sh): vision_bundle.mjs + wasm/ +
   models/hand_landmarker.task. Add a 'pose-landmarker' entry here later for
   MediaPipe Pose-Landmarker-lite: same interface, slice hand indices into kp42. */
/* load a vendored js file even when the static server sends the wrong MIME —
   wfd replies octet-stream for .mjs/.js today, which hard-fails import() and
   warns <script src>. fetch it (the probe already did) and run it via blob URL. */
async function blobModule(resp) {
  const src = await resp.text();
  const u = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
  try { return await import(u); } finally { URL.revokeObjectURL(u); }
}
async function blobScript(resp) {
  const src = await resp.text();
  const u = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
  return new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = u;
    s.onload = () => { URL.revokeObjectURL(u); res(); };
    s.onerror = () => { URL.revokeObjectURL(u); rej(new Error('script load failed')); };
    document.head.appendChild(s);
  });
}

const PROVIDERS = [
  { id: 'tasks-hands', entry: 'vision_bundle.mjs',
    async load(dir, resp) {
      const m = await blobModule(resp);
      const R = m.FilesetResolver, L = m.HandLandmarker;
      if (!R || !L) throw new Error('tasks-vision exports missing');
      const fs = await R.forVisionTasks(dir + 'wasm');
      let lm = null, err = null;
      for (const d of ['GPU', 'CPU']) {
        try {
          lm = await L.createFromOptions(fs, {
            baseOptions: { modelAssetPath: dir + 'models/hand_landmarker.task', delegate: d },
            runningMode: 'VIDEO', numHands: 2,
            minHandDetectionConfidence: .4, minHandPresenceConfidence: .4,
            minTrackingConfidence: .4 });
          break;
        } catch (e) { err = e; }
      }
      if (!lm) throw err || new Error('hand_landmarker failed');
      let last = -1;
      return { name: 'handlandmarker',
        close() { try { lm.close(); } catch (e) {} },
        detect(v, t) {
          if (t === last) return [];
          last = t;
          const r = lm.detectForVideo(v, t), out = [];
          const lms = r.landmarks || [], hds = r.handednesses || r.handedness || [];
          for (let i = 0; i < lms.length; i++) {
            const h = hds[i];
            const side = (h && (h.categoryName || (h[0] && h[0].categoryName) || h.label)) || 'Left';
            out.push({ side, pts: lms[i].map(p => ({ x: p.x, y: p.y,
              score: p.visibility == null ? 1 : p.visibility })) });
          }
          return out;
        } };
    } },
  { id: 'classic-hands', entry: 'hands.js',
    async load(dir, resp) {
      await blobScript(resp);
      if (!window.Hands) throw new Error('Hands global missing');
      const h = new window.Hands({ locateFile: f => dir + f });
      h.setOptions({ maxNumHands: 2, modelComplexity: 1,
        minDetectionConfidence: .5, minTrackingConfidence: .5 });
      let q = null;
      h.onResults(r => { const p = q; q = null; if (p) p(r); });
      return { name: 'mp-hands',
        close() { try { h.close(); } catch (e) {} },
        detect() {
          if (q) return Promise.resolve(null);   // busy — drop this frame
          return new Promise(res => {
            q = res;
            h.send({ image: video }).catch(() => { if (q) { q = null; res(null); } });
          }).then(r => {
            const out = [], lms = (r && r.multiHandLandmarks) || [],
                        hds = (r && r.multiHandedness) || [];
            for (let i = 0; i < lms.length; i++) {
              const side = (hds[i] && hds[i].label) || 'Left';
              out.push({ side, pts: lms[i].map(p => ({ x: p.x, y: p.y,
                score: p.visibility == null ? 1 : p.visibility })) });
            }
            return out;
          });
        } };
    } },
];

async function loadPose() {
  const dir = './vendor/mediapipe/';
  let man = null;
  try {                                    // optional vendor manifest wins
    const r = await fetch(dir + 'manifest.json', { cache: 'no-store' });
    if (r.ok) man = await r.json();
  } catch (e) {}
  const order = [];
  if (man && man.provider) {
    const p = PROVIDERS.find(p => p.id === man.provider);
    if (p) order.push({ p, entry: man.entry || p.entry });
  }
  for (const p of PROVIDERS)
    if (!order.find(o => o.p === p)) order.push({ p, entry: p.entry });
  for (const { p, entry } of order) {
    try {
      const probe = await fetch(dir + entry, { cache: 'no-store' });   // silent probe
      if (!probe.ok) continue;
      const pose = await p.load(dir, probe);
      if (pose) return pose;
    } catch (e) { /* next provider */ }
  }
  return null;
}

/* ---------- kp42 pack: left[0:21], right[21:42], absent = score 0 ---------- */
const ZP = { x: 0, y: 0, score: 0 };
function pack42(hands) {
  const pts = new Array(42);
  for (let i = 0; i < 42; i++) pts[i] = ZP;
  for (const h of hands || []) {
    if (!h || !h.pts) continue;
    const off = /^l/i.test(h.side || '') ? 0 : 21;
    for (let i = 0; i < 21 && i < h.pts.length; i++) {
      const p = h.pts[i];
      pts[off + i] = { x: +(+p.x).toFixed(4), y: +(+p.y).toFixed(4),
                       score: +(p.score == null ? 1 : p.score).toFixed(3) };
    }
  }
  return pts;
}

/* ---------- skeleton: drawn from the exact posted array ---------- */
const EDGES = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],
               [10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[17,18],
               [18,19],[19,20],[0,17]];
function draw(pts) {
  const x = skel.getContext('2d');
  if (!x) return;
  const W = skel.clientWidth, H = skel.clientHeight, dpr = devicePixelRatio || 1;
  if (skel.width !== Math.round(W * dpr) || skel.height !== Math.round(H * dpr)) {
    skel.width = Math.round(W * dpr); skel.height = Math.round(H * dpr);
  }
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, W, H);
  const vw = video.videoWidth || 0, vh = video.videoHeight || 0;
  if (!vw || !vh) return;
  // object-fit:cover mapping + selfie mirror so points land exactly on the image
  const s = Math.max(W / vw, H / vh), w = vw * s, h = vh * s,
        ox = (W - w) / 2, oy = (H - h) / 2, m = S.front;
  const P = p => [ox + (m ? 1 - p.x : p.x) * w, oy + p.y * h];
  x.lineWidth = 1.6; x.lineCap = 'round';
  x.strokeStyle = 'rgba(74,125,255,.92)'; x.fillStyle = 'rgba(74,125,255,.92)';
  for (const off of [0, 21]) {
    if (!present(pts, off)) continue;
    x.beginPath();
    for (const [a, b] of EDGES) {
      const A = pts[off + a], B = pts[off + b];
      if (A.score <= 0 || B.score <= 0) continue;
      const [ax, ay] = P(A), [bx, by] = P(B);
      x.moveTo(ax, ay); x.lineTo(bx, by);
    }
    x.stroke();
    for (let i = 0; i < 21; i++) {
      const p = pts[off + i];
      if (p.score <= 0) continue;
      const [px, py] = P(p);
      x.beginPath(); x.arc(px, py, i === 0 ? 3 : 2.2, 0, 6.2832); x.fill();
    }
  }
}

/* ---------- hold-to-listen state machine (in-page, on the posted points) ------
   palm open ~400ms -> 'palm_open' event (ask the engine to listen)
   palms together ~260ms -> 'namaste' event (ask it to stop)
   these are mode EVENTS, not text: we fire once per gesture, show a hold
   progress, and let /live/state be the source of truth — if the engine never
   confirms, the hint says so honestly. */
const GM = { palmT: 0, namT: 0, armed: { palm: true, nam: true },
             pend: null, lastT: 0, failAt: 0 };
const HOLD = 420, NHOLD = 260;
function present(pts, o) { return pts[o].score > 0 && pts[o + 9].score > 0; }
function ext(pts, o, tip, pip) {
  const w = pts[o];
  return Math.hypot(pts[o + tip].x - w.x, pts[o + tip].y - w.y) >
         Math.hypot(pts[o + pip].x - w.x, pts[o + pip].y - w.y) * 1.16;
}
function handOpen(pts, o) {
  if (!present(pts, o)) return false;
  let n = 0;
  for (const [t, p] of [[8,6],[12,10],[16,14],[20,18]]) if (ext(pts, o, t, p)) n++;
  return n >= 3;
}
function palmC(pts, o) {
  let x = 0, y = 0;
  for (const i of [0, 5, 9, 13, 17]) { x += pts[o + i].x; y += pts[o + i].y; }
  return { x: x / 5, y: y / 5 };
}
function feed(pts, t) {
  const dt = GM.lastT ? Math.min(300, t - GM.lastT) : 0;
  GM.lastT = t;
  const L = handOpen(pts, 0), R = handOpen(pts, 21);
  if (L || R) { GM.palmT += dt; } else { GM.palmT = 0; GM.armed.palm = true; }
  let nam = false;
  if (present(pts, 0) && present(pts, 21)) {
    const a = palmC(pts, 0), b = palmC(pts, 21);
    nam = Math.hypot(a.x - b.x, a.y - b.y) < .20;
  }
  if (nam) { GM.namT += dt; GM.palmT = 0; } else { GM.namT = 0; GM.armed.nam = true; }
  holdI.style.width = Math.min(100, Math.max(GM.palmT / HOLD, GM.namT / NHOLD) * 100) + '%';
  if (GM.armed.palm && GM.palmT >= HOLD) { GM.armed.palm = false; fire('palm_open'); }
  if (GM.armed.nam  && GM.namT  >= NHOLD) { GM.armed.nam = false;  fire('namaste'); }
}
function fire(type) {
  GM.pend = { type, at: performance.now() };
  hint.textContent = type === 'palm_open'
    ? 'palm held — asking the engine to listen…'
    : 'palms together — asking the engine to stop…';
  fetch('/live/event', { method: 'POST', headers: JH,
    body: JSON.stringify({ type, t_ms: Math.round(performance.now()) }) })
    .then(r => { if (r.status === 403) unpaired(); }).catch(() => {});
}
function checkPending(listening) {
  const p = GM.pend;
  if (!p) return;
  const want = p.type === 'palm_open' ? listening : !listening;
  if (want) { GM.pend = null; GM.failAt = 0; }
  else if (performance.now() - p.at > 1700) {
    GM.pend = null;
    GM.failAt = performance.now();
    hint.textContent = p.type === 'palm_open'
      ? "engine didn't start listening — teach it a palm-open sign, or check the daemon"
      : "engine didn't confirm the stop — check the daemon";
  }
}

/* ---------- camera ---------- */
async function startCam() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
    throw Object.assign(new Error('no camera api'), { name: 'NoAPI' });
  let err = null;
  for (const c of [
    { video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
    { video: { facingMode: 'user' }, audio: false },
    { video: true, audio: false }]) {
    try {
      S.stream = await navigator.mediaDevices.getUserMedia(c);
      const tr = S.stream.getVideoTracks()[0],
            st = tr && tr.getSettings ? tr.getSettings() : {};
      // mirror the preview like every front cam; rear stays unmirrored
      S.front = st.facingMode ? st.facingMode !== 'environment' : true;
      video.classList.toggle('rear', !S.front);
      video.srcObject = S.stream;
      await video.play().catch(() => {});
      return;
    } catch (e) {
      err = e;
      if (e.name === 'NotAllowedError' || e.name === 'SecurityError') throw e;
    }
  }
  throw err || new Error('getUserMedia failed');
}
function stopCam() {
  if (S.stream) for (const t of S.stream.getTracks()) t.stop();
  S.stream = null;
  if (video) video.srcObject = null;
}

/* ---------- overlay card ---------- */
function card(title, msg, buttons) {
  S.lastCard = { title, msg, buttons };
  renderCard();
}
function renderCard() {
  const c = S.lastCard;
  if (!c) return;
  cardT.textContent = c.title || '';
  cardM.textContent = c.msg || '';
  cardB.textContent = '';
  const bs = typeof c.buttons === 'function' ? c.buttons() : c.buttons;
  for (const b of bs || []) if (b) cardB.appendChild(b);
  cover.hidden = false;
}
function b(label, fn, pri) {
  const el = document.createElement('button');
  el.className = 'lv-btn' + (pri ? ' pri' : '');
  el.textContent = label;
  el.onclick = ev => { ev.stopPropagation(); fn(); };
  return el;
}
function canLaptop() { return S.eng.up && S.eng.laptopCam; }
function laptopBtn() {
  return canLaptop() ? b('use the laptop camera', useLaptop, true) : null;
}

/* ---------- engine probe / state poll / heartbeat / kp stream ---------- */
async function probe() {
  for (const m of ['POST', 'GET']) {
    try {
      const r = await fetch('/engine', { method: m, cache: 'no-store',
        headers: m === 'POST' ? JH : AH, body: m === 'POST' ? '{}' : undefined });
      if (r.status === 403) { unpaired(); return; }
      if (!r.ok) continue;
      const j = await r.json().catch(() => ({}));
      S.eng.up = true;
      S.eng.name = j.engine || j.name || j.kind || '';
      S.eng.laptopCam = !!(j.laptop_cam || j.laptopCam || j.cam || j.camera ||
        /rust|kp53|webcam/i.test(S.eng.name || ''));
      renderEng();
      return;
    } catch (e) { engDown(); return; }
  }
  engDown();
}
function renderEng() {
  engEl.textContent = S.eng.up ? (S.eng.name || 'engine') : 'offline';
  engEl.style.color = S.eng.up ? '' : '#b0483f';
  if (S.lastCard && !cover.hidden) renderCard();   // engine probe may add a laptop-cam button
}
function setBadge(cls, txt) {
  badge.className = 'lv-badge' + (cls ? ' ' + cls : '');
  badgeT.textContent = txt;
}
function engDown() {
  S.eng.up = false;
  renderEng();
  setBadge('err', 'offline');
  guessEl.textContent = '—'; guessEl.classList.add('dim'); distEl.textContent = '';
}
function unpaired() {
  S.unpaired = true;
  if (S.kpT) { clearInterval(S.kpT); S.kpT = null; }
  setBadge('err', 'unpaired');
  say('unpaired — rescan QR');
  try { window.WF && window.WF.unpair && window.WF.unpair(); } catch (e) {}
}
function engDead(which) {   // 404/405 — this daemon predates live mode; stop hammering it
  if (which === 'kp' && S.kpT) { clearInterval(S.kpT); S.kpT = null; S.kpDead = true; }
  if (which === 'state') { S.stDead = true; if (S.stT) { clearInterval(S.stT); S.stT = null; } }
  setBadge('err', 'no live engine');
  card('daemon has no live engine',
    'this wfd predates the merge — update the laptop daemon (wrap start) and live mode comes up.',
    [b('close', closeLive)]);
}

async function poll() {
  if (!S.open || S.stDead || S.unpaired) return;
  try {
    const r = await fetch('/live/state', { headers: AH, cache: 'no-store' });
    if (r.status === 403) return unpaired();
    if (r.status === 404 || r.status === 405) return engDead('state');
    if (!r.ok) return;
    S.eng.up = true; renderEng();
    renderState(await r.json());
  } catch (e) { engDown(); }
}
const fmt = v => (typeof v === 'number' ? +v.toFixed(3) : v);
function renderState(j) {
  const listening = !!(j.listening || j.listening === 1 ||
                       j.mode === 'listening' || j.state === 'listening');
  setBadge(listening ? 'listen' : '', listening ? 'listening' : 'idle');
  if (j.engine && !S.eng.name) { S.eng.name = j.engine; renderEng(); }
  const g = j.guess || j.top || j.match || null;
  const glabel = g && (g.label || g.gloss || g.name);
  if (glabel) {
    guessEl.textContent = glabel;
    guessEl.classList.remove('dim');
    const isD = g.distance != null || g.d != null;
    const d = isD ? (g.distance != null ? g.distance : g.d)
                  : (g.score != null ? g.score : g.conf);
    distEl.textContent = d == null ? '' : (isD ? 'd=' + fmt(d) : 'conf ' + fmt(d));
  } else {
    guessEl.textContent = listening ? '…' : '—';
    guessEl.classList.add('dim');
    distEl.textContent = '';
  }
  const a = j.last_action || j.action || j.fired || null;
  const aid = a && ((a.at || a.t_ms || a.ts || '') + '|' +
                    (a.label || a.gloss || a.name || a.argv || ''));
  if (aid && aid !== S.seenAct) {
    if (S.seenAct !== undefined) {   // first sight just primes dedupe — no fake flash
      actT.textContent = 'fired · ' + (a.label || a.gloss || a.name || a.argv || 'action');
      actEl.classList.add('show');
      vibe();
      clearTimeout(actEl._t);
      actEl._t = setTimeout(() => actEl.classList.remove('show'), 2400);
    }
    S.seenAct = aid;
  }
  checkPending(listening);
  if (!GM.pend && !(GM.failAt && performance.now() - GM.failAt < 3500))
    hint.textContent = listening ? 'listening — sign a command' : HINT_DEF;
}

/* kp frames: ~12Hz, latest-wins (a slow network drops frames, never queues them) */
function tick() {
  if (!S.open || !S.pose || !S.stream || video.readyState < 2) return;
  const t = performance.now();
  let res;
  try { res = S.pose.detect(video, t); } catch (e) { return; }
  Promise.resolve(res).then(hands => {
    if (!S.open || !hands) return;
    const pts = pack42(hands);
    feed(pts, t);
    sendKp(pts, t);
  }).catch(() => {});
}
function sendKp(pts, t) {
  if (S.inflight) { S.pend = { pts, t }; return; }
  S.inflight = true;
  draw(pts);                              // canvas shows exactly what was posted
  fetch('/live/kp', { method: 'POST', headers: JH, keepalive: true,
    body: JSON.stringify({ t_ms: Math.round(t), points: pts }) })
    .then(r => {
      if (r.status === 403) unpaired();
      else if (r.status === 404 || r.status === 405) engDead('kp');
      else if (r.ok && !S.eng.up) { S.eng.up = true; renderEng(); setBadge('', 'idle'); }
    })
    .catch(() => engDown())
    .finally(() => {
      S.inflight = false;
      if (S.open && S.pose && S.pend && !S.unpaired) {
        const p = S.pend; S.pend = null; sendKp(p.pts, p.t);
      }
    });
}
function startHb() {
  if (S.hbDead) return;
  clearInterval(S.hbT);
  S.hbT = setInterval(() => {
    if (!S.open || S.unpaired) return;
    fetch('/live/ping', { method: 'POST', headers: JH, keepalive: true,
      body: JSON.stringify({ t_ms: Math.round(performance.now()) }) })
      .then(r => {
        if (r.status === 404 || r.status === 405) {
          S.hbDead = true; clearInterval(S.hbT); S.hbT = null;
        } else if (r.status === 403) unpaired();
      }).catch(() => {});
  }, HB_MS);
}

/* ---------- degraded paths ---------- */
function camFail(e) {
  if (e && (e.name === 'NotAllowedError' || e.name === 'SecurityError'))
    card('camera blocked',
      'grant camera access for this page, then retry — the video never leaves this device, only keypoints do.',
      () => [b('retry', boot, true), laptopBtn()]);
  else if (e && e.name === 'NoAPI')
    card('camera needs a secure context',
      'browsers only open the camera on https or localhost — this page is plain http. ' +
      'pair over usb (wrap bridge) or the wrap tunnel, or use the laptop camera.',
      () => [laptopBtn()].filter(Boolean));
  else if (e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError'))
    card('no camera here', 'this device has no usable camera.',
      () => [laptopBtn()].filter(Boolean));
  else
    card('camera failed', (e && e.message) || 'unknown error',
      () => [b('retry', boot, true), laptopBtn()]);
  setBadge('err', 'camera');
}

async function useLaptop() {
  S.remote = true;
  stopCam();
  if (S.pose && S.pose.close) { try { S.pose.close(); } catch (e) {} }
  S.pose = null;
  if (S.kpT) { clearInterval(S.kpT); S.kpT = null; }
  const x = skel.getContext('2d');
  if (x) x.clearRect(0, 0, skel.width, skel.height);
  card('laptop camera',
    'asking the daemon to watch the laptop webcam — sign at your laptop. ' +
    'the reading strip below still shows what the engine sees.',
    [b('back to phone camera', () => { S.remote = false; boot(); })]);
  try {
    const r = await fetch('/live/event', { method: 'POST', headers: JH,
      body: JSON.stringify({ type: 'source', source: 'laptop',
                             t_ms: Math.round(performance.now()) }) });
    if (r.status === 403) unpaired();
    else if (r.status === 404 || r.status === 405) {
      S.lastCard.msg += ' (this daemon has no source switch yet — it decides the source itself.)';
      renderCard();
    }
  } catch (e) {}
}

/* ---------- orchestration ---------- */
async function boot() {
  if (S.remote) { useLaptop(); return; }
  card('camera', 'asking for the camera…');
  try { await startCam(); }
  catch (e) { camFail(e); return; }
  cover.hidden = true;
  if (!S.open) { stopCam(); return; }
  card('pose', 'loading the on-device pose model…');
  const pose = await loadPose();
  if (!S.open) { if (pose && pose.close) pose.close(); return; }
  if (!pose) {
    card('pose model not vendored yet',
      'the mediapipe bundle is not in web/vendor/mediapipe/ on this build — so this ' +
      'page cannot extract keypoints. the camera is on but nothing is being sent.',
      () => [b('retry', boot), laptopBtn()].filter(Boolean));
    setBadge('err', 'no pose');
    return;
  }
  S.pose = pose;
  cover.hidden = true;
  clearInterval(S.kpT);
  S.kpT = setInterval(tick, KP_MS);
}

function openLive() {
  if (S.open) { closeLive(); return; }
  S.open = true;
  S.unpaired = false;
  S.kpDead = S.stDead = S.hbDead = false;   // retry endpoints — daemon may have been updated
  btn.classList.add('on');
  btn.setAttribute('aria-expanded', 'true');
  scrim.hidden = false; sheet.hidden = false;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    scrim.classList.add('open'); sheet.classList.add('open');
  }));
  boot();                                   // getUserMedia fires inside the tap gesture
  css(); probe();
  clearInterval(S.stT);
  S.stT = setInterval(poll, ST_MS); poll();
  startHb();
}
function closeLive() {
  if (!S.open) return;
  S.open = false;
  btn.classList.remove('on');
  btn.setAttribute('aria-expanded', 'false');
  scrim.classList.remove('open'); sheet.classList.remove('open');
  setTimeout(() => {
    if (!S.open) { scrim.hidden = true; sheet.hidden = true; }
  }, 360);
  clearInterval(S.kpT); clearInterval(S.stT); clearInterval(S.hbT);
  S.kpT = S.stT = S.hbT = null;
  if (S.pose && S.pose.close) { try { S.pose.close(); } catch (e) {} }
  S.pose = null;
  stopCam();
  GM.palmT = GM.namT = GM.lastT = 0;
  GM.armed.palm = GM.armed.nam = true; GM.pend = null;
  S.inflight = false; S.pend = null; S.remote = false;
  cover.hidden = true;
  const x = skel.getContext('2d');
  if (x) x.clearRect(0, 0, skel.width, skel.height);
  setBadge('', 'off');
  hint.textContent = HINT_DEF;
  holdI.style.width = '0';
}

btn.onclick = openLive;
xbtn.onclick = closeLive;
scrim.onclick = closeLive;
addEventListener('keydown', e => { if (e.key === 'Escape' && S.open) closeLive(); });
addEventListener('pagehide', closeLive);
addEventListener('pageshow', e => {   // bfcache restore: camera/pose died while hidden — re-arm
  if (e.persisted && S.open && !S.remote && (!S.stream || !S.stream.active)) boot();
});
})();
