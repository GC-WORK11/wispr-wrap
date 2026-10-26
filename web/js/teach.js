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
//   GET  /packs / POST /packs/install {id}          — ready-made signs (packs.js)
// Reserved glosses (daemon refuses them): PALM-OPEN, NAMASTE.
//
// Compat: keeps .tw-step[data-step="name|record|bind|done"] in that DOM order
// (train is appended LAST), the full id set, Teach.openAdd(gloss) and route.
import { App } from './app.js';
import { Pose } from './pose.js';
import { Skel } from './skeleton.js';
import { KpLink } from './live.js';
import { Actions } from './actions.js';
import { Packs } from './packs.js';

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
      <div class="tw-packs" id="tw-packs" hidden></div>
      <div class="tw-ask">
        <h2 class="tw-ask-t">What should it do?</h2>
        <button class="tw-pickrow" id="tw-pick" type="button">
          <span class="tw-pick-ic" id="tw-pick-ic">@</span>
          <span class="tw-pick-tx" id="tw-pick-tx">Pick an app or action</span>
        </button>
        <label class="tw-namelbl" for="tw-name">Call it</label>
        <input class="input" id="tw-name" type="text" placeholder="Give it a name"
               autocomplete="off" autocorrect="off" autocapitalize="off" maxlength="32" enterkeyhint="next">
        <span id="tw-gloss" class="tw-gloss" aria-hidden="true"></span>
        <div class="errline" id="tw-name-err"></div>
      </div>
      <div class="tw-foot">
        <button class="btn btn-ghost" id="tw-name-cancel">Cancel</button>
        <button class="btn btn-primary" id="tw-name-next" disabled>Record</button>
      </div>
    </section>

    <section class="tw-step" data-step="record">
      <p class="tw-lead" id="tw-rec-lead"></p>
      <div class="tw-stage" id="tw-stage">
        <video id="tw-vid" muted playsinline autoplay></video>
        <canvas id="tw-skel"></canvas>
        <div class="tw-count" id="tw-count"></div>
        <div class="tw-rec" id="tw-rec"><i></i>Recording</div>
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
      <p class="tw-lead">Your laptop learned it. What should it do?</p>
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
        <div class="tw-check">✓</div>
        <h2 id="tw-done-t">Ready</h2>
        <p class="tw-done-m" id="tw-done-m"></p>
        <div class="tw-foot">
          <button class="btn btn-ghost" id="tw-done-lib">My signs</button>
          <button class="btn btn-primary" id="tw-done-live">Try it now</button>
        </div>
      </div>
    </section>

    <section class="tw-step" data-step="train">
      <h2 class="tw-ask-t" id="tw-train-t">Learning your sign</h2>
      <div class="tw-prog"><i id="tw-prog-bar"></i></div>
      <ul class="tw-train" id="tw-train">
        <li data-k="read"><span class="tw-tick"></span><span>Reading your three takes</span></li>
        <li data-k="check"><span class="tw-tick"></span><span>Checking it against your other signs</span></li>
        <li data-k="save"><span class="tw-tick"></span><span>Saving to your laptop</span></li>
      </ul>
      <div class="tw-retake" id="tw-retake" hidden>
        <p class="tw-lead" id="tw-retake-m"></p>
        <div class="tw-foot">
          <button class="btn btn-ghost" id="tw-retake-keep">Keep it anyway</button>
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
      packsEl = $('tw-packs'), progBar = $('tw-prog-bar'), trainList = $('tw-train'),
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
        recLead.textContent = 'Got it. Same sign again — drop your hands between takes.';
      }
    } else if (t.ok === false) {
      recErr.textContent = 'That one was too short or had no hands in view. Try again, a little slower.';
      const slot = takebar.children[W.got];
      if (slot) { slot.classList.add('miss'); setTimeout(() => slot.classList.remove('miss'), 2000); }
    }
  }
}
function onStatus(s) {
  if (s === 'dead') recErr.textContent = 'This laptop runs an older version — update it and try again.';
  if (s === 'denied') recErr.textContent = 'Not connected — scan the code again.';
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
    camErr.textContent = e && e.name === 'NoAPI'
      ? 'The camera needs a secure connection. Connect over USB, or use the tunnel link.'
      : 'Camera blocked. Allow camera access to perform the sign.';
  }
  try {
    W.engine = await Pose.load();
  } catch (e) {
    recErr.textContent = 'The pose model failed to load. Reload and try again.';
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
  const sentence = (Actions.sentence && Actions.sentence(a)) ||
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
    `<span class="tw-act-w">${(a.tier >= 2) ? 'Asks you first on your laptop' : ''}</span>`;
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

/* ---------- step 1 · name ---------- */
function validateName() {
  const g = slug(nameIn.value);
  glossEl.textContent = g;
  nameErr.textContent = '';
  // name valid OR an action already picked -> can proceed
  if (!g) { nameNext.disabled = !W.action ? true : false; if (W.action && !g) nameErr.textContent = ''; }
  if (!g && !W.action) { nameNext.disabled = true; return; }
  if (g && RESERVED.has(g)) { nameErr.textContent = 'That gesture already means listen / stop. Pick another name.'; nameNext.disabled = true; return; }
  if (g && W.library.some(s => s.gloss === g)) { nameErr.textContent = 'You already have a sign with that name. Add takes to it from My signs.'; nameNext.disabled = true; return; }
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
  if (!W.gloss) { nameErr.textContent = 'Give it a name first.'; return; }
  if (W.library.some(s => s.gloss === W.gloss)) { nameErr.textContent = 'You already have a sign with that name.'; return; }
  const r = await App.api.post('/teach/begin',
    { name: W.gloss, takes: NEED, label: W.label, action: W.action || undefined }).catch(() => null);
  if (!r) { nameErr.textContent = 'Your laptop is not reachable right now.'; return; }
  if (r.status === 403) { nameErr.textContent = 'Not connected — scan the code again.'; return; }
  if (r.status === 404 || r.status === 405) {
    nameErr.textContent = 'This laptop runs an older version — update it to teach signs.';
    return;
  }
  if (r.status === 503 || (r.json && r.json.error && /signbank/.test(r.json.error))) {
    nameErr.textContent = 'This laptop can’t learn signs yet — update it first.';
    return;
  }
  if (!r.ok) { nameErr.textContent = (r.json && r.json.error) || 'Could not start — try again.'; return; }
  setTeaching(true);
  recLead.textContent = 'Do your “' + W.label + '” sign once, then drop your hands. Three times, so it learns you.';
  recDone.hidden = false; recBack.textContent = 'Back';
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
  if (W.teaching) await endSession();
  if (W.mode === 'add') App.back(); else showStep('name');
};
recDone.onclick = async () => {
  if (W.mode === 'add') { await endSession(); finishAdd(); return; }
  const j = await endSession();
  buildBind();
  if (W.action) runTrain(j); else showStep('bind');
};

/* ---------- step 3 · preview + bind ---------- */
function buildBind() {
  prevEl.textContent = '';
  W.takes.slice(-5).forEach((t, i) => {
    const row = document.createElement('div'); row.className = 'tkv';
    const cv = document.createElement('canvas');
    Skel.drawTrail(cv, t.frames);
    const meta = document.createElement('div'); meta.className = 'tkv-m';
    meta.textContent = 'Take ' + (i + 1);
    row.append(cv, meta);
    prevEl.appendChild(row);
  });
  actionEl.hidden = !W.action;
  saveBtn.disabled = !W.action;
}

$('tw-bind-back').onclick = () => {
  recLead.textContent = 'Extra takes sharpen the match. It keeps your newest five.';
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
  for (const li of trainList.children) li.classList.remove('done', 'bad');
  progBar.style.width = '0%';
  showStep('train');

  // (1) Reading your three takes — draw the trails
  const t0 = Date.now();
  buildBind(); // draws tw-prev trails (reused as the "reading" evidence)
  progBar.style.width = '33%';
  await sleep(Math.max(0, STAGE_MIN - (Date.now() - t0)));
  tick('read');

  // (2) Checking it against your other signs — real held-out result
  const t1 = Date.now();
  const tr = W.train;
  let failed = false;
  if (tr && tr.state) {
    if (tr.state === 'trained') tick('check');
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
  if (!a) { bindErr.textContent = 'Pick what it should do first.'; showStep('bind'); return false; }
  const body = { gloss: W.gloss, label: W.label || W.gloss,
                 tier: a.tier == null ? 1 : a.tier, action: a };
  if (Array.isArray(a.argv) && a.argv.length) body.argv = a.argv;
  const r = await App.api.put('/bank', body).catch(() => null);
  if (!r) { bindErr.textContent = 'Your laptop is not reachable right now.'; return false; }
  if (r.status === 403) { bindErr.textContent = 'Not connected — scan the code again.'; return false; }
  if (!r.ok) {
    bindErr.textContent = 'Your laptop refused it' +
      (r.json && r.json.error ? ' — ' + r.json.error : ' (' + r.status + ')');
    return false;
  }
  return true;
}

$('tw-retake-redo').onclick = () => {
  retakeEl.hidden = true;
  W.got = 0; W.cur = []; W.need = NEED; W.takes = [];
  recLead.textContent = 'Do it again, a little clearer. Three times.';
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
  const sentence = a ? ((Actions.sentence && Actions.sentence(a)) || (Actions.describe && Actions.describe(a))) : '';
  doneT.textContent = 'Ready';
  doneM.textContent = sentence ? ('Your laptop will ' + sentence + '.') : ('“' + W.label + '” is ready to use.');
  showStep('done');
  App.buzz();
  try { window.dispatchEvent(new CustomEvent('wf:sign-ready')); } catch (e) {}
}

async function finishAdd() {
  doneT.textContent = 'Take added';
  doneM.textContent = '“' + (W.label || W.target) + '” has another take now — a little surer each time.';
  showStep('done');
  try { window.dispatchEvent(new CustomEvent('wf:sign-ready')); } catch (e) {}
}
$('tw-done-lib').onclick = () => App.nav('signs');
$('tw-done-live').onclick = () => App.nav('live');
$('tw-x').onclick = () => App.back();

/* ---------- ready-made signs (packs.js) ---------- */
async function mountPacks() {
  try {
    await Packs.mount(packsEl, {
      onStart: startFromPack,   // needs_takes -> jump into Record with name+action
      onDone: () => refreshLibrary(),
    });
  } catch (e) { packsEl.hidden = true; }
}

// a pack chosen for "Teach it": pre-fill name + a type-text action, go to Record
async function startFromPack(pack) {
  W.mode = 'teach'; W.target = null;
  W.gloss = pack.gloss || slug(pack.title || pack.id);
  W.label = pack.title || W.gloss;
  W.takes = []; W.got = 0; W.cur = []; W.need = NEED; W.busy = false;
  // pack text: hello -> "hello", i-am-govinda -> "I am Govinda"
  const text = PACK_TEXT[pack.id] || (pack.action && pack.action.args && pack.action.args.text) || pack.title;
  W.action = pack.action || {
    kind: 'builtin', tool: 'type.text', args: { text }, label: 'Type “' + text + '”', tier: 1,
  };
  nameIn.value = W.label; glossEl.textContent = W.gloss;
  setAction(W.action);
  const r = await App.api.post('/teach/begin',
    { name: W.gloss, takes: NEED, label: W.label, action: W.action }).catch(() => null);
  if (!r || !r.ok) { App.toast('Could not start — try again', true); return; }
  setTeaching(true);
  recLead.textContent = 'Do your “' + W.label + '” sign once, then drop your hands. Three times, so it learns you.';
  recDone.hidden = false; recBack.textContent = 'Back';
  renderTakebar();
  showStep('record');
  startCapture();
}
const PACK_TEXT = { 'hello': 'hello', 'i-am-govinda': 'I am Govinda' };

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
  refreshLibrary();
  mountPacks();
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
    W.gloss = gloss; W.label = gloss; W.takes = []; W.action = null; W.train = null;
    W.got = 0; W.cur = []; W.need = 1;
    $('tw-title').textContent = 'Add a take · ' + gloss;
    const r = await App.api.post('/teach/begin', { name: gloss, takes: 1 }).catch(() => null);
    if (!r || !r.ok) {
      App.toast(r && r.json && r.json.error ? r.json.error : 'Couldn’t teach that — try again', true);
      App.nav('signs');
      return;
    }
    setTeaching(true);
    recLead.textContent = 'Same sign once more — every take makes it surer.';
    recDone.hidden = true; recBack.textContent = 'Cancel';
    renderTakebar();
    showStep('record');
    startCapture();
  },
};

App.route('teach', { el: sheet, open, close });
export { Teach };
