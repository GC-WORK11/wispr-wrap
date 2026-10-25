/* teach.js — teach-a-sign wizard + sign library.
   Standalone route, same auth model as index.html (?k= → localStorage 'wfk').

   Endpoint contract (daemon-side implemented in wfd.py / signbank.py):
     GET    /health                    → 200 "ok"
     GET    /bank                      → {ok, signs:[{gloss,label,action,tier,exemplar_count}]}
     PUT    /bank                      → teach/update:
                {gloss,label,action:{type,argv|url|keys|text},tier,
                 exemplars:[{frames:[{t_ms,points:[{x,y,score}×42]}]}]}
                {gloss, add_exemplar:{frames:[…]}}       → re-record one take
                {gloss, rename:{gloss,label}}            → rename
     DELETE /bank                      → body {gloss}
     POST   /teach/begin {name}        → bracket a teach session (laptop-side
     POST   /teach/end    {name,takes}     capture engines attach segments to it)
     GET    /act                       → {ok, actions:[{type,label,desc,tier,fields}], apps:[{name,argv}]}
   All authed with X-WF-Key. If /bank 404s the daemon is an old wrap —
   the page says so and never fakes success.

   Keypoints: kp42 = MediaPipe hand topology ×2. points[0..20] = left hand,
   points[21..41] = right hand, {x,y,score} normalized. Score 0 = absent.
   The tracker is pluggable, in order:
     1. window.WFLive.track(video, onFrame)   — live.js shared impl
     2. window.Hands                          — vendored MediaPipe Hands
     3. window.WFFakeTrack                    — test seam (see tests/)
   Camera frames never leave this device — only keypoints are sent. */

'use strict';
(() => {

/* ---------- css mime fallback ----------
   Today's wfd.py serves .css as application/octet-stream; strict-mode
   browsers (Firefox, some WebViews) refuse it. If the <link> didn't
   produce rules, fetch the sheet as text and inline it — works everywhere. */
function ensureCss() {
  try {
    for (const s of document.styleSheets) {
      if (s.href && s.href.endsWith('teach.css')) {
        try { if (s.cssRules && s.cssRules.length) return; } catch (e) { /* cross-origin guards */ return; }
      }
    }
    fetch('teach.css').then(r => r.ok ? r.text() : Promise.reject(r.status))
      .then(t => { const el = document.createElement('style'); el.textContent = t; document.head.appendChild(el); })
      .catch(() => {});
  } catch (e) {}
}

/* ---------- auth (same model as index.html) ---------- */
const store = (() => { try { return localStorage; } catch (e) { return sessionStorage; } })();
const K = new URLSearchParams(location.search).get('k') || store.getItem('wfk') || '';
if (K) { store.setItem('wfk', K); if (location.search) history.replaceState(null, '', location.pathname); }
const AUTH = { 'X-WF-Key': K };

/* ---------- dom ---------- */
const $ = id => document.getElementById(id);
const dot = $('dot'), dotLabel = $('dotLabel'),
      banner = $('banner'), bannerText = $('bannerText'), bannerBtn = $('bannerBtn'),
      signsEl = $('signs'), emptyEl = $('empty'), libCount = $('libCount'), teachBtn = $('teachBtn'),
      sheet = $('sheet'), wizClose = $('wizClose'), wizSteps = $('wizSteps'),
      nameIn = $('nameIn'), glossPre = $('glossPre'), nameErr = $('nameErr'), nameNext = $('nameNext'),
      recVerb = $('recVerb'), recGloss = $('recGloss'), recLead = $('recLead'), stage = $('stage'), vid = $('vid'),
      skel = $('skel'), recdot = $('recdot'), stageHint = $('stageHint'),
      camErr = $('camErr'), takebar = $('takebar'), recBtn = $('recBtn'), recBack = $('recBack'),
      recErr = $('recErr'),
      takesEl = $('takes'), actCat = $('actCat'), actRows = $('actRows'), bindErr = $('bindErr'), saveBtn = $('saveBtn'),
      doneTitle = $('doneTitle'), doneLead = $('doneLead'),
      toast = $('toast');
const countDown = $('countNum');

const sleep = ms => new Promise(r => setTimeout(r, ms));
const buzz = () => { try { navigator.vibrate && navigator.vibrate(25); } catch (e) {} };
let toastT;
function flash(msg) {
  toast.textContent = msg; toast.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => toast.classList.remove('show'), 2200);
}

/* ---------- api ---------- */
async function api(path, method, body) {
  const opt = { method: method || 'GET', headers: { ...AUTH } };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, opt); }
  catch (e) { return { net: false, status: 0, json: null }; }
  const j = await r.json().catch(() => null);
  return { net: true, ok: r.ok, status: r.status, json: j };
}

function setBanner(kind, text, retry) {
  if (!kind) { banner.hidden = true; return; }
  banner.hidden = false; banner.className = 'banner ' + kind;
  bannerText.textContent = text;
  bannerBtn.hidden = !retry;
  bannerBtn.onclick = retry || null;
}

/* ---------- kp42 / skeleton ---------- */
/* MediaPipe hand topology. points[0..20] = left, [21..41] = right. */
const HAND_EDGES = [
  [0,1],[1,2],[2,3],[3,4],
  [0,5],[5,6],[6,7],[7,8],
  [5,9],[9,10],[10,11],[11,12],
  [9,13],[13,14],[14,15],[15,16],
  [13,17],[17,18],[18,19],[19,20],[0,17],
];
const LIVE_MIN_SCORE = .28;

function handPresent(pts, base) {
  let n = 0;
  for (let i = 0; i < 21; i++) { const p = pts[base + i]; if (p && p.score > LIVE_MIN_SCORE) n++; }
  return n;
}
function anyHand(pts) { return handPresent(pts, 0) >= 4 || handPresent(pts, 21) >= 4; }

function fitSkelCanvas() {
  const dpr = Math.min(2, devicePixelRatio || 1);
  const r = stage.getBoundingClientRect();
  if (skel.width !== (r.width * dpr | 0)) { skel.width = r.width * dpr | 0; skel.height = r.height * dpr | 0; }
  const x = skel.getContext('2d');
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { x, w: r.width, h: r.height };
}

function drawSkel(x, w, h, pts, light) {
  x.clearRect(0, 0, w, h);
  const bone = light ? 'rgba(37,99,235,.85)' : 'rgba(255,255,255,.92)';
  const bone2 = light ? 'rgba(100,116,139,.5)' : 'rgba(148,163,184,.55)';
  for (let hand = 0; hand < 2; hand++) {
    const off = hand * 21;
    if (handPresent(pts, off) < 4) continue;
    x.lineWidth = 2.2; x.lineCap = 'round'; x.strokeStyle = hand ? bone2 : bone;
    x.beginPath();
    for (const [a, b] of HAND_EDGES) {
      const p = pts[off + a], q = pts[off + b];
      if (!p || !q || p.score <= LIVE_MIN_SCORE || q.score <= LIVE_MIN_SCORE) continue;
      x.moveTo(p.x * w, p.y * h); x.lineTo(q.x * w, q.y * h);
    }
    x.stroke();
    x.fillStyle = hand ? bone2 : '#2563eb';
    for (let i = 0; i < 21; i++) {
      const p = pts[off + i];
      if (!p || p.score <= LIVE_MIN_SCORE) continue;
      x.beginPath(); x.arc(p.x * w, p.y * h, i === 0 ? 4 : 2.4, 0, 7); x.fill();
    }
  }
}

/* ---------- tracker — pluggable kp42 source ---------- */
const WFTrack = {
  kind: null, _stop: null,

  sources() {
    const out = [];
    if (window.WFLive && typeof window.WFLive.track === 'function')
      out.push({ kind: 'wflive', start: (v, cb) => window.WFLive.track(v, cb) });
    if (typeof window.Hands === 'function')
      out.push({ kind: 'hands', start: (v, cb) => this._startHands(v, cb) });
    if (window.WFFakeTrack)
      out.push({ kind: 'fake', start: (v, cb) => {
        const f = window.WFFakeTrack;
        return (typeof f === 'function') ? f(v, cb) : f.start(v, cb);
      }});
    return out;
  },

  /* If none of the sources exist yet, try pulling in live.js (W2a) once —
     the page upgrades itself the moment the shared tracker lands. */
  async ensure() {
    if (this.sources().length) return true;
    await new Promise(res => {
      const s = document.createElement('script');
      let done = false;
      const fin = ok => { if (!done) { done = true; res(ok); } };
      s.onload = () => fin(true); s.onerror = () => fin(false);
      s.src = 'live.js'; document.head.appendChild(s);
      setTimeout(() => fin(false), 3500);
    });
    return this.sources().length > 0;
  },

  _startHands(video, cb) {
    const hands = new window.Hands({ locateFile: f => f });
    hands.setOptions({ maxNumHands: 2, modelComplexity: 0, minDetectionConfidence: .5, minTrackingConfidence: .5 });
    hands.onResults(res => {
      const pts = new Array(42);
      for (let i = 0; i < 42; i++) pts[i] = { x: 0, y: 0, score: 0 };
      const lms = res.multiHandLandmarks || [], hs = res.multiHandedness || [];
      for (let hI = 0; hI < lms.length && hI < 2; hI++) {
        const label = (hs[hI] && hs[hI].label) || (hI ? 'Right' : 'Left');
        const base = label === 'Right' ? 21 : 0;
        const lm = lms[hI];
        for (let i = 0; i < 21 && i < lm.length; i++)
          pts[base + i] = { x: lm[i].x, y: lm[i].y, score: hs[hI].score || 1 };
      }
      cb({ t_ms: performance.now(), points: pts });
    });
    let dead = false;
    (async () => { while (!dead) { try { await hands.send({ image: video }); } catch (e) { break; } } })();
    return { stop: () => { dead = true; try { hands.close(); } catch (e) {} } };
  },

  async start(video, cb) {
    this.stop();
    for (const s of this.sources()) {
      try {
        const r = s.start(video, cb);
        this._stop = (r && r.stop) ? () => r.stop() : (typeof r === 'function' ? r : null);
        this.kind = s.kind;
        return s.kind;
      } catch (e) { /* try next source */ }
    }
    this.kind = null;
    return null;
  },

  stop() { if (this._stop) { try { this._stop(); } catch (e) {} this._stop = null; } },
};

/* ---------- camera ---------- */
let stream = null;
async function openCam() {
  if (stream) return true;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } }, audio: false,
    });
    vid.srcObject = stream;
    await vid.play().catch(() => {});
    return true;
  } catch (e) { return false; }
}
function closeCam() {
  if (stream) { for (const t of stream.getTracks()) t.stop(); stream = null; }
  vid.srcObject = null;
}

/* ---------- timings (selftest may shorten via WFTeach.T) ---------- */
const T = { beat: 760, go: 500, cap: 2600, grace: 750, min: 10 };

/* ---------- wizard state ---------- */
const W = {
  mode: 'teach',            // 'teach' | 'add' (add_exemplar)
  target: null,             // gloss being added to, when mode==='add'
  gloss: '', label: '',
  takes: [],                // [{frames:[{t_ms,points}], kept, durMs}]
  need: 3,
  actionType: null,
  busy: false,
};

function stepEl(name) { return sheet.querySelector(`.wstep[data-step="${name}"]`); }
function showStep(name) {
  for (const el of sheet.querySelectorAll('.wstep')) el.classList.toggle('on', el.dataset.step === name);
  const IDX = W.mode === 'add' ? { record: 0, bind: 1, done: 2 } : { name: 0, record: 1, bind: 2, done: 3 };
  const idx = IDX[name] ?? 0;
  [...wizSteps.children].forEach((li, i) => {
    li.classList.toggle('on', i === idx && idx < 3);
    li.classList.toggle('done', i < idx);
  });
}

function openWizard(mode, target) {
  W.mode = mode; W.target = target || null;
  W.gloss = ''; W.label = ''; W.takes = []; W.actionType = null; W.busy = false;
  nameIn.value = ''; glossPre.textContent = ''; nameErr.textContent = '';
  nameNext.disabled = true; recErr.textContent = ''; bindErr.textContent = '';
  saveBtn.disabled = true; saveBtn.textContent = 'make it live';
  sheet.hidden = false;
  document.body.style.overflow = 'hidden';
  actCat.style.display = mode === 'add' ? 'none' : '';
  $('bindActH').style.display = mode === 'add' ? 'none' : '';
  if (mode === 'add') {
    $('wizTitle').textContent = 'add a take · ' + target;
    recVerb.textContent = 'one more take';
    recGloss.textContent = target;
    recLead.textContent = 'same sign, one take — it joins the existing exemplars.';
    saveBtn.textContent = 'add take';
    recBack.textContent = 'cancel';
    W.need = 1;
    enterRecord();
  } else {
    $('wizTitle').textContent = 'teach a sign';
    recBack.textContent = 'back';
    recVerb.textContent = 'perform it';
    recLead.textContent = 'three takes, same sign. hold still, wait for the count, then sign once — it stops on its own when your hands drop.';
    saveBtn.textContent = 'make it live';
    W.need = 3;
    showStep('name');
    setTimeout(() => nameIn.focus(), 60);
  }
}
function closeWizard() {
  sheet.hidden = true; document.body.style.overflow = '';
  WFTrack.stop(); closeCam(); capturing = false;
  if (teachSession) { api('/teach/end', 'POST', { name: teachSession, takes: 0 }); teachSession = null; }
}

/* ---------- step 1 · name ---------- */
const slug = s => s.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
nameIn.addEventListener('input', () => {
  const g = slug(nameIn.value);
  glossPre.innerHTML = g ? 'gloss&nbsp; <b>' + g + '</b>' : '';
  nameNext.disabled = !g; nameErr.textContent = '';
});
nameIn.addEventListener('keydown', e => { if (e.key === 'Enter' && !nameNext.disabled) nameNext.click(); });
$('nameCancel').onclick = closeWizard;

let teachSession = null;
nameNext.onclick = async () => {
  W.label = nameIn.value.trim(); W.gloss = slug(nameIn.value);
  const exists = library.some(s => s.gloss === W.gloss);
  if (exists) { nameErr.textContent = 'that gloss is taken — add takes from the library instead'; return; }
  const r = await api('/teach/begin', 'POST', { name: W.gloss });
  if (r.net && r.status === 403) return unpaired();
  /* 404 = daemon has no teach-session capture (older/lighter engine).
     Not fatal: the phone's exemplars still ride inside PUT /bank. */
  if (r.ok) teachSession = W.gloss;
  recGloss.textContent = W.gloss;
  await enterRecord();
  if (!r.net) recErr.textContent = 'laptop unreachable — you can still record, saving will retry';
};

/* ---------- step 2 · record ---------- */
let capturing = false, capBuf = null, capT0 = 0, lastHandAt = 0, sawHand = false, trackOn = false;

async function enterRecord() {
  showStep('record');
  camErr.hidden = true; camErr.textContent = '';
  renderTakebar();
  const hasSrc = await WFTrack.ensure();
  const camOk = await openCam();
  stage.classList.toggle('nocam', !camOk);
  if (!camOk) {
    camErr.hidden = false;
    camErr.innerHTML = hasSrc
      ? 'camera unavailable — recording from the injected frame source.'
      : 'camera blocked.<br>allow camera access to teach signs.';
  }
  if (hasSrc) {
    trackOn = true;
    await WFTrack.start(vid, onFrame);
    stageHint.textContent = trackHint();
    recBtn.disabled = false;
  } else {
    recBtn.disabled = true;
    recErr.textContent = 'no pose engine found — update wrap on the laptop (live.js / tracker missing)';
    stageHint.textContent = '';
  }
  recBtn.textContent = nextTakeLabel();
}
function trackHint() {
  return { wflive: 'live skeleton — these exact keypoints go to the laptop',
           hands: 'mediapipe hands — keypoints only, video never leaves the phone',
           fake: 'injected frames (test source)' }[WFTrack.kind] || '';
}

function onFrame(fr) {
  // draw only while the record step is up — what you see is what the daemon scores
  if (stepEl('record').classList.contains('on')) {
    const c = fitSkelCanvas();
    drawSkel(c.x, c.w, c.h, fr.points, stage.classList.contains('nocam'));
  }
  if (!capturing) return;
  capBuf.push({ t_ms: Math.round(fr.t_ms - capT0), points: fr.points });
  if (anyHand(fr.points)) { sawHand = true; lastHandAt = performance.now(); }
  const el = performance.now() - capT0;
  if (el >= T.cap || (sawHand && el > 1200 && performance.now() - lastHandAt > T.grace)) finishTake();
}

function renderTakebar() {
  takebar.innerHTML = '';
  const n = Math.max(W.need, W.takes.length + (W.takes.length < W.need ? 1 : 0));
  for (let i = 0; i < n; i++) {
    const d = document.createElement('div');
    d.className = 'tk' + (W.takes[i] && W.takes[i].kept ? ' got' : '');
    takebar.appendChild(d);
  }
}

async function recordTake() {
  recErr.textContent = '';
  recBtn.disabled = true; recBack.disabled = true;
  // 3·2·1 · sign!
  countDown.className = 'count show';
  for (const b of ['3', '2', '1']) {
    countDown.textContent = b; countDown.classList.remove('go');
    buzz(); await sleep(T.beat);
    if (!trackOn) { countDown.className = 'count'; recBtn.disabled = false; recBack.disabled = false; return; }
  }
  countDown.textContent = 'sign'; countDown.classList.add('go');
  await sleep(T.go * .4);
  countDown.className = 'count';
  capturing = true; capBuf = []; capT0 = performance.now(); lastHandAt = 0; sawHand = false;
  recdot.classList.add('on');
  recBtn.textContent = 'recording — tap to end early';
  recBtn.disabled = false; recBtn.onclick = finishTake;
}

function finishTake() {
  if (!capturing) return;
  capturing = false; recdot.classList.remove('on');
  const frames = capBuf; capBuf = null;
  const hands = frames.filter(f => anyHand(f.points)).length;
  const dur = frames.length ? frames[frames.length - 1].t_ms : 0;
  const take = { frames, kept: frames.length >= T.min && hands > 0,
                 durMs: dur, hands,
                 bad: !frames.length ? 'no frames captured'
                    : !hands ? 'no hands seen — keep them in view'
                    : frames.length < T.min ? 'too short — sign a beat slower' : null };
  W.takes.push(take);
  buzz(); renderTakebar();
  recBtn.onclick = recordTake;
  const done = W.takes.filter(t => t.kept).length >= W.need;
  if (done) {
    buildTakesPreview(); buildActionPicker(); validateBind();
    showStep('bind'); recBack.disabled = false; return;
  }
  recBtn.textContent = nextTakeLabel();
  recBtn.disabled = false; recBack.disabled = false;
}
function nextTakeLabel() {
  return W.mode === 'add' ? 'record take' : 'record take ' + (W.takes.length + 1);
}

recBack.onclick = () => { if (W.mode === 'add') closeWizard(); else showStep('name'); };
recBtn.onclick = recordTake;

/* ---------- step 3 · preview + action ---------- */
function drawTakePreview(cv, take) {
  const dpr = Math.min(2, devicePixelRatio || 1), S = 92;
  cv.width = S * dpr; cv.height = S * dpr;
  const x = cv.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.fillStyle = '#f8fafc'; x.fillRect(0, 0, S, S);
  const fr = take.frames; if (!fr.length) return;
  // wrist trajectory per hand (landmark 0 = wrist)
  for (const base of [0, 21]) {
    const path = fr.map(f => f.points[base]).filter(p => p && p.score > LIVE_MIN_SCORE);
    if (path.length < 2) continue;
    x.strokeStyle = base ? 'rgba(100,116,139,.7)' : 'rgba(37,99,235,.9)';
    x.lineWidth = 1.8; x.beginPath();
    path.forEach((p, i) => i ? x.lineTo(p.x * S, p.y * S) : x.moveTo(p.x * S, p.y * S));
    x.stroke();
    x.fillStyle = base ? '#94a3b8' : '#2563eb';
    x.beginPath(); x.arc(path[0].x * S, path[0].y * S, 3, 0, 7); x.fill();
    const e = path[path.length - 1];
    x.beginPath(); x.arc(e.x * S, e.y * S, 3.4, 0, 7); x.stroke();
  }
  // mid-frame hand shape
  const mid = fr[fr.length >> 1].points;
  x.save(); x.scale(.62, .62);
  x.strokeStyle = 'rgba(15,23,42,.5)'; x.lineWidth = 1.4;
  for (const base of [0, 21]) {
    if (handPresent(mid, base) < 4) continue;
    x.beginPath();
    for (const [a, b] of HAND_EDGES) {
      const p = mid[base + a], q = mid[base + b];
      if (p.score > LIVE_MIN_SCORE && q.score > LIVE_MIN_SCORE) { x.moveTo(p.x * S, p.y * S); x.lineTo(q.x * S, q.y * S); }
    }
    x.stroke();
  }
  x.restore();
}

function buildTakesPreview() {
  takesEl.innerHTML = '';
  W.takes.forEach((t, i) => {
    const row = document.createElement('div'); row.className = 'take';
    const cv = document.createElement('canvas'); drawTakePreview(cv, t);
    const info = document.createElement('div'); info.className = 'tinfo';
    const nm = document.createElement('div'); nm.className = 'tname';
    nm.textContent = 'take ' + (i + 1) + (t.kept ? '' : ' · not counted');
    const meta = document.createElement('div'); meta.className = 'tmeta';
    meta.innerHTML = t.bad ? '<span class="bad">' + t.bad + '</span>'
      : t.frames.length + ' frames · ' + (t.durMs / 1000).toFixed(1) + 's · ' + t.hands + ' with hands';
    const btns = document.createElement('div'); btns.className = 'tbtns';
    const redo = document.createElement('button'); redo.className = 'iconbtn'; redo.title = 'redo take'; redo.textContent = '↺';
    redo.onclick = () => {
      W.takes.splice(i, 1);
      if (W.mode === 'add' || W.takes.filter(k => k.kept).length < W.need) {
        enterRecord();
      } else { buildTakesPreview(); validateBind(); }
    };
    btns.appendChild(redo);
    row.append(cv, info, btns);
    info.append(nm, meta);
    takesEl.appendChild(row);
  });
}

/* ---------- action picker ---------- */
/* Default catalog mirrors alive-act ToolId::risk_tier (1 = local, 2 = acts on
   the world → laptop-side confirm). GET /act may override + extend. */
const DEFAULT_ACTIONS = [
  { type: 'open_app', label: 'open app', desc: 'launch a program — the app name as you’d type it in a launcher',
    fields: [{ k: 'argv', ph: 'google-chrome' }], tier: 2 },
  { type: 'open_url', label: 'open url', desc: 'open a page in the laptop browser',
    fields: [{ k: 'url', ph: 'https://example.com' }], tier: 1 },
  { type: 'key_press', label: 'key press', desc: 'fire a key combo — window manager, media keys, anything',
    fields: [{ k: 'keys', ph: 'ctrl+alt+t' }], tier: 2 },
  { type: 'type_text', label: 'type text', desc: 'type a phrase at the laptop cursor',
    fields: [{ k: 'text', ph: 'on my way —' }], tier: 2 },
  { type: 'screenshot', label: 'screenshot', desc: 'save a screenshot on the laptop', fields: [], tier: 1 },
];
let ACT = DEFAULT_ACTIONS.slice(), APPS = [];

function tierChip(t) {
  return '<span class="tier' + (t >= 2 ? ' t2' : '') + '">tier ' + t + '</span>';
}

function buildActionPicker() {
  actRows.innerHTML = '';
  for (const a of ACT) {
    const t = +a.tier || 1;
    const row = document.createElement('div');
    row.className = 'arow' + (t >= 2 ? ' t2' : '') + (W.actionType === a.type ? ' sel' : '');
    row.setAttribute('role', 'button'); row.tabIndex = 0;
    const fieldHtml = (a.fields || []).map(f =>
      `<input type="text" data-k="${esc(f.k)}" placeholder="${esc(f.ph || '')}" autocomplete="off" autocorrect="off" autocapitalize="none">`).join('');
    row.innerHTML =
      `<div class="a-top"><span class="a-name">${esc(a.label)}</span>${tierChip(t)}</div>` +
      `<div class="a-desc">${esc(a.desc || '')}</div>` +
      `<div class="a-field">${fieldHtml}` +
      (a.type === 'open_app' && APPS.length
        ? '<div class="apps">' + APPS.slice(0, 24).map(ap =>
            `<button type="button" class="appchip" data-argv="${esc(ap.argv || ap.name)}">${esc(ap.name)}</button>`).join('') + '</div>'
        : '') +
      `</div>` +
      (t >= 2 ? `<div class="a-confirm">needs confirm on the laptop the first time it fires</div>` : '');
    const pick = () => {
      W.actionType = a.type;
      actRows.querySelectorAll('.arow').forEach(el => el.classList.remove('sel'));
      row.classList.add('sel');
      const inp = row.querySelector('input'); if (inp) setTimeout(() => inp.focus(), 60);
      validateBind();
    };
    row.onclick = e => {
      if (e.target.tagName === 'INPUT' || e.target.classList.contains('appchip')) return;
      pick();
    };
    row.onkeydown = e => { if (e.key === 'Enter' && e.target === row) pick(); };
    row.addEventListener('input', validateBind);
    row.querySelectorAll('.appchip').forEach(ch => ch.onclick = e => {
      e.stopPropagation();
      const inp = row.querySelector('input[data-k="argv"]');
      if (inp) { inp.value = ch.dataset.argv; validateBind(); }
      W.actionType = a.type;
      actRows.querySelectorAll('.arow').forEach(el => el.classList.remove('sel'));
      row.classList.add('sel');
    });
    actRows.appendChild(row);
  }
}

function readAction() {
  const a = ACT.find(x => x.type === W.actionType);
  if (!a) return null;
  const row = [...actRows.children][ACT.indexOf(a)];
  const action = { type: a.type };
  for (const f of (a.fields || [])) {
    const v = (row.querySelector(`input[data-k="${f.k}"]`) || {}).value || '';
    if (!v.trim()) return { missing: f.k };
    action[f.k] = v.trim();
  }
  if (action.url && !/^[a-z]+:\/\//i.test(action.url)) action.url = 'https://' + action.url;
  return { action, tier: +a.tier || 1 };
}

function validateBind() {
  const kept = W.takes.filter(t => t.kept).length;
  const rd = W.mode === 'add' ? { action: {} } : readAction();
  saveBtn.disabled = !(kept > 0 && rd && !rd.missing);
  bindErr.textContent = '';
}
$('bindBack').onclick = () => { enterRecord(); };

/* ---------- save ---------- */
saveBtn.onclick = async () => {
  if (W.busy) return;
  const kept = W.takes.filter(t => t.kept);
  if (!kept.length) { bindErr.textContent = 'keep at least one take'; return; }
  W.busy = true; saveBtn.disabled = true; saveBtn.textContent = 'saving…';

  if (W.mode === 'add') {
    const take = kept[kept.length - 1];
    const r = await api('/bank', 'PUT', { gloss: W.target, add_exemplar: { frames: take.frames } });
    W.busy = false;
    if (handleBankErr(r, 'take')) return;
    doneTitle.textContent = 'take added';
    doneLead.textContent = W.target + ' now has ' + (take.frames.length) + ' more frames to match against.';
    showStep('done'); refreshLibrary(); return;
  }

  const rd = readAction();
  if (!rd || rd.missing) {
    bindErr.textContent = 'fill in the action field';
    W.busy = false; saveBtn.disabled = false; saveBtn.textContent = 'make it live'; return;
  }
  const body = {
    gloss: W.gloss, label: W.label || W.gloss,
    action: rd.action, tier: rd.tier,
    exemplars: kept.map(t => ({ frames: t.frames })),
  };
  const r = await api('/bank', 'PUT', body);
  W.busy = false; saveBtn.disabled = false; saveBtn.textContent = 'make it live';
  if (handleBankErr(r, 'sign')) return;
  if (teachSession) { api('/teach/end', 'POST', { name: teachSession, takes: kept.length }); teachSession = null; }
  doneTitle.textContent = W.gloss + ' is live';
  doneLead.textContent = 'sign it in live mode — the laptop fires ' + (rd.action.label || rd.action.type) +
    (rd.tier >= 2 ? ' after a laptop-side confirm.' : '.');
  showStep('done'); refreshLibrary(); buzz();
};

function handleBankErr(r, what) {
  if (r.ok) return false;
  if (!r.net) { bindErr.textContent = 'laptop unreachable — run: wrap start'; return true; }
  if (r.status === 403) { unpaired(); return true; }
  if (r.status === 404) {
    setBanner('warn', 'the laptop runs an older wrap — update it to teach signs (git pull && npm run wrap)', null);
    bindErr.textContent = 'update wrap on the laptop — this build can’t store ' + what + 's';
    return true;
  }
  bindErr.textContent = 'laptop refused the save' + (r.status ? ' (' + r.status + ')' : '') +
    ((r.json && r.json.error) ? ' — ' + r.json.error : '');
  return true;
}

function unpaired() {
  flash('unpaired — rescan QR');
  try { window.WF && window.WF.unpair && window.WF.unpair(); } catch (e) {}
}

$('doneLib').onclick = () => { closeWizard(); };
$('doneAgain').onclick = () => { closeWizard(); setTimeout(() => openWizard('teach'), 60); };
wizClose.onclick = closeWizard;
teachBtn.onclick = () => openWizard('teach');

/* ---------- library ---------- */
let library = [];

function actSummary(a) {
  if (!a || !a.type) return 'no action bound';
  const v = a.argv || a.url || a.keys || (a.text ? '“' + a.text + '”' : '') || '';
  return (a.type || '?').replace(/_/g, ' ') + (v ? ' · ' + v : '');
}

function renderLibrary() {
  signsEl.innerHTML = '';
  emptyEl.hidden = library.length > 0;
  libCount.textContent = library.length ? library.length + (library.length === 1 ? ' sign' : ' signs') : '';
  for (const s of library) {
    const card = document.createElement('div'); card.className = 'sign';
    const n = s.exemplar_count != null ? s.exemplar_count : (s.exemplars ? s.exemplars.length : '?');
    card.innerHTML =
      `<div class="sign-top"><span class="sign-name">${esc(s.label || s.gloss)}<span class="gl">${esc(s.gloss)}</span></span>` +
      `<span class="nx">${n} exemplar${n === 1 ? '' : 's'}</span></div>` +
      `<div class="sign-act"><span class="av">${esc(actSummary(s.action))}</span>` +
      `${s.tier != null ? tierChip(s.tier) : ''}` +
      `${s.tier >= 2 ? '<span class="tier-note">needs confirm on laptop</span>' : ''}</div>` +
      `<div class="sign-row"></div>`;
    const row = card.querySelector('.sign-row');
    const mk = (txt, cls, fn) => { const b = document.createElement('button'); b.className = 'btn small' + (cls ? ' ' + cls : ''); b.textContent = txt; b.onclick = fn; row.appendChild(b); return b; };
    mk('+ take', '', () => openWizard('add', s.gloss));
    mk('rename', '', () => startRename(card, s));
    const del = mk('delete', 'danger', async () => {
      if (del.dataset.armed) {
        del.textContent = '…'; del.disabled = true;
        const r = await api('/bank', 'DELETE', { gloss: s.gloss });
        if (r.ok) { library = library.filter(x => x.gloss !== s.gloss); renderLibrary(); flash('deleted ' + s.gloss); }
        else if (r.status === 404) setBanner('warn', 'the laptop runs an older wrap — update it to manage signs', null);
        else if (r.status === 403) unpaired();
        else { flash('delete failed (' + (r.status || 'offline') + ')'); del.disabled = false; del.textContent = 'delete'; delete del.dataset.armed; }
      } else { del.dataset.armed = '1'; del.textContent = 'sure?'; setTimeout(() => { if (del.isConnected) { delete del.dataset.armed; del.textContent = 'delete'; } }, 2600); }
    });
    signsEl.appendChild(card);
  }
}

function startRename(card, s) {
  const row = card.querySelector('.sign-row'); row.style.display = 'none';
  const rr = document.createElement('div'); rr.className = 'rename-row';
  const inp = document.createElement('input'); inp.type = 'text'; inp.value = s.label || s.gloss;
  inp.maxLength = 48; inp.autocapitalize = 'characters';
  const ok = document.createElement('button'); ok.className = 'btn small pri'; ok.textContent = 'save';
  const no = document.createElement('button'); no.className = 'btn small'; no.textContent = 'cancel';
  rr.append(inp, ok, no); card.appendChild(rr); inp.focus(); inp.select();
  const cleanup = () => { rr.remove(); row.style.display = ''; };
  no.onclick = cleanup;
  ok.onclick = async () => {
    const label = inp.value.trim(); if (!label) return cleanup();
    const g = slug(label) || s.gloss;
    ok.disabled = true; ok.textContent = '…';
    const r = await api('/bank', 'PUT', { gloss: s.gloss, rename: { gloss: g, label } });
    if (r.ok) { s.label = label; if (g !== s.gloss) s.gloss = g; renderLibrary(); flash('renamed'); }
    else if (r.status === 404) { setBanner('warn', 'the laptop runs an older wrap — update it to manage signs', null); cleanup(); }
    else if (r.status === 403) { unpaired(); cleanup(); }
    else { flash('rename failed (' + (r.status || 'offline') + ')'); ok.disabled = false; ok.textContent = 'save'; }
  };
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') ok.click(); if (e.key === 'Escape') cleanup(); });
}

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function refreshLibrary() {
  const r = await api('/bank');
  if (r.ok && r.json) {
    setBanner(null);
    library = Array.isArray(r.json.signs) ? r.json.signs
            : Array.isArray(r.json) ? r.json : [];
    teachBtn.disabled = false;
  } else if (r.status === 404) {
    setBanner('warn', 'the laptop runs an older wrap — it can’t store signs yet. update it (git pull && npm run wrap)', null);
    teachBtn.disabled = true;
  } else if (r.status === 403) {
    setBanner('err', 'unpaired — rescan the QR shown by wrap start', null);
    teachBtn.disabled = true;
  } else {
    setBanner('warn', 'laptop unreachable — run: wrap start', refreshLibrary);
    teachBtn.disabled = true;
  }
  renderLibrary();
}

/* ---------- probe ---------- */
let wasOn = null;
async function probe() {
  let on = false;
  try { const r = await fetch('/health', { cache: 'no-store' }); on = r.ok; } catch (e) {}
  dot.classList.toggle('on', on);
  dotLabel.textContent = on ? 'linked' : 'offline';
  if (on && wasOn === false) refreshLibrary();   // daemon came back — clear the stale banner
  wasOn = on;
  return on;
}

async function loadCatalog() {
  const r = await api('/act');
  if (!r.ok || !r.json) return;   // picker falls back to the built-in catalog
  const j = r.json;
  const normFields = fs => Array.isArray(fs) ? fs.map(f => typeof f === 'string' ? { k: f, ph: '' } : f) : [];
  if (Array.isArray(j.actions)) {
    const byType = {};
    for (const a of j.actions) if (a && a.type) byType[a.type] = a;
    ACT = DEFAULT_ACTIONS.map(d => ({ ...d, ...(byType[d.type] || {}),
      fields: byType[d.type] && byType[d.type].fields ? normFields(byType[d.type].fields) : d.fields }));
    for (const a of j.actions)
      if (a && a.type && !ACT.find(x => x.type === a.type)) {
        const fields = normFields(a.fields);
        if (a.label) ACT.push({ ...a, fields });
      }
  }
  if (Array.isArray(j.apps)) APPS = j.apps;
}

/* ---------- boot ---------- */
ensureCss();
probe(); setInterval(probe, 3000);
refreshLibrary();
loadCatalog();

/* selftest/seam — tests/drive.js drives the real UI, no fake success paths */
window.WFTeach = { T, openWizard, refreshLibrary, WFTrack,
                   get state() { return { mode: W.mode, gloss: W.gloss, takes: W.takes.length, kept: W.takes.filter(t => t.kept).length, library: library.length }; } };

})();
