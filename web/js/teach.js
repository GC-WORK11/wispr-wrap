// teach.js — teach-a-sign wizard, mounted in #sheet-teach (route 'teach').
//
//   name -> perform (the daemon's own endpointer captures each sign segment
//   from the SAME kp42 stream live mode uses -> exemplar) -> bind (@ picker)
//   -> PUT /bank -> sign it live.
//
// Wire contract (all authed; the live surface is LAN/USB only):
//   POST /teach/begin {name, takes, label}  — open a capture session; every
//                       endpointed segment while it runs is stored as an
//                       exemplar of `name` (wfd.py _on_segment, FIFO cap 5)
//   POST /teach/end                        — close the session (bank saved)
//   PUT  /bank {gloss,label,argv|action,tier} — bind the action (commands.tsv)
//   DELETE /bank?gloss=                    — remove sign + binding
// The capture is server-side: the UI previews locally-buffered frames but
// the daemon's segment is what's scored — nothing here fakes it.
// Reserved glosses (daemon refuses them): PALM-OPEN, NAMASTE.
import { App } from './app.js';
import { Pose } from './pose.js';
import { Skel } from './skeleton.js';
import { KpLink } from './live.js';
import { Actions } from './actions.js';

const sheet = App.el.sheetTeach;
if (!sheet) throw new Error('teach: #sheet-teach missing');

sheet.innerHTML = `
  <div class="sheet-head">
    <h1 id="tw-title">Teach a sign</h1>
    <button class="sheet-x" id="tw-x" aria-label="Close">×</button>
  </div>
  <ol class="tw-steps" id="tw-steps"><li></li><li></li><li></li></ol>
  <div class="sheet-body tw-body">

    <section class="tw-step" data-step="name">
      <p class="hint">A sign is a gesture <b>you</b> choose, bound to something your laptop does.</p>
      <input class="input" id="tw-name" type="text" placeholder="Name it, e.g. open browser"
             autocomplete="off" autocorrect="off" autocapitalize="characters" maxlength="32" enterkeyhint="next">
      <div class="tw-gloss" id="tw-gloss"></div>
      <div class="errline" id="tw-name-err"></div>
      <div class="tw-foot">
        <button class="btn btn-ghost" id="tw-name-cancel">Cancel</button>
        <button class="btn btn-primary" id="tw-name-next" disabled>Record it</button>
      </div>
    </section>

    <section class="tw-step" data-step="record">
      <p class="hint" id="tw-rec-lead"></p>
      <div class="tw-stage" id="tw-stage">
        <video id="tw-vid" muted playsinline autoplay></video>
        <canvas id="tw-skel"></canvas>
        <div class="tw-count" id="tw-count"></div>
        <div class="tw-rec" id="tw-rec"><i></i>REC</div>
        <div class="tw-camerr" id="tw-camerr" hidden></div>
      </div>
      <div class="tw-takes" id="tw-takebar"></div>
      <div class="errline" id="tw-rec-err"></div>
      <div class="tw-foot">
        <button class="btn btn-ghost" id="tw-rec-back">Back</button>
        <button class="btn btn-primary" id="tw-rec-done" disabled>Next</button>
      </div>
    </section>

    <section class="tw-step" data-step="bind">
      <p class="hint">Your laptop learned this from your three takes. Now say what it should do.</p>
      <div class="tw-prev" id="tw-prev"></div>
      <div class="tw-bound" id="tw-bound">
        <button class="btn btn-ghost btn-block" id="tw-pick">@ Pick what it does</button>
        <div class="tw-action" id="tw-action" hidden></div>
      </div>
      <div class="errline" id="tw-bind-err"></div>
      <div class="tw-foot">
        <button class="btn btn-ghost" id="tw-bind-back">Record more</button>
        <button class="btn btn-primary" id="tw-save" disabled>Finish</button>
      </div>
    </section>

    <section class="tw-step" data-step="done">
      <div class="tw-done">
        <div class="tw-check">✓</div>
        <h2 id="tw-done-t">Live</h2>
        <p class="hint" id="tw-done-m"></p>
        <div class="tw-foot">
          <button class="btn btn-ghost" id="tw-done-lib">Your signs</button>
          <button class="btn btn-primary" id="tw-done-live">Try it live</button>
        </div>
      </div>
    </section>

  </div>`;
const $ = id => sheet.querySelector('#' + id);
const nameIn = $('tw-name'), glossPre = $('tw-gloss'), nameErr = $('tw-name-err'),
      nameNext = $('tw-name-next'), stepsEl = $('tw-steps'),
      recLead = $('tw-rec-lead'), stageEl = $('tw-stage'), vid = $('tw-vid'),
      skelC = $('tw-skel'), countEl = $('tw-count'), recEl = $('tw-rec'),
      camErr = $('tw-camerr'), takebar = $('tw-takebar'), recErr = $('tw-rec-err'),
      recDone = $('tw-rec-done'), recBack = $('tw-rec-back'),
      prevEl = $('tw-prev'), pickBtn = $('tw-pick'), actionEl = $('tw-action'),
      bindErr = $('tw-bind-err'), saveBtn = $('tw-save'),
      doneT = $('tw-done-t'), doneM = $('tw-done-m');

/* the REC chip mirrors the daemon's capture session, nothing else */
const setTeaching = v => { W.teaching = v; recEl.classList.toggle('on', v); };

const slug = s => s.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
const RESERVED = new Set(['PALM-OPEN', 'NAMASTE']);
const NEED = 3;

const W = {
  mode: 'teach',           // 'teach' | 'add'
  target: null,            // gloss for 'add'
  gloss: '', label: '',
  takes: [],               // [{frames, hands}]
  action: null,
  stream: null, engine: null, link: new KpLink(),
  teaching: false, got: 0, cur: [], front: true,
  library: [],             // gloss set for dupe check
  busy: false,
};

function stepEl(name) { return sheet.querySelector(`.tw-step[data-step="${name}"]`); }
function showStep(name) {
  for (const el of sheet.querySelectorAll('.tw-step'))
    el.classList.toggle('on', el.dataset.step === name);
  const IDX = W.mode === 'add' ? { record: 0, done: 1 } : { name: 0, record: 1, bind: 2, done: 2 };
  const idx = IDX[name] ?? 0;
  [...stepsEl.children].forEach((li, i) => {
    li.classList.toggle('on', i === idx && idx < 3);
    li.classList.toggle('done', i < idx);
  });
}

/* ---------- kp capture ---------- */
function onFrame(pts, t) {
  Skel.draw(skelC, pts, { video: vid, mirror: W.front });
  W.cur.push({ t_ms: Math.round(t), points: pts });
  if (W.cur.length > 400) W.cur.shift();
}
function onResp(j) {
  if (j.teach) {
    const t = j.teach;
    if (t.got > W.got) {
      W.got = t.got;
      const hands = W.cur.filter(f =>
        Skel.present(f.points, 0) >= 4 || Skel.present(f.points, 21) >= 4).length;
      W.takes.push({ frames: W.cur.slice(), hands });
      W.cur = [];
      App.buzz(); renderTakebar();
      if (W.got >= W.need) {
        App.api.post('/teach/end', { name: W.gloss, takes: W.got }).catch(() => {});
        setTeaching(false);
        if (W.mode === 'add') { finishAdd(); return; }
        buildBind(); showStep('bind');
      } else {
        recLead.textContent = 'Got it. Same sign again, and drop your hands between takes.';
      }
    } else if (t.ok === false) {
      recErr.textContent = 'That one was too short or had no hands in view. Try again a little slower.';
      const slot = takebar.children[W.got];
      if (slot) { slot.classList.add('miss'); setTimeout(() => slot.classList.remove('miss'), 2000); }
    }
  }
}
function onStatus(s) {
  if (s === 'dead') recErr.textContent = 'this daemon has no live surface — update wrap on the laptop.';
  if (s === 'denied') recErr.textContent = 'unpaired — rescan the QR.';
}

async function startCapture() {
  recErr.textContent = '';
  camErr.hidden = true;
  W.link.reset();
  /* test seam: tests/selftest.html injects window.WFFakeTrack — a fake kp42
     source so headless Chrome drives the real wizard. Never defined in
     production; camera/model paths run untouched there. */
  if (window.WFFakeTrack && typeof window.WFFakeTrack.start === 'function') {
    recEl.classList.add('on');
    W.link.start(vid, null, { onFrame, onResp, onStatus });
    /* the seam replaces the detections only — still show the real camera */
    Pose.openCamera(vid, true).then(c => {
      W.stream = c.stream; W.front = c.front; vid.classList.toggle('rear', !W.front);
    }).catch(() => {});
    return;
  }
  /* dev seam: a loopback kp42 replay service (tools/demo-rec) stands in for
     camera+model on displays without one — same detectFrame shape. */
  try {
    const rep = await Pose.replay();
    if (rep) {
      W.engine = rep; W.front = false;
      stageEl.classList.add('replay');
      recLead.textContent += ' (demo input)';
      recEl.classList.add('on');
      W.link.start(vid, W.engine, { onFrame, onResp, onStatus });
      return;
    }
  } catch (e) { /* probe failed — camera path */ }
  try {
    const c = await Pose.openCamera(vid, true);
    W.stream = c.stream; W.front = c.front;
    vid.classList.toggle('rear', !W.front);
  } catch (e) {
    camErr.hidden = false;
    camErr.textContent = e && e.name === 'NoAPI'
      ? 'The camera needs a secure connection. Connect over USB (wrap bridge) or use the wrap tunnel.'
      : 'Camera blocked. Allow camera access to perform the sign.';
  }
  try {
    W.engine = await Pose.load();
  } catch (e) {
    recErr.textContent = 'pose model failed: ' + (Pose.error || e.message);
    return;
  }
  recEl.classList.add('on');
  W.link.start(vid, W.engine, { onFrame, onResp, onStatus });
}

function stopCapture() {
  W.link.stop();
  recEl.classList.remove('on');
  if (W.stream) { for (const t of W.stream.getTracks()) t.stop(); W.stream = null; }
  vid.srcObject = null;
  stageEl.classList.remove('replay');
}

/* ---------- step 1 · name ---------- */
nameIn.addEventListener('input', () => {
  const g = slug(nameIn.value);
  glossPre.innerHTML = g ? 'gloss&nbsp; <b>' + g + '</b>' : '';
  nameErr.textContent = '';
  nameNext.disabled = true;
  if (!g) return;
  if (RESERVED.has(g)) { nameErr.textContent = g + ' is the built-in listen / stop gesture. Pick another name.'; return; }
  if (W.library.some(s => s.gloss === g)) { nameErr.textContent = 'You already have a sign with that name. Add takes to it from Your signs.'; return; }
  nameNext.disabled = false;
});
nameIn.addEventListener('keydown', e => { if (e.key === 'Enter' && !nameNext.disabled) nameNext.click(); });
$('tw-name-cancel').onclick = () => App.back();

nameNext.onclick = async () => {
  W.label = nameIn.value.trim(); W.gloss = slug(nameIn.value);
  const r = await App.api.post('/teach/begin',
    { name: W.gloss, takes: NEED, label: W.label }).catch(() => null);
  if (!r) { nameErr.textContent = 'laptop unreachable — run: wrap start'; return; }
  if (r.status === 403) { nameErr.textContent = 'unpaired — rescan the QR.'; return; }
  if (r.status === 404 || r.status === 405) {
    nameErr.textContent = 'the laptop runs an older wrap — update it (git pull && npm run wrap) to teach signs.';
    return;
  }
  if (r.status === 503 || (r.json && r.json.error && /signbank/.test(r.json.error))) {
    nameErr.textContent = 'no sign engine on the laptop — the daemon needs its signbank module.';
    return;
  }
  if (!r.ok) { nameErr.textContent = (r.json && r.json.error) || 'teach session refused'; return; }
  setTeaching(true);
  recLead.textContent = 'Do ' + W.gloss + ' once, then drop your hands. Three times, so it learns you.';
  recDone.hidden = false; recBack.textContent = 'back';
  renderTakebar();
  showStep('record');
  startCapture();
};

/* ---------- step 2 · record ---------- */
function renderTakebar() {
  takebar.textContent = '';
  const n = W.need || (W.mode === 'add' ? 1 : NEED);
  for (let i = 0; i < n; i++) {
    const d = document.createElement('div');
    d.className = 'tk' + (i < W.got ? ' got' : '');
    takebar.appendChild(d);
  }
  recDone.disabled = W.got < 1;
  recDone.textContent = W.mode === 'add' ? 'Finish'
    : (W.got >= W.need ? 'Next' : 'Next (' + W.got + '/' + W.need + ')');
}
recBack.onclick = async () => {
  if (W.teaching) { await App.api.post('/teach/end', { name: W.gloss }).catch(() => {}); setTeaching(false); }
  if (W.mode === 'add') App.back(); else showStep('name');
};
recDone.onclick = async () => {
  if (W.teaching) { await App.api.post('/teach/end', { name: W.gloss, takes: W.got }).catch(() => {}); setTeaching(false); }
  if (W.mode === 'add') { finishAdd(); return; }
  buildBind(); showStep('bind');
};

/* ---------- step 3 · preview + bind ---------- */
function buildBind() {
  prevEl.textContent = '';
  W.takes.slice(-5).forEach((t, i) => {
    const row = document.createElement('div'); row.className = 'tkv';
    const cv = document.createElement('canvas');
    Skel.drawTrail(cv, t.frames);
    const meta = document.createElement('div'); meta.className = 'tkv-m';
    meta.textContent = 'take ' + (i + 1) + ' · ' + t.frames.length + ' frames' +
      (t.hands ? '' : ' · no hands seen');
    row.append(cv, meta);
    prevEl.appendChild(row);
  });
  actionEl.hidden = true;
  pickBtn.textContent = '@ Pick what ' + W.gloss + ' does';
  saveBtn.disabled = true;
}
pickBtn.onclick = async () => {
  const a = await Actions.pick({ forGloss: W.gloss });
  if (!a) return;
  W.action = a;
  actionEl.hidden = false;
  actionEl.innerHTML =
    `<span class="tw-act-n">${esc(Actions.describe(a))}</span>` +
    `<span class="chip ${a.tier >= 2 ? 'warn' : 'accent'}">${a.tier >= 2 ? 'laptop confirm' : 'instant'}</span>`;
  pickBtn.textContent = 'Change it';
  saveBtn.disabled = false;
};

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

$('tw-bind-back').onclick = () => {
  recLead.textContent = 'Extra takes sharpen the match. It keeps your newest five.';
  showStep('record');
  if (!W.teaching && W.gloss) {
    App.api.post('/teach/begin', { name: W.gloss, takes: 5, label: W.label })
      .then(r => { if (r && r.ok) { setTeaching(true); W.got = 0; W.need = 5; renderTakebar(); } })
      .catch(() => {});
  }
};

saveBtn.onclick = async () => {
  if (W.busy || !W.action) return;
  W.busy = true; saveBtn.disabled = true; saveBtn.textContent = 'Saving…';
  const a = W.action;
  const body = { gloss: W.gloss, label: W.label || W.gloss,
                 tier: a.tier == null ? 1 : a.tier, action: a };
  if (Array.isArray(a.argv) && a.argv.length) body.argv = a.argv;
  const r = await App.api.put('/bank', body).catch(() => null);
  W.busy = false; saveBtn.disabled = false; saveBtn.textContent = 'Finish';
  if (!r) { bindErr.textContent = 'laptop unreachable — run: wrap start'; return; }
  if (r.status === 403) { bindErr.textContent = 'unpaired — rescan the QR.'; return; }
  if (!r.ok) {
    bindErr.textContent = 'the laptop refused it' +
      (r.json && r.json.error ? ' — ' + r.json.error : ' (' + r.status + ')');
    return;
  }
  doneT.textContent = W.gloss + ' is live';
  doneM.textContent = 'Go live and sign it. Your laptop will ' +
    Actions.describe(a) + (a.tier >= 2 ? ', after you approve it on the laptop.' : '.');
  showStep('done');
  App.buzz();
};

async function finishAdd() {
  doneT.textContent = 'take added';
  doneM.textContent = W.target + ' now has ' + W.got + ' new frame' +
    (W.got === 1 ? '' : 's') + ' to match against.';
  showStep('done');
}
$('tw-done-lib').onclick = () => App.nav('signs');
$('tw-done-live').onclick = () => App.nav('live');
$('tw-x').onclick = () => App.back();

/* ---------- route ---------- */
async function refreshLibrary() {
  const r = await App.api.get('/bank').catch(() => null);
  W.library = (r && r.ok && r.json && Array.isArray(r.json.signs)) ? r.json.signs : [];
}
async function open() {
  W.mode = 'teach'; W.target = null;
  W.gloss = ''; W.label = ''; W.takes = []; W.action = null;
  W.got = 0; W.cur = []; W.need = NEED; W.busy = false;
  nameIn.value = ''; glossPre.textContent = ''; nameErr.textContent = '';
  nameNext.disabled = true;
  $('tw-title').textContent = 'Teach a sign';
  showStep('name');
  refreshLibrary();
  setTimeout(() => nameIn.focus(), 250);
}
async function close() {
  stopCapture();
  if (W.teaching) { App.api.post('/teach/end', { name: W.gloss }).catch(() => {}); setTeaching(false); }
}

/* library's "+ take" lands here */
const Teach = {
  async openAdd(gloss) {
    W.mode = 'add'; W.target = gloss;
    W.gloss = gloss; W.label = gloss; W.takes = []; W.action = null;
    W.got = 0; W.cur = []; W.need = 1;
    $('tw-title').textContent = 'add a take · ' + gloss;
    const r = await App.api.post('/teach/begin', { name: gloss, takes: 1 }).catch(() => null);
    if (!r || !r.ok) {
      App.toast(r && r.json && r.json.error ? r.json.error : 'teach refused — check the daemon', true);
      App.nav('signs');
      return;
    }
    setTeaching(true);
    recLead.textContent = 'same sign, one more take — it joins the stored exemplars.';
    recDone.hidden = true; recBack.textContent = 'cancel';
    showStep('record');
    startCapture();
  },
};

App.route('teach', { el: sheet, open, close });
export { Teach };
