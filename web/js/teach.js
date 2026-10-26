// teach.js — teach-a-sign wizard, mounted in #sheet-teach (route 'teach').
//
// Teach v3 (FOUNDER-SPEC-1229 B): one calm flow —
//   what should it do?  ->  perform it three times  ->  it learns you  ->  ready.
//
// The capture is server-side and real. The UI previews locally-buffered frames,
// but the daemon's own endpointer segments each sign from the SAME kp42 stream
// Go Live uses, and the match/training numbers come back from the daemon —
// nothing here fakes a result.
//
// Wire contract (all authed; the live surface is LAN/USB only):
//   POST /teach/begin {name, takes, label, action?} — open a capture session
//   POST /teach/end  {name, takes}  -> {ok, exemplars, train:{state,takes,
//                       checked,ok,worst_dist}}  — close + run the held-out check
//   PUT  /bank {gloss,label,argv|action,tier}       — bind the action
//   DELETE /bank?gloss=                             — remove a sign
//   GET  /packs                                     — Hello / I am Govinda become tiles (quick.js)
// Reserved glosses (daemon refuses them): PALM-OPEN, NAMASTE.
//
// Compat: keeps .tw-step[data-step="name|record|bind|done"] in that DOM order
// (train is appended LAST), the full id set, Teach.openAdd(gloss) and route.
import { App } from './app.js';
import { Pose } from './pose.js';
import { Skel } from './skeleton.js';
import { KpLink } from './live.js';
import { Actions } from './actions.js';
import { Quick, typeAction } from './quick.js';

const sheet = App.el.sheetTeach;
if (!sheet) throw new Error('teach: #sheet-teach missing');

sheet.innerHTML = `
  <div class="sheet-head">
    <h1 id="tw-title" class="sr-only">Teach a sign</h1>
    <button class="sheet-x" id="tw-x" aria-label="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
  </div>
  <ol class="tw-steps" id="tw-steps" aria-hidden="true"><li></li><li></li><li></li></ol>
  <div class="sheet-body tw-body">

    <section class="tw-step" data-step="name" aria-label="What should it do?">
      <div class="qa-off" id="qa-off" hidden>
        <svg class="qa-off-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="No laptop connected"><rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2 19h20"/></svg>
        <button class="btn btn-primary" id="qa-connect" type="button">Connect</button>
      </div>
      <div class="qa-grid" id="qa-grid" role="group" aria-label="Actions"></div>
      <label class="qa-text" id="qa-text" hidden>
        <svg class="qa-text-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M7 14h10"/></svg>
        <input class="input" id="qa-text-in" type="text" aria-label="Words to type"
               autocomplete="off" autocapitalize="sentences" maxlength="120" enterkeyhint="done">
      </label>
      <div class="tw-compat" hidden>
        <button id="tw-pick" type="button"><span id="tw-pick-ic">@</span><span id="tw-pick-tx">Pick an app or action</span></button>
        <input class="input" id="tw-name" type="text" aria-label="Name"
               autocomplete="off" autocorrect="off" autocapitalize="off" maxlength="32" enterkeyhint="next">
        <span id="tw-gloss" class="tw-gloss" aria-hidden="true"></span>
        <div class="errline" id="tw-name-err"></div>
        <button id="tw-name-cancel" type="button">Cancel</button>
      </div>
      <div class="tw-foot" id="tw-name-foot">
        <button class="btn btn-primary btn-block" id="tw-name-next" disabled>Record</button>
      </div>
    </section>

    <section class="tw-step" data-step="record">
      <p class="tw-lead sr-only" id="tw-rec-lead" role="status"></p>
      <div class="qa-done qa-rec" id="qa-rec"></div>
      <div class="tw-stage" id="tw-stage">
        <video id="tw-vid" muted playsinline autoplay></video>
        <canvas id="tw-skel"></canvas>
        <svg class="tw-ghost" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8.5 11.5V6a1.5 1.5 0 0 1 3 0v5m0-1.5a1.5 1.5 0 0 1 3 0V11m0-.5a1.5 1.5 0 0 1 3 0v1m0-.5a1.5 1.5 0 0 1 3 0v4.5c0 3-2.5 5.5-6 5.5s-5-1.5-6.5-4l-2-3.6c-.6-1 .8-2.1 1.7-1.3l1.3 1.1"/></svg>
        <div class="tw-count" id="tw-count"></div>
        <div class="tw-rec" id="tw-rec" role="img" aria-label="Recording"><i></i></div>
        <div class="tw-camerr" id="tw-camerr" hidden></div>
      </div>
      <div class="tw-takes" id="tw-takebar" role="img" aria-label="Takes"></div>
      <div class="errline sr-only" id="tw-rec-err" role="status"></div>
      <div class="tw-foot">
        <button class="btn btn-ghost" id="tw-rec-back">Back</button>
        <button class="btn btn-primary" id="tw-rec-done" disabled hidden>Next</button>
      </div>
    </section>

    <section class="tw-step" data-step="bind">
      <p class="tw-lead sr-only">What should it do?</p>
      <div class="tw-prev" id="tw-prev"></div>
      <div class="tw-bound" id="tw-bound">
        <button class="tw-pickrow" id="tw-pick2" type="button">
          <span class="tw-pick-ic">@</span>
          <span class="tw-pick-tx" id="tw-pick2-tx">Pick an app or action</span>
        </button>
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
        <div class="tw-check" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></div>
        <div class="qa-done" id="qa-done"></div>
        <h2 id="tw-done-t" class="sr-only">Ready</h2>
        <p class="tw-done-m sr-only" id="tw-done-m" role="status"></p>
        <div class="tw-foot">
          <button class="btn btn-ghost tw-ibtn" id="tw-done-lib" aria-label="My signs"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h10"/></svg></button>
          <button class="btn btn-primary" id="tw-done-live">Try it now</button>
        </div>
      </div>
    </section>

    <section class="tw-step" data-step="train">
      <h2 class="sr-only" id="tw-train-t">Learning your sign</h2>
      <div class="tw-trails" id="tw-trails" aria-hidden="true"></div>
      <div class="tw-prog"><i id="tw-prog-bar"></i></div>
      <ul class="tw-train sr-only" id="tw-train" role="status">
        <li data-k="read"><span class="tw-tick"></span><span>Reading your three takes</span></li>
        <li data-k="check"><span class="tw-tick"></span><span>Checking it against your other signs</span></li>
        <li data-k="save"><span class="tw-tick"></span><span>Saving to your laptop</span></li>
      </ul>
      <div class="tw-retake" id="tw-retake" hidden>
        <p class="tw-lead sr-only" id="tw-retake-m" role="status"></p>
        <div class="tw-foot">
          <button class="btn btn-ghost" id="tw-retake-keep">Keep</button>
          <button class="btn btn-primary" id="tw-retake-redo">Record again</button>
        </div>
      </div>
    </section>

  </div>`;
const $ = id => sheet.querySelector('#' + id);
const nameIn = $('tw-name'), nameErr = $('tw-name-err'),
      nameNext = $('tw-name-next'), stepsEl = $('tw-steps'), glossEl = $('tw-gloss'),
      recLead = $('tw-rec-lead'), stageEl = $('tw-stage'), vid = $('tw-vid'),
      skelC = $('tw-skel'), countEl = $('tw-count'), recEl = $('tw-rec'),
      camErr = $('tw-camerr'), takebar = $('tw-takebar'), recErr = $('tw-rec-err'),
      recDone = $('tw-rec-done'), recBack = $('tw-rec-back'),
      prevEl = $('tw-prev'), pickBtn = $('tw-pick'), pickTx = $('tw-pick-tx'),
      pickIc = $('tw-pick-ic'), pickBtn2 = $('tw-pick2'), pickTx2 = $('tw-pick2-tx'),
      actionEl = $('tw-action'), bindErr = $('tw-bind-err'), saveBtn = $('tw-save'),
      doneT = $('tw-done-t'), doneM = $('tw-done-m'),
      qaDone = $('qa-done'), qaRec = $('qa-rec'), progBar = $('tw-prog-bar'), trainList = $('tw-train'), trailsEl = $('tw-trails'),
      retakeEl = $('tw-retake'), retakeM = $('tw-retake-m');

/* the Recording chip mirrors the daemon's capture session, nothing else */
const setTeaching = v => { W.teaching = v; recEl.classList.toggle('on', v); };

const slug = s => s.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
const RESERVED = new Set(['PALM-OPEN', 'NAMASTE']);
const NEED = 3;

const W = {
  mode: 'teach',           // 'teach' | 'add'
  target: null,            // gloss for 'add'
  gloss: '', label: '',
  takes: [],               // [{frames, hands}]
  action: null,            // chosen up front (optional) or at bind
  train: null,             // the daemon's held-out check for the done screen
  stream: null, engine: null, link: new KpLink(),
  teaching: false, got: 0, cur: [], front: true,
  library: [],             // gloss set for dupe check
  busy: false,
};

function stepEl(name) { return sheet.querySelector(`.tw-step[data-step="${name}"]`); }
function showStep(name) {
  for (const el of sheet.querySelectorAll('.tw-step'))
    el.classList.toggle('on', el.dataset.step === name);
  // progress maps name/train/done into the 3-segment header (train shares seg 3)
  const IDX = W.mode === 'add'
    ? { record: 0, train: 1, done: 1 }
    : { name: 0, record: 1, bind: 2, train: 2, done: 2 };
  const idx = IDX[name] ?? 0;
  [...stepsEl.children].forEach((li, i) => {
    li.classList.toggle('on', i === idx && idx < 3);
    li.classList.toggle('done', i < idx);
  });
}

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
        if (W.mode === 'add') { endSession().then(() => finishAdd()); return; }
        buildBind();
        // action chosen up front? train straight away. otherwise ask at bind.
        if (W.action) endSession().then(j2 => runTrain(j2));
        else { endSession(); showStep('bind'); }
      } else {
        recLead.textContent = 'Take ' + Math.min(W.got + 1, W.need) + ' of ' + W.need;
      }
    } else if (t.ok === false) {
      recErr.textContent = 'Too quick';
      const slot = takebar.children[W.got];
      if (slot) { slot.classList.add('miss'); setTimeout(() => slot.classList.remove('miss'), 2000); }
    }
  }
}
function onStatus(s) {
  if (s === 'dead') recErr.textContent = 'Update the laptop app';
  if (s === 'denied') recErr.textContent = 'Not connected';
}

/* close the capture session; return the daemon's end payload (with .train) */
async function endSession() {
  if (!W.teaching) return null;
  const r = await App.api.post('/teach/end', { name: W.gloss, takes: W.got }).catch(() => null);
  setTeaching(false);
  return r && r.ok ? r.json : null;
}

async function startCapture() {
  recErr.textContent = '';
  camErr.hidden = true;
  W.link.reset();
  if (window.WFFakeTrack && typeof window.WFFakeTrack.start === 'function') {
    recEl.classList.add('on');
    W.link.start(vid, null, { onFrame, onResp, onStatus });
    Pose.openCamera(vid, true).then(c => {
      W.stream = c.stream; W.front = c.front; vid.classList.toggle('rear', !W.front);
    }).catch(() => {});
    return;
  }
  try {
    const rep = await Pose.replay();
    if (rep) {
      W.engine = rep; W.front = false;
      stageEl.classList.add('replay');
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
    camErr.textContent = e && e.name === 'NoAPI' ? 'Needs a secure connection' : 'Camera blocked';
  }
  try {
    W.engine = await Pose.load();
  } catch (e) {
    recErr.textContent = 'Camera model failed';
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

/* ---------- the action row (shared helper) ---------- */
async function setAction(a) {
  W.action = a;
  if (!a) {
    pickIc.textContent = '@'; pickTx.textContent = 'Pick an app or action';
    pickTx2.textContent = 'Pick an app or action';
    if (!nameIn.value.trim()) { nameNext.disabled = true; }
    actionEl.hidden = true; saveBtn.disabled = true;
    validateName();
    return;
  }
  const sentence = a.say || (Actions.sentence && Actions.sentence(a)) ||
                   (Actions.describe && Actions.describe(a)) || 'do something';
  const text = sentence.charAt(0).toUpperCase() + sentence.slice(1);
  pickTx.textContent = text; pickTx2.textContent = text;
  // real app icon if the picker exposes one (feature-detected)
  if (typeof Actions.iconFor === 'function') {
    try {
      const url = await Actions.iconFor(a);
      if (url && W.action === a) { pickIc.textContent = ''; pickIc.style.backgroundImage = `url("${url}")`; pickIc.classList.add('img'); }
    } catch (e) { /* keep the @ */ }
  }
  // auto-fill the name on the name step if the user has not typed one
  if (stepEl('name').classList.contains('on') && !nameIn.value.trim()) {
    const label = a.label || (Actions.describe && Actions.describe(a)) || '';
    if (label) { nameIn.value = label; onNameInput(); }
  }
  // bind-step action line
  actionEl.hidden = false;
  actionEl.innerHTML =
    `<span class="tw-act-n">${esc(text)}</span>` +
    `<span class="tw-act-w sr-only">${(a.tier >= 2) ? 'Needs a yes on your laptop' : ''}</span>`;
  saveBtn.disabled = false;
}

async function openPicker() {
  if (!App.canAct()) { App.emit('unpaired'); return; }
  const q = pickerQuery; pickerQuery = '';
  const a = await Actions.pick({ forGloss: W.gloss, title: 'What should it do?', query: q });
  if (a) setAction(a);
}
let pickerQuery = '';
pickBtn.onclick = openPicker;
pickBtn2.onclick = openPicker;

/* ---------- the one-tap quick-actions grid (FOUNDER-SIMPLIFY-1433) ----------
   Tap a tile -> selected -> the one primary button ("Record"). The sign's name follows
   the tile ("Switch window") unless you typed your own; duplicates get a number. */
const qaGrid = $('qa-grid'), qaText = $('qa-text'), qaTextIn = $('qa-text-in');
let autoName = '';
function uniqueLabel(label) {
  let n = label, i = 2;
  while (W.library.some(x => x.gloss === slug(n))) n = label + ' ' + (i++);
  return n;
}
function applyAuto(label) {
  if (!nameIn.value.trim() || nameIn.value === autoName) {
    autoName = label ? uniqueLabel(label) : '';
    nameIn.value = autoName;
  }
  validateName();
}
function quickPick(a) {
  if (!a) { setAction(null); applyAuto(''); return; }
  applyAuto(a.label);
  setAction(a);
}
async function quickApp() {
  if (!App.canAct()) { App.emit('unpaired'); Quick.clear(); return; }
  const a = await Actions.pick({ title: 'Open which app?', apps: true });
  if (!a) { Quick.clear(); Quick.resetApp(); quickPick(null); return; }
  quickPick(a);
  let url = null;
  try { url = Actions.iconFor ? await Actions.iconFor(a) : null; } catch (e) { /* letter-less is fine */ }
  Quick.setApp(a.label || 'App', url);
}
function showText(v) {                     // v === null hides it; '' shows it empty
  qaText.hidden = v === null;
  if (v === null) { qaTextIn.value = ''; return; }
  qaTextIn.value = v;
  setTimeout(() => qaTextIn.focus(), 60);
}
qaTextIn.addEventListener('input', () => {
  const t = qaTextIn.value.trim();
  quickPick(t ? typeAction(t) : null);
});
qaTextIn.addEventListener('keydown', e => { if (e.key === 'Enter' && !nameNext.disabled) { e.preventDefault(); nameNext.click(); } });
/* laptop actions exist only while a laptop is connected: otherwise one calm "Connect" button */
const qaOff = $('qa-off'), nameFoot = $('tw-name-foot');
let laptopUp = true, gateTimer = null;
async function probeLaptop() {          // the service worker answers /health with a 503 when the laptop is gone
  try { const r = await fetch('/health', { cache: 'no-store' }); laptopUp = r.status < 500; } catch (e) { laptopUp = false; }
  if (!sheet.hidden && stepEl('name').classList.contains('on')) {
    const on = App.canAct() && laptopUp;
    if (on && qaGrid.hidden) mountQuick(); else if (!on && !qaGrid.hidden) applyGate();
  }
}
function applyGate() {
  const on = App.canAct() && laptopUp;
  qaOff.hidden = on; qaGrid.hidden = !on; nameFoot.hidden = !on;
  if (!on) qaText.hidden = true;
  return on;
}
$('qa-connect').onclick = () => { if (App.connect) App.connect(); else App.nav('laptop'); };
App.on('statusbar', () => {
  if (sheet.hidden || !stepEl('name').classList.contains('on')) return;
  const on = App.canAct() && laptopUp;
  if (on && qaGrid.hidden) mountQuick(); else if (!on && !qaGrid.hidden) applyGate();
});
function mountQuick() {
  autoName = '';
  if (!applyGate()) return;
  Quick.mount(qaGrid, {
    onPick: quickPick,
    onOpenApp: quickApp,
    onText: showText,
  });
}


/* ---------- step 1 · name ---------- */
function validateName() {
  const g = slug(nameIn.value);
  glossEl.textContent = g;
  nameErr.textContent = '';
  // name valid OR an action already picked -> can proceed
  if (!g) { nameNext.disabled = !W.action ? true : false; if (W.action && !g) nameErr.textContent = ''; }
  if (!g && !W.action) { nameNext.disabled = true; return; }
  if (g && RESERVED.has(g)) { nameErr.textContent = 'Name taken'; nameNext.disabled = true; return; }
  if (g && W.library.some(s => s.gloss === g)) { nameErr.textContent = 'Name taken'; nameNext.disabled = true; return; }
  nameNext.disabled = !(g || W.action);
}
function onNameInput() {
  // "@" as the first char opens the picker (query = the rest), clears the field
  const v = nameIn.value;
  if (v.charAt(0) === '@') {
    pickerQuery = v.slice(1).trim();
    nameIn.value = '';
    glossEl.textContent = '';
    openPicker();
    return;
  }
  validateName();
}
nameIn.addEventListener('input', onNameInput);
nameIn.addEventListener('keydown', e => { if (e.key === 'Enter' && !nameNext.disabled) nameNext.click(); });
$('tw-name-cancel').onclick = () => App.back();

nameNext.onclick = async () => {
  W.label = nameIn.value.trim() || (W.action && W.action.label) || W.gloss;
  W.gloss = slug(W.label);
  if (!W.gloss) { nameErr.textContent = 'Name needed'; return; }
  if (W.library.some(s => s.gloss === W.gloss)) { nameErr.textContent = 'Name taken'; return; }
  const r = await App.api.post('/teach/begin',
    { name: W.gloss, takes: NEED, label: W.label, action: W.action || undefined }).catch(() => null);
  if (!r) { nameErr.textContent = 'Can’t reach your laptop.'; return; }
  if (r.status === 403) { nameErr.textContent = 'Not connected — scan the code again.'; return; }
  if (r.status === 404 || r.status === 405) {
    nameErr.textContent = 'Update the laptop app';
    return;
  }
  if (r.status === 503 || (r.json && r.json.error && /signbank/.test(r.json.error))) {
    nameErr.textContent = 'Update the laptop app';
    return;
  }
  if (!r.ok) { nameErr.textContent = App.daemonErr(r, 'Could not start — try again.'); return; }
  setTeaching(true);
  recLead.textContent = 'Take 1 of ' + NEED;
  recDone.hidden = true; recBack.textContent = 'Back';
  { const sum = Quick.summary();
    qaRec.innerHTML = sum ? `<span class="qa-ic">${sum.ic}</span><span class="qa-l">${esc(sum.label)}</span>` : `<span class="qa-l">${esc(W.label)}</span>`; }
  renderTakebar();
  showStep('record');
  startCapture();
};

/* ---------- step 2 · record ---------- */
function renderTakebar() {
  stageEl.classList.toggle('got', W.got > 0);
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
  if (W.teaching) await endSession();
  if (W.mode === 'add') App.back(); else showStep('name');
};
recDone.onclick = async () => {
  if (W.mode === 'add') { await endSession(); finishAdd(); return; }
  const j = await endSession();
  buildBind();
  if (W.action) runTrain(j); else showStep('bind');
};

/* ---------- a take, drawn as the path of the wrist(s) ----------
   Thin deep-pink line, a dot where it starts, a ring where it ends. Mirrored for
   the selfie camera so it looks the way you moved. animate=true draws it on. */
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
function drawTake(cv, frames, animate, delay) {
  const S = 92, dpr = Math.min(2, devicePixelRatio || 1);
  cv.width = Math.round(S * dpr); cv.height = Math.round(S * dpr);
  const x = cv.getContext('2d'); if (!x) return;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  const fr = (frames || []).map(f => f.points || f);
  const paths = [];
  for (const off of [0, 21]) {
    const p = fr.map(pt => pt && pt[off]).filter(pt => pt && pt.score > 0.28);
    if (p.length > 1) paths.push(p);
  }
  if (!paths.length) return;
  const mx = q => (W.front ? 1 - q.x : q.x);
  let x0 = 9, y0 = 9, x1 = -9, y1 = -9;
  for (const p of paths) for (const q of p) {
    x0 = Math.min(x0, mx(q)); x1 = Math.max(x1, mx(q)); y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y);
  }
  const pad = 14, w = Math.max(1e-3, x1 - x0), h = Math.max(1e-3, y1 - y0);
  const sc = (S - 2 * pad) / Math.max(w, h);
  const ox = (S - w * sc) / 2 - x0 * sc, oy = (S - h * sc) / 2 - y0 * sc;
  const pt = q => [mx(q) * sc + ox, q.y * sc + oy];
  const cols = [getComputedStyle(sheet).getPropertyValue('--accent').trim() || '#FF309C', '#FFB3D9'];
  const paint = k => {
    x.clearRect(0, 0, S, S);
    paths.forEach((p, i) => {
      const n = Math.max(2, Math.round(p.length * k));
      x.strokeStyle = x.fillStyle = cols[i % 2];
      x.lineWidth = 1.9; x.lineCap = x.lineJoin = 'round';
      x.beginPath();
      for (let j = 0; j < n; j++) { const [a, b] = pt(p[j]); j ? x.lineTo(a, b) : x.moveTo(a, b); }
      x.stroke();
      const [sx, sy] = pt(p[0]); x.beginPath(); x.arc(sx, sy, 3, 0, 7); x.fill();
      if (k >= 1) { const [ex, ey] = pt(p[p.length - 1]); x.beginPath(); x.arc(ex, ey, 4, 0, 7); x.stroke(); }
    });
  };
  if (!animate || REDUCED) { paint(1); return; }
  const T = 720; let t0 = null;
  const step = ts => {
    if (t0 == null) t0 = ts;
    const u = Math.min(1, (ts - t0) / T), e = 1 - Math.pow(1 - u, 3);
    paint(e);
    if (u < 1) requestAnimationFrame(step);
  };
  paint(0.02);
  setTimeout(() => requestAnimationFrame(step), delay || 0);
}

/* ---------- step 3 · preview + bind ---------- */
function buildBind() {
  prevEl.textContent = '';
  W.takes.slice(-5).forEach((t, i) => {
    const row = document.createElement('div'); row.className = 'tkv';
    const cv = document.createElement('canvas');
    drawTake(cv, t.frames, false);
    const meta = document.createElement('div'); meta.className = 'tkv-m';
    meta.textContent = 'Take ' + (i + 1);
    row.append(cv, meta);
    prevEl.appendChild(row);
  });
  actionEl.hidden = !W.action;
  saveBtn.disabled = !W.action;
}

$('tw-bind-back').onclick = () => {
  recLead.textContent = 'Take 1 of 5';
  showStep('record');
  if (!W.teaching && W.gloss) {
    App.api.post('/teach/begin', { name: W.gloss, takes: 5, label: W.label, action: W.action || undefined })
      .then(r => { if (r && r.ok) { setTeaching(true); W.got = 0; W.need = 5; renderTakebar(); } })
      .catch(() => {});
  }
};

saveBtn.onclick = async () => {
  if (W.busy || !W.action) return;
  // the end session (if still open) carries the training check
  let endJson = null;
  if (W.teaching) endJson = await endSession();
  runTrain(endJson);
};

/* ---------- the training view (real progress only) ---------- */
const sleep = ms => new Promise(r => setTimeout(r, ms));
const STAGE_MIN = 450;
function tick(k, ok = true) {
  const li = trainList.querySelector(`li[data-k="${k}"]`);
  if (li) { li.classList.toggle('done', ok); li.classList.toggle('bad', !ok); }
}

async function runTrain(endJson) {
  if (W.busy) return;
  W.busy = true;
  W.train = endJson && endJson.train ? endJson.train : null;
  // reset train view
  retakeEl.hidden = true;
  progBar.parentElement.classList.remove('bad');
  for (const li of trainList.children) li.classList.remove('done', 'bad');
  progBar.style.width = '0%';
  showStep('train');

  // (1) Reading your takes — the paths you drew, one after another
  const t0 = Date.now();
  buildBind(); // keeps #tw-prev (old path + tooling) filled too
  trailsEl.textContent = '';
  const shown = W.takes.slice(-3);
  shown.forEach((t, i) => {
    const fig = document.createElement('figure'); fig.className = 'tw-trail';
    const cv = document.createElement('canvas');
    const okb = document.createElement('i'); okb.className = 'tw-trail-ok';
    fig.append(cv, okb); trailsEl.appendChild(fig);
    drawTake(cv, t.frames, true, 150 * i);
  });
  progBar.style.width = '33%';
  await sleep(Math.max(0, (REDUCED ? STAGE_MIN : 720 + 150 * Math.max(0, shown.length - 1)) - (Date.now() - t0)));
  tick('read');

  // (2) Checking it against your other signs — real held-out result
  const t1 = Date.now();
  const tr = W.train;
  let failed = false;
  if (tr && tr.state) {
    if (tr.state === 'trained') {
      tick('check');
      const lab = trainList.querySelector('li[data-k="check"] span:last-child');
      if (lab && typeof tr.ok === 'number' && typeof tr.checked === 'number' && tr.checked > 0) {
        lab.textContent = tr.ok === tr.checked
          ? 'All ' + tr.checked + ' takes agree with each other'
          : tr.ok + ' of ' + tr.checked + ' takes agree with each other';
      }
      trailsEl.querySelectorAll('.tw-trail-ok').forEach((b, i) => setTimeout(() => b.classList.add('on'), 90 * i));
    }
    else if (tr.state === 'recheck' || tr.state === 'needs_retake' || tr.state === 'needs_more') failed = true;
    else tick('check');
  } else {
    // training info absent — mark done without claiming a check
    tick('check');
  }
  progBar.style.width = '66%';
  await sleep(Math.max(0, STAGE_MIN - (Date.now() - t1)));

  if (failed) {
    tick('check', false);
    progBar.style.width = '66%';
    progBar.parentElement.classList.add('bad');
    trailsEl.querySelectorAll('.tw-trail-ok').forEach(b => b.classList.add('on', 'bad'));
    retakeM.textContent = (tr.state === 'needs_more')
      ? 'It needs a couple more takes to be sure. Record it again.'
      : 'That one looks too close to another sign. Record it again, a little differently.';
    retakeEl.hidden = false;
    W.busy = false;
    return; // never show success on a failed check
  }

  // (3) Saving to your laptop — bind the action
  const t2 = Date.now();
  const ok = await saveBank();
  progBar.style.width = '100%';
  await sleep(Math.max(0, STAGE_MIN - (Date.now() - t2)));
  if (!ok) { tick('save', false); W.busy = false; showStep('bind'); return; }
  tick('save');
  await sleep(220);
  finishDone();
  W.busy = false;
}

async function saveBank() {
  const a = W.action;
  if (!a) { bindErr.textContent = 'Pick an action'; showStep('bind'); return false; }
  const body = { gloss: W.gloss, label: W.label || W.gloss,
                 tier: a.tier == null ? 1 : a.tier, action: a };
  if (Array.isArray(a.argv) && a.argv.length) body.argv = a.argv;
  const r = await App.api.put('/bank', body).catch(() => null);
  if (!r) { bindErr.textContent = 'Can’t reach your laptop.'; return false; }
  if (r.status === 403) { bindErr.textContent = 'Not connected — scan the code again.'; return false; }
  if (!r.ok) {
    bindErr.textContent = App.daemonErr(r, 'Your laptop refused it — try again.');
    return false;
  }
  return true;
}

$('tw-retake-redo').onclick = () => {
  retakeEl.hidden = true;
  W.got = 0; W.cur = []; W.need = NEED; W.takes = [];
  recLead.textContent = 'Take 1 of ' + NEED;
  renderTakebar();
  showStep('record');
  App.api.post('/teach/begin', { name: W.gloss, takes: NEED, label: W.label, action: W.action || undefined })
    .then(r => { if (r && r.ok) setTeaching(true); })
    .catch(() => {});
};
$('tw-retake-keep').onclick = async () => {
  const ok = await saveBank();
  if (ok) finishDone();
};

function finishDone() {
  const a = W.action;
  const sentence = a ? (a.say || (Actions.sentence && Actions.sentence(a)) || (Actions.describe && Actions.describe(a))) : '';
  doneT.textContent = 'Ready';
  doneM.textContent = sentence ? sentence.charAt(0).toUpperCase() + sentence.slice(1) : W.label;
  const sum = Quick.summary();
  qaDone.innerHTML = sum ? `<span class="qa-ic">${sum.ic}</span><span class="qa-l">${esc(sum.label)}</span>` : '';
  showStep('done');
  App.buzz();
  try { window.dispatchEvent(new CustomEvent('wf:sign-ready')); } catch (e) {}
}

async function finishAdd() {
  qaDone.textContent = '';
  doneT.textContent = 'Take added';
  doneM.textContent = 'Take added';
  showStep('done');
  try { window.dispatchEvent(new CustomEvent('wf:sign-ready')); } catch (e) {}
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
  W.gloss = ''; W.label = ''; W.takes = []; W.action = null; W.train = null;
  W.got = 0; W.cur = []; W.need = NEED; W.busy = false;
  nameIn.value = ''; nameErr.textContent = ''; glossEl.textContent = '';
  pickerQuery = '';
  pickIc.textContent = '@'; pickIc.style.backgroundImage = ''; pickIc.classList.remove('img');
  pickTx.textContent = 'Pick an app or action';
  pickTx2.textContent = 'Pick an app or action';
  actionEl.hidden = true; saveBtn.disabled = true;
  nameNext.disabled = true;
  $('tw-title').textContent = 'Teach a sign';
  showStep('name');
  showText(null);
  qaGrid.textContent = '';                       // never show last visit's tiles
  clearInterval(gateTimer); probeLaptop(); gateTimer = setInterval(probeLaptop, 2500);
  refreshLibrary().then(() => mountQuick());
}
async function close() {
  clearInterval(gateTimer);
  stopCapture();
  if (W.teaching) { App.api.post('/teach/end', { name: W.gloss }).catch(() => {}); setTeaching(false); }
}

/* library's "+ take" lands here */
const Teach = {
  async openAdd(gloss) {
    W.mode = 'add'; W.target = gloss;
    W.gloss = gloss; W.label = gloss; W.takes = []; W.action = null; W.train = null;
    W.got = 0; W.cur = []; W.need = 1;
    $('tw-title').textContent = 'Add a take · ' + gloss;
    const r = await App.api.post('/teach/begin', { name: gloss, takes: 1 }).catch(() => null);
    if (!r || !r.ok) {
      App.toast(App.daemonErr(r, 'Couldn’t teach that — try again'), true);
      App.nav('signs');
      return;
    }
    setTeaching(true);
    recLead.textContent = 'Take 1 of 1';
    qaRec.innerHTML = `<span class="qa-l">${esc(W.label || gloss)}</span>`;
    recDone.hidden = true; recBack.textContent = 'Cancel';
    renderTakebar();
    showStep('record');
    startCapture();
  },
};

App.route('teach', { el: sheet, open, close });
export { Teach };
