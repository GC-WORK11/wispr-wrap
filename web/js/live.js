// live.js — Go Live: the full-screen camera view behind the composer's black
// waveform button. You sign; your laptop types the word (Text) or runs what
// the sign is bound to (Control). Video never leaves this device: the page
// finds the hands on the phone and sends ~1 KB of hand positions per frame.
//
// Daemon contract (all X-WF-Key, polling only, no websockets):
//   POST /live/kp      {t_ms, points[42], session?, aspect?}
//                        -> {ok, session, mode?, match?, events?}   (a match rides the
//                           response of the frame that closed the sign)
//   GET  /live/state   {listening, text_mode, last_match, feed[], pending[], bank_size}
//                        polled ~5 Hz while this view is open
//   POST /live/mode    {mode:"text"|"control"}  Text: an accepted sign types its word at
//                        the laptop cursor. Control: it runs the sign's bound action.
//                        Closing the view puts the laptop back in control mode.
//   POST /live/listen  {on}          pause / resume (an open palm and palms together do too)
//   POST /act/confirm  {id, cancel?} run or drop an action that waits for an OK
//   GET  /bank         sign names + bound actions, so captions read like people talk
//   GET  /engine       laptop-camera capability (offered only when this camera can't open)
// Engine names, distances and reasons stay hidden unless the page runs with ?dev.
//
// Kept for other lanes: `export class KpLink` (teach.js streams through it) and the
// ids tools rely on: #lv-x #lv-cam #lv-skel #lv-cover #lv-badge(.listen) #lv-guess
// #lv-act-t #lv-m-text #lv-m-ctl.
import { App } from './app.js';
import { Pose } from './pose.js';
import { Skel } from './skeleton.js';

const sheet = App.el.sheetLive;
if (!sheet) throw new Error('live: #sheet-live missing');

const svg = (d, w) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w || 2}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const ICON = {
  flip: svg('<path d="M20.5 11.5a8.5 8.5 0 0 0-15-4.9L3.5 8.5"/><path d="M3.5 3.8v4.7h4.7"/><path d="M3.5 12.5a8.5 8.5 0 0 0 15 4.9l2-1.9"/><path d="M20.5 20.2v-4.7h-4.7"/>', 1.9),
  x: svg('<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>', 2.1),
  hand: svg('<path d="M18 11V6a2 2 0 0 0-4 0"/><path d="M14 10V4a2 2 0 0 0-4 0v2"/><path d="M10 10.5V6a2 2 0 0 0-4 0v8"/><path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-6-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/><path class="sl" d="M3 3l18 18"/>', 1.8),
  cam: svg('<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>', 1.7),
  camOff: svg('<path d="M2 2l20 20"/><path d="M7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16"/><path d="M9.5 4h5L17 7h3a2 2 0 0 1 2 2v7.5"/><path d="M14.1 15.1a3 3 0 1 1-4.2-4.2"/>', 1.7),
  lock: svg('<rect x="4.5" y="11" width="15" height="10" rx="2.5"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>', 1.7),
  laptop: svg('<rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2.5 19.5h19"/>', 1.7),
  check: svg('<path d="M20 6 9 17l-5-5"/>', 2.6),
};

/* ---------- DOM (built once; index.html keeps only the mount) ---------- */
sheet.innerHTML = `
  <div class="lv-morph" id="lv-morph" aria-hidden="true" hidden></div>
  <div class="lv mode-control is-paused" id="lv-view">
    <video id="lv-cam" playsinline muted autoplay></video>
    <canvas id="lv-skel" aria-hidden="true"></canvas>
    <div class="lv-scrim" aria-hidden="true"></div>
    <div class="lv-top">
      <p class="lv-net" id="lv-net" hidden><i></i><span>Reconnecting to your laptop</span></p>
      <button class="lv-rb" id="lv-flip" aria-label="Flip camera">${ICON.flip}</button>
    </div>
    <p class="lv-tag" id="lv-tag" hidden>Demo input · recorded hand motion</p>
    <div class="lv-cover" id="lv-cover" hidden>
      <div class="lv-card" role="alertdialog" aria-labelledby="lv-card-t" aria-describedby="lv-card-m">
        <span class="lv-card-i" id="lv-card-i"></span>
        <h2 class="lv-card-t" id="lv-card-t"></h2>
        <p class="lv-card-m" id="lv-card-m"></p>
        <div class="lv-card-b" id="lv-card-b"></div>
      </div>
    </div>
    <div class="lv-foot">
      <div class="lv-cap" id="lv-cap">
        <p class="lv-line" id="lv-guess" aria-live="polite"></p>
        <p class="lv-act" id="lv-act" aria-live="polite"><i class="lv-ok">${ICON.check}</i><span id="lv-act-t"></span></p>
        <button class="lv-cta" id="lv-cta" hidden></button>
        <p class="lv-why" id="lv-why"></p>
      </div>
      <div class="lv-ask" id="lv-pend" hidden></div>
      <div class="lv-row">
        <button class="lv-rb lv-listen" id="lv-listen" aria-pressed="false" aria-label="Resume">${ICON.hand}<i class="lv-ring" id="lv-ring" aria-hidden="true"></i></button>
        <div class="lv-seg" id="lv-seg" role="tablist" aria-label="What your signs do">
          <span class="lv-thumb" aria-hidden="true"></span>
          <button role="tab" id="lv-m-text" data-m="text" aria-selected="false">Text</button>
          <button role="tab" id="lv-m-ctl" data-m="control" aria-selected="true">Control</button>
        </div>
        <button class="lv-rb" id="lv-x" aria-label="Close">${ICON.x}</button>
      </div>
    </div>
    <span class="lv-sr" id="lv-badge" role="status"><span id="lv-badge-t">off</span></span>
  </div>`;

const $ = id => sheet.querySelector('#' + id);
const viewEl = $('lv-view'), video = $('lv-cam'), skel = $('lv-skel'), morph = $('lv-morph'),
      netEl = $('lv-net'), flipBtn = $('lv-flip'), tagEl = $('lv-tag'),
      coverEl = $('lv-cover'), cardI = $('lv-card-i'), cardT = $('lv-card-t'), cardM = $('lv-card-m'), cardB = $('lv-card-b'),
      guessEl = $('lv-guess'), actEl = $('lv-act'), actT = $('lv-act-t'), ctaBtn = $('lv-cta'), whyEl = $('lv-why'),
      pendEl = $('lv-pend'), segText = $('lv-m-text'), segCtl = $('lv-m-ctl'),
      listenBtn = $('lv-listen'), ring = $('lv-ring'), xBtn = $('lv-x'),
      badge = $('lv-badge'), badgeT = $('lv-badge-t');

const KP_MS = 83;          // ~12 Hz hand frames (spec: 10-15 Hz)
const ST_MS = 200;         // ~5 Hz state poll while open
const DEV = document.body.classList.contains('dev');

/* ---------- shared hand-frame stream (teach.js reuses KpLink) ---------- */
export class KpLink {
  /* start(video, engine, {session, onFrame(pts,t), onResp(j), onStatus(s)})
     detect -> POST /live/kp, latest-wins (a slow link drops frames, never queues). */
  constructor() { this.reset(); }
  reset() {
    this.session = null; this.inflight = false; this.pend = null;
    this.timer = null; this.video = null; this.engine = null;
    this.onFrame = null; this.onResp = null; this.onStatus = null;
    this.dead = false;    // 404/405: a laptop on an older build
    this.denied = false;  // 403: not paired, or a proxied request
    this._fake = null;
  }
  start(video, engine, opts) {
    this.stop();
    Object.assign(this, opts || {});
    this.video = video; this.engine = engine;
    /* test seam: a test page defines window.WFFakeTrack before load, a synthetic
       hand source so a headless browser can drive the real flows. Production
       pages never define it, so this branch is dead there. */
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
    if (!this.engine.videoless && (!this.video || this.video.readyState < 2)) return;
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
    /* t_ms is wall-clock based: performance.now() restarts at 0 on every page
       load, and the laptop would see time run backwards after a reload. */
    const body = { t_ms: Math.round(performance.timeOrigin + t - 1.78e12), points: pts };
    if (this.session) body.session = this.session;
    // the real video aspect lets the laptop map hand positions on portrait sensors
    if (this.video && this.video.videoWidth)
      body.aspect = +(this.video.videoWidth / this.video.videoHeight).toFixed(4);
    App.api.post('/live/kp', body)
      .then(r => {
        if (r.status === 403) { this.denied = true; this.stop(); if (this.onStatus) this.onStatus('denied'); return; }
        if (r.status === 404 || r.status === 405) { this.dead = true; this.stop(); if (this.onStatus) this.onStatus('dead'); return; }
        if (r.ok && r.json) {
          if (r.json.session && !this.session) this.session = r.json.session;
          if (this.onStatus) this.onStatus('ok');
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
  open: false, gen: 0, openAt: 0,
  stream: null, front: true, engine: null, replay: null, remote: false, booting: false, hidden: false,
  link: new KpLink(), stT: null, polling: false, linkUp: false, wantListen: true,
  mode: (() => { try { return localStorage.getItem('wf.livemode') === 'text' ? 'text' : 'control'; } catch (e) { return 'control'; } })(),
  textLocal: false, modeAt: 0,
  listening: false, listenAt: 0, userPaused: false,
  words: [], seen: new Set(), base: false,
  bank: new Map(), bankSize: null, fireAct: {},
  pendKey: '', energy: 0, hands: 0, prev: null, lastFrame: 0,
  wake: null, lapCam: false, signT: null,
};

/* ---------- words people read ---------- */
const up = g => String(g || '').toUpperCase();
const cap = s => { s = String(s || '').trim(); return s ? s[0].toUpperCase() + s.slice(1) : s; };
const human = s => String(s || '').replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
const hostOf = u => { try { return new URL(/^[a-z]+:/i.test(u) ? u : 'https://' + u).host.replace(/^www\./, ''); } catch (e) { return 'the page'; } };
const keysOf = k => String(k || '').split('+').map(p => p.length <= 1 ? p.toUpperCase() : cap(p)).join('+');

function bankRow(g) { return S.bank.get(up(g)) || null; }
function signName(g, label) {
  const row = bankRow(g);
  const l = label || (row && row.label) || human(g).toLowerCase();
  return cap(human(l));
}
/* the app a bound action points at: the laptop's own name for it (its app list),
   else the action's label ("open Notes"), else a tidied id */
function appName(a) {
  const ar = a.args || {};
  const id = String(ar.id || ar.app || a.id || a.app || '');
  if (id && APPS.has(id.toLowerCase())) return APPS.get(id.toLowerCase());
  const m = /^(?:open|close|focus|switch to)\s+(.+)$/i.exec(String(a.label || '').trim());
  if (m && !/^app( window)?$/i.test(m[1])) return m[1];
  return id ? cap(human(id.replace(/\.desktop$/, ''))) : 'the app';
}
const APPS = new Map();
async function loadApps() {
  if (APPS.size) return;
  const r = await App.api.get('/act').catch(() => null);
  const apps = r && r.ok && r.json && Array.isArray(r.json.apps) ? r.json.apps : [];
  for (const x of apps) if (x && x.id && x.name) APPS.set(String(x.id).toLowerCase(), String(x.name));
}
function kindOf(a) {
  const t = String(a.tool || a.type || '');
  if (t === 'app.open' || t === 'open_app' || (a.kind === 'app' && !t)) return 'open';
  if (t === 'app.close' || t === 'close_window') return 'close';
  if (t === 'app.focus' || t === 'focus_app') return 'focus';
  if (t === 'type.text' || t === 'type_text') return 'type';
  if (t === 'key.press' || t === 'key_press') return 'key';
  if (t === 'open.url' || t === 'open_url') return 'url';
  if (/screenshot/.test(t)) return 'shot';
  if (t === 'macro' || a.kind === 'macro') return 'macro';
  if (t === 'command' || t === 'run_argv' || Array.isArray(a.argv)) return 'cmd';
  return '';
}
/* what a sign did, past tense — never argv, never ids */
function doneText(a) {
  if (!a) return 'Done';
  const ar = a.args || {};
  switch (kindOf(a)) {
    case 'open': return 'Opened ' + appName(a);
    case 'close': return 'Closed ' + appName(a);
    case 'focus': return 'Switched to ' + appName(a);
    case 'type': return 'Typed “' + String(ar.text || a.text || '').trim().slice(0, 32) + '”';
    case 'key': return 'Pressed ' + keysOf(ar.keys || a.keys);
    case 'url': return 'Opened ' + hostOf(ar.url || a.url);
    case 'shot': return 'Took a screenshot';
    case 'macro': return 'Ran your shortcut';
  }
  return 'Done';
}
/* what it will do, for "needs your OK" */
function askText(a, fallback) {
  if (!a) return cap(fallback || 'Run this');
  const ar = a.args || {};
  switch (kindOf(a)) {
    case 'open': return 'Open ' + appName(a);
    case 'close': return 'Close ' + appName(a);
    case 'focus': return 'Switch to ' + appName(a);
    case 'type': return 'Type “' + String(ar.text || a.text || '').trim().slice(0, 28) + '”';
    case 'key': return 'Press ' + keysOf(ar.keys || a.keys);
    case 'url': return 'Open ' + hostOf(ar.url || a.url);
    case 'shot': return 'Take a screenshot';
    case 'macro': return 'Run your shortcut';
  }
  return cap(human(a.label || fallback || 'Run this'));
}

/* ---------- captions ---------- */
const T = { text: '', kind: '', until: 0, timer: 0 };
function say(text, kind = '', ms = 3200) {
  T.text = text; T.kind = kind; T.until = performance.now() + ms;
  clearTimeout(T.timer); T.timer = setTimeout(() => renderSub(), ms + 30);
  renderSub();
}
function hint() {
  if (S.booting) return { t: 'Getting ready…', kind: 'wait' };
  if (App.state && App.state.online === false) return { t: '' };
  if (S.bankSize === 0) return { t: 'You haven’t taught any signs yet', cta: 'Teach a sign' };
  if (!S.listening) return { t: 'Paused. Show an open palm to resume' };
  if (S.mode === 'text') {
    if (S.textLocal && S.words.length) return { t: 'Added to your message', cta: 'Send to laptop' };
    return { t: S.words.length ? 'Typed on your laptop' : 'Sign, and the words type on your laptop' };
  }
  return { t: 'Sign to control your laptop' };
}
let subShown = '';
function renderSub() {
  const live = performance.now() < T.until;
  const h = live ? { t: T.text, kind: T.kind } : hint();
  const k = (h.t || '') + '|' + (h.kind || '');
  actEl.className = 'lv-act' + (h.kind ? ' ' + h.kind : '') + (h.t ? '' : ' empty');
  if (k !== subShown) {
    subShown = k;
    actT.textContent = h.t || '';
    actEl.classList.remove('in'); void actEl.offsetWidth; actEl.classList.add('in');
  } else actEl.classList.add('in');
  ctaBtn.hidden = !h.cta || live;
  if (h.cta) ctaBtn.textContent = h.cta;
}
ctaBtn.onclick = () => {
  if (ctaBtn.textContent === 'Send to laptop') App.emit('text:send');
  else App.nav('teach');
};

function renderWords() {
  guessEl.classList.remove('pop', 'gone');
  guessEl.textContent = '';
  const ws = S.words.slice(-14);
  ws.forEach((w, i) => {
    const s = document.createElement('span');
    s.className = 'w' + (i === ws.length - 1 ? ' new' : '');
    s.textContent = w;
    guessEl.append(s, i === ws.length - 1 ? '' : ' ');
  });
  // more lines than fit: the oldest words fade out at the top (measured on the
  // first word, so the new word's entry animation can't trigger it)
  const first = guessEl.firstElementChild;
  guessEl.classList.toggle('long', !!first && first.getBoundingClientRect().top < guessEl.getBoundingClientRect().top - 1);
}
function onWord(word, gloss, t) {
  const k = 'w|' + t + '|' + up(gloss);
  if (S.seen.has(k)) return;
  S.seen.add(k);
  word = String(word || '').trim();
  if (!word) return;
  S.words.push(word);
  if (S.words.length > 60) S.words.splice(0, S.words.length - 60);
  renderWords();
  if (S.textLocal) App.emit('text:append', word);
  say(S.textLocal ? 'Added to your message' : 'Typed on your laptop', 'ok', 2600);
  hit();
}
function showSign(g, label) {
  guessEl.textContent = signName(g, label);
  guessEl.classList.remove('pop', 'gone', 'long');
  void guessEl.offsetWidth;
  guessEl.classList.add('pop');
  clearTimeout(S.signT);
  S.signT = setTimeout(fadeSign, 4200);
  hit();
}
/* the sign's name fades once it has been read (the pop animation's fill would
   otherwise hold it at full opacity) */
function fadeSign() {
  clearTimeout(S.signT);
  if (S.mode !== 'control' || !guessEl.textContent) return;
  guessEl.classList.remove('pop');
  guessEl.classList.add('gone');
}
function dev(m) {
  if (!DEV || !m) return;
  const d = m.dist != null ? m.dist : m.d;
  whyEl.textContent = [m.gloss, d == null ? '' : 'd=' + (+d).toFixed(3), m.second ? 'next ' + m.second : '', m.reason || ''].filter(Boolean).join(' · ');
}

/* ---------- what the laptop decided ---------- */
function onMatch(m) {
  if (!m) return;
  const k = 'm|' + (m.at_ms != null ? m.at_ms : '') + '|' + up(m.gloss) + '|' + (m.accepted ? 1 : 0) + (m.mode ? '|mode' : '');
  if (S.seen.has(k)) return;
  S.seen.add(k);
  if (m.mode) { listenFromLaptop(/listen/i.test(m.label || '') || /palm/i.test(m.gloss || '')); return; }
  if (m.dup_suppressed) return;            // the second half of one sign: already handled
  dev(m);
  if (!m.accepted) {
    if (!/^dropped/.test(m.reason || '')) { fadeSign(); say('Didn’t catch that', 'soft', 2400); }
    return;
  }
  const a = m.action || null;
  if (S.mode === 'text' && a && a.kind === 'text') { onWord(a.typed, m.gloss, m.at_ms); return; }
  if (S.mode === 'text' && S.textLocal) { onWord(signName(m.gloss, m.label).toLowerCase(), m.gloss, m.at_ms); return; }
  // Control (or a laptop that is not in text mode: show what really happened)
  showSign(m.gloss, m.label);
  if (S.mode === 'text') syncMode();
  if (a && a.pending) say('Waiting for your OK', 'wait', 8000);
  else if (a && a.fired) say(doneText((bankRow(m.gloss) || {}).action || a), 'ok');
  else if (a && a.routed === false) say('Nothing is set for this sign yet', 'soft', 3000);
}
function onFired(ev) {
  const g = (ev.glosses || [])[0];
  const r = ev.result || {};
  const act = S.fireAct[up(g)] || (bankRow(g) || {}).action || null;
  if (r.pending) { say('Waiting for your OK', 'wait', 8000); return; }
  if (ev.ok === false || r.fired === false || r.refused) { say('Couldn’t do that on your laptop', 'soft', 3200); return; }
  say(doneText(act), 'ok');
}
function feed(evs) {
  if (!Array.isArray(evs)) return;
  for (const ev of evs) {
    const gl = ev.gloss || (ev.glosses || []).join('+');
    const k = 'e|' + ev.type + '|' + ev.t_ms + '|' + up(gl);
    if (S.seen.has(k)) continue;
    S.seen.add(k);
    if (ev.type === 'fire' && ev.action) for (const g of ev.glosses || []) S.fireAct[up(g)] = ev.action;
    if (!S.base) continue;                  // the first poll after opening is history, not news
    if (ev.type === 'type' && S.mode === 'text') onWord(ev.text, (ev.glosses || [])[0], ev.t_ms);
    else if (ev.type === 'fired' && S.mode === 'control') onFired(ev);
    /* the laptop started a fresh session (it slept, the link dropped for a while,
       or it restarted) and stopped listening: you didn't pause, so keep going */
    else if (ev.type === 'session' && ev.reset && !S.userPaused && S.linkUp) setTimeout(() => {
      if (S.open && !S.userPaused && !S.listening) listen(true);
    }, 250);
  }
  if (S.seen.size > 600) { const keep = [...S.seen].slice(-250); S.seen.clear(); keep.forEach(x => S.seen.add(x)); }
}
function renderState(j) {
  if (typeof j.listening === 'boolean' && j.listening !== S.listening &&
      performance.now() - S.listenAt > 900) setListening(j.listening);
  if (typeof j.bank_size === 'number' && j.bank_size !== S.bankSize) {
    const first = S.bankSize == null;
    S.bankSize = j.bank_size;
    if (!first) loadBank();
    renderSub();
  }
  if (S.mode === 'text' && !S.textLocal && j.text_mode === false && S.base) syncMode();
  if (j.last_match) {
    if (!S.base) S.seen.add('m|' + (j.last_match.at_ms != null ? j.last_match.at_ms : '') + '|' + up(j.last_match.gloss) + '|' + (j.last_match.accepted ? 1 : 0) + (j.last_match.mode ? '|mode' : ''));
    else onMatch(j.last_match);
  }
  feed(j.feed);
  renderPending(Array.isArray(j.pending) ? j.pending : []);
  S.base = true;
}
async function poll() {
  if (!S.open || S.polling) return;
  S.polling = true;
  try {
    const r = await App.api.get('/live/state');
    if (!S.open) return;
    if (r.status === 404 || r.status === 405) { outdated(); return; }
    if (r.ok && r.json) renderState(r.json);
  } catch (e) { /* offline: the net line says so */ }
  finally { S.polling = false; }
  if (performance.now() - S.lastFrame > 450 && (S.energy > .01 || S.hands)) { S.energy *= .4; S.hands = 0; setBars(); }
}
async function loadBank() {
  const r = await App.api.get('/bank').catch(() => null);
  const rows = r && r.ok && r.json && Array.isArray(r.json.signs) ? r.json.signs : null;
  if (!rows) return;
  S.bank.clear();
  for (const s of rows) {
    if (!s || !s.gloss) continue;
    const action = s.action && typeof s.action === 'object' ? s.action
      : (Array.isArray(s.argv) && s.argv.length ? { argv: s.argv, label: s.label } : null);
    S.bank.set(up(s.gloss), { label: s.label || '', action });
  }
  if (S.bankSize == null) S.bankSize = rows.length;
}

/* ---------- "needs your OK" ---------- */
function renderPending(rows) {
  const key = rows.map(p => p.id).join(',');
  if (key === S.pendKey) return;
  S.pendKey = key;
  pendEl.textContent = '';
  if (!rows.length) { pendEl.hidden = true; viewEl.classList.remove('has-ask'); return; }
  const p = rows[rows.length - 1];
  const act = (bankRow(p.gloss) || {}).action || S.fireAct[up(p.gloss)] || null;
  const row = document.createElement('div');
  row.className = 'lv-ask-row';
  const t = document.createElement('div');
  t.className = 'lv-ask-t';
  const b = document.createElement('b');
  b.textContent = askText(act, signName(p.gloss, p.label)) + '?';
  const s = document.createElement('span');
  s.textContent = rows.length > 1 ? rows.length + ' signs are waiting for your OK' : 'This sign asks before it runs';
  t.append(b, s);
  const btns = document.createElement('div');
  btns.className = 'lv-ask-b';
  const no = document.createElement('button');
  no.className = 'no'; no.textContent = 'Not now';
  const yes = document.createElement('button');
  yes.className = 'yes'; yes.id = 'lv-ok-run'; yes.textContent = 'Run';
  yes.onclick = async () => {
    yes.disabled = no.disabled = true;
    const r = await App.api.post('/act/confirm', { id: p.id }).catch(() => null);
    if (r && r.ok) { say(doneText(act), 'ok'); hit(); }
    else say('Couldn’t do that on your laptop', 'soft');
    S.pendKey = '#'; poll();
  };
  no.onclick = async () => {
    yes.disabled = no.disabled = true;
    await App.api.post('/act/confirm', { id: p.id, cancel: true }).catch(() => null);
    say('Cancelled', 'soft', 1800);
    S.pendKey = '#'; poll();
  };
  btns.append(no, yes);
  row.append(t, btns);
  pendEl.appendChild(row);
  pendEl.hidden = false;
  viewEl.classList.add('has-ask');
  App.buzz();
}

/* ---------- listening ---------- */
function setListening(on) {
  S.listening = !!on;
  viewEl.classList.toggle('is-live', S.listening);
  viewEl.classList.toggle('is-paused', !S.listening);
  listenBtn.setAttribute('aria-pressed', S.listening ? 'true' : 'false');
  listenBtn.setAttribute('aria-label', S.listening ? 'Pause' : 'Resume');
  badge.className = 'lv-sr' + (S.listening ? ' listen' : '');
  badgeT.textContent = S.listening ? 'listening' : 'paused';
  if (!S.listening) fadeSign();
  setBars();
  renderSub();
}
function listenFromLaptop(on) {
  S.userPaused = !on;                      // an open palm / palms together is you deciding
  if (on === S.listening) return;
  S.listenAt = performance.now();
  setListening(on);
  App.buzz();
}
async function listen(on) {
  S.listenAt = performance.now();
  setListening(on);
  const r = await App.api.post('/live/listen', { on }).catch(() => null);
  if (!r || !r.ok) { setListening(!on); say('Couldn’t reach your laptop', 'soft'); }
}
listenBtn.onclick = () => { S.wantListen = false; S.userPaused = S.listening; listen(!S.listening); };

/* ---------- Text | Control ---------- */
function setMode(m, persist = true) {
  S.mode = m === 'text' ? 'text' : 'control';
  if (persist) { try { localStorage.setItem('wf.livemode', S.mode); } catch (e) {} }
  segText.setAttribute('aria-selected', S.mode === 'text' ? 'true' : 'false');
  segCtl.setAttribute('aria-selected', S.mode === 'control' ? 'true' : 'false');
  viewEl.classList.toggle('mode-text', S.mode === 'text');
  viewEl.classList.toggle('mode-control', S.mode === 'control');
  S.words = [];
  clearTimeout(S.signT);
  guessEl.textContent = '';
  T.until = 0;
  renderSub();
  return syncMode(true);
}
/* tell the laptop; an older laptop without text mode falls back to "words go
   into the message on this phone" (observe: nothing fires, nothing types) */
async function syncMode(force) {
  const now = performance.now();
  if (!force && now - S.modeAt < 4000) return;
  S.modeAt = now;
  const want = S.mode;
  const r = await App.api.post('/live/mode', { mode: want }).catch(() => null);
  if (want !== S.mode) return;
  if (want === 'text' && r && r.status === 400) {
    S.textLocal = true;
    await App.api.post('/live/mode', { mode: 'observe' }).catch(() => null);
  } else if (want === 'control' && r && r.status === 400) {
    S.textLocal = false;
    await App.api.post('/live/mode', { mode: 'live' }).catch(() => null);
  } else if (r && r.ok) S.textLocal = false;
  renderSub();
}
segText.onclick = () => { if (S.mode !== 'text') { setMode('text'); App.buzz(); } };
segCtl.onclick = () => { if (S.mode !== 'control') { setMode('control'); App.buzz(); } };
App.on('sent', r => {
  if (!S.open || !S.textLocal) return;
  if (r && r.ok) { S.words = []; renderWords(); say('Sent to your laptop', 'ok'); }
  else say('Couldn’t send', 'soft');
});

/* ---------- hands seen: a quiet ring around the listen button ----------
   it swells with how much your hands move (transform/opacity only, set at the
   frame cadence with a CSS transition, so it stays smooth on the compositor) */
function setBars() {
  const on = S.listening && !S.booting && S.hands > 0;
  const e = on ? Math.min(1, S.energy) : 0;
  const t = on ? 'scale(' + (1.04 + .2 * e).toFixed(3) + ')' : 'scale(.92)';
  const o = on ? (.32 + .6 * e).toFixed(2) : '0';
  if (ring._t !== t) { ring._t = t; ring.style.transform = t; }
  if (ring._o !== o) { ring._o = o; ring.style.opacity = o; }
}
function hit() {
  listenBtn.classList.remove('hit'); void listenBtn.offsetWidth; listenBtn.classList.add('hit');
  clearTimeout(hit.t); hit.t = setTimeout(() => listenBtn.classList.remove('hit'), 700);
  App.buzz();
}
const TIPS = [0, 4, 8, 12, 16, 20];
function onFrame(pts) {
  S.lastFrame = performance.now();
  if (DEV) Skel.draw(skel, pts, { video: S.replay ? null : video, mirror: S.front && !S.replay });
  let hands = 0, e = 0;
  for (const o of [0, 21]) {
    const w = pts[o], m = pts[o + 9];
    if (!w || !m || w.score < .28 || m.score < .28) continue;
    hands++;
    const size = Math.max(.02, Math.hypot(m.x - w.x, m.y - w.y));
    const p = S.prev;
    if (p && p[o] && p[o].score >= .28) {
      let d = 0;
      for (const k of TIPS) d += Math.hypot(pts[o + k].x - p[o + k].x, pts[o + k].y - p[o + k].y);
      e = Math.max(e, d / TIPS.length / size);
    }
  }
  S.prev = pts;
  S.hands = hands;
  S.energy = Math.max(Math.min(1, e / .32), S.energy * .7);
  setBars();
}

/* ---------- overlay card (camera permission, errors) ---------- */
function card(icon, title, msg, buttons) {
  cardI.innerHTML = ICON[icon] || '';
  cardT.textContent = title || '';
  cardM.textContent = msg || '';
  cardB.textContent = '';
  for (const b of buttons || []) if (b) cardB.appendChild(b);
  coverEl.hidden = false;
  viewEl.classList.add('has-cover');
}
function hideCard() { coverEl.hidden = true; viewEl.classList.remove('has-cover'); }
function mkBtn(label, fn, pri) {
  const el = document.createElement('button');
  el.className = pri ? 'pri' : '';
  el.textContent = label;
  el.onclick = ev => { ev.stopPropagation(); fn(); };
  return el;
}
function outdated() {
  if (!coverEl.hidden && cardT.textContent === 'Your laptop needs an update') return;
  card('laptop', 'Your laptop needs an update',
    'This laptop runs an older Wispr Wrap without Go Live. Update it, then open Go Live again.',
    []);
}

/* ---------- camera ---------- */
async function camPermission() {
  try {
    const p = await navigator.permissions.query({ name: 'camera' });
    return p.state;                    // granted | denied | prompt
  } catch (e) { return ''; }           // Safari / Firefox may not answer: just ask
}
function constraints(front, deviceId) {
  const v = deviceId ? { deviceId: { exact: deviceId } }
    : { facingMode: front ? 'user' : { ideal: 'environment' } };
  v.width = { ideal: 960 }; v.height = { ideal: 720 };
  return { video: v, audio: false };
}
async function openCam(front, deviceId) {
  const stream = await navigator.mediaDevices.getUserMedia(constraints(front, deviceId));
  if (!S.open) { stream.getTracks().forEach(t => t.stop()); throw Object.assign(new Error('closed'), { name: 'Closed' }); }
  stopCam();
  S.stream = stream;
  video.srcObject = stream;
  await video.play().catch(() => {});
  const tr = stream.getVideoTracks()[0];
  const st = tr && tr.getSettings ? tr.getSettings() : {};
  // laptops and fake cameras report no facing: treat the first one as the selfie view
  S.front = st.facingMode ? st.facingMode !== 'environment' : front;
  S.deviceId = st.deviceId || '';
  video.classList.toggle('rear', !S.front);
  tr && tr.addEventListener('ended', () => { if (S.open && S.stream === stream) camFail({ name: 'Ended' }); });
}
function stopCam() {
  if (S.stream) for (const t of S.stream.getTracks()) t.stop();
  S.stream = null;
  video.srcObject = null;
}
async function boot() {
  const gen = S.gen;
  hideCard();
  if (S.remote) return;
  /* dev seam: a loopback replay service (tools/demo-rec) may feed recorded hand
     motion. Its probe is ~350 ms, once; on a phone it never answers. */
  try {
    const rep = await Pose.replay();
    if (gen !== S.gen) { if (rep) rep.close(); return; }
    if (rep) { startReplay(rep); return; }
  } catch (e) { /* no replay service: the camera path runs */ }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    camFail({ name: window.isSecureContext ? 'NoAPI' : 'Insecure' }); return;
  }
  S.booting = true; setBars(); renderSub();
  const perm = await camPermission();
  if (gen !== S.gen) return;
  if (perm === 'denied') { S.booting = false; camFail({ name: 'NotAllowedError' }); return; }
  const ask = setTimeout(() => {
    if (gen === S.gen && !S.stream) card('cam', 'Allow the camera',
      'Your browser will ask. Video stays on this phone; only the positions of your hands go to your laptop.');
  }, perm === 'granted' ? 900 : 0);
  try { await openCam(true); }
  catch (e) { clearTimeout(ask); if (gen !== S.gen || e.name === 'Closed') return; S.booting = false; camFail(e); return; }
  clearTimeout(ask);
  if (gen !== S.gen) return;
  hideCard();
  await startModel(gen);
}
async function startModel(gen) {
  S.booting = true; setBars(); renderSub();
  /* compiling the model is the heaviest main-thread work here: let the open
     animation finish first so it never competes with it */
  const wait = S.openAt + 560 - performance.now();
  if (wait > 0) await new Promise(r => setTimeout(r, wait));
  if (gen !== S.gen) return;
  let eng = null;
  try { eng = await Pose.load(); }
  catch (e) {
    if (gen !== S.gen) return;
    S.booting = false; setBars(); renderSub();
    card('camOff', 'Sign reading didn’t start',
      'The hand model could not load on this phone. Check the connection to your laptop, then try again.',
      [mkBtn('Try again', () => { hideCard(); startModel(S.gen); }, true)]);
    return;
  }
  if (gen !== S.gen) return;
  /* the model is shared and stays loaded: closing Go Live never closes it, so
     opening again is instant (and a closed model cannot silently stop reading) */
  S.engine = eng;
  S.booting = false;
  startLink(video, eng);
  setBars(); renderSub();
}
function startReplay(rep) {
  S.replay = rep;
  S.front = false;
  viewEl.classList.add('replay');
  tagEl.hidden = false;
  S.booting = false;
  startLink(video, rep);
  setBars(); renderSub();
}
function startLink(src, engine) {
  S.link.start(src, engine, {
    onFrame,
    onResp: onKpResp,
    onStatus: s => {
      if (s === 'denied') {
        card('laptop', 'Connect your laptop first',
          'This phone isn’t connected to your laptop any more. Connect it again, then open Go Live.',
          [mkBtn('Connect', () => App.nav('laptop'), true)]);
      } else if (s === 'dead') outdated();
      else if (s === 'neterr') setNet(false);
      else if (s === 'ok') setNet(true);
    },
  });
}
function onKpResp(j) {
  if (!S.linkUp) {
    S.linkUp = true;
    // opening Go Live is the intent to sign: start listening once the stream is up
    if (S.wantListen) { S.wantListen = false; listen(true); }
  }
  if (j.mode) listenFromLaptop(/palm/i.test(j.mode));
  if (j.match) onMatch(j.match);
}
function camFail(e) {
  S.booting = false; setBars(); renderSub();
  const name = (e && e.name) || '';
  const lap = S.lapCam ? mkBtn('Use the laptop camera', useLaptop) : null;
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError')
    card('camOff', 'Camera is off',
      'Allow camera access for this page in your browser’s site settings, then try again. Video stays on this phone.',
      [mkBtn('Try again', () => boot(), true), lap]);
  else if (name === 'Insecure' || name === 'NoAPI')
    card('lock', 'Camera needs a secure connection',
      'Browsers only open the camera on a secure link. Connect this phone with USB, or run “wrap tunnel” on your laptop and scan the new code.',
      [S.lapCam ? mkBtn('Use the laptop camera', useLaptop, true) : null]);
  else if (name === 'NotFoundError' || name === 'OverconstrainedError' || name === 'DevicesNotFoundError')
    card('camOff', 'No camera found', 'This device has no camera Go Live can use.',
      [S.lapCam ? mkBtn('Use the laptop camera', useLaptop, true) : null]);
  else if (name === 'NotReadableError' || name === 'TrackStartError')
    card('camOff', 'The camera is busy', 'Another app is using the camera. Close it, then try again.',
      [mkBtn('Try again', () => boot(), true)]);
  else if (name === 'Ended')
    card('camOff', 'The camera stopped', 'The camera was turned off. Try again to turn it back on.',
      [mkBtn('Try again', () => boot(), true)]);
  else
    card('camOff', 'The camera didn’t start', 'Try again. If it keeps happening, reload the page.',
      [mkBtn('Try again', () => boot(), true)]);
}
/* the laptop's own webcam, when its engine offers one (laptop camera lane) */
async function useLaptop() {
  S.remote = true;
  S.link.stop(); stopCam();
  await App.api.post('/live/source', { source: 'laptop' }).catch(() => null);
  card('laptop', 'Using your laptop’s camera', 'Sign in front of your laptop. Captions still show here.',
    [mkBtn('Use this phone’s camera', async () => {
      S.remote = false;
      await App.api.post('/live/source', { source: 'phone' }).catch(() => null);
      boot();
    }, true)]);
}
async function probeEngine() {
  const r = await App.api.get('/engine').catch(() => null);
  const j = r && r.ok && r.json;
  S.lapCam = !!(j && (j.laptop_cam || j.laptopCam));
}

/* ---------- flip camera ---------- */
let flipping = false;
flipBtn.onclick = async () => {
  if (flipping || S.remote || S.replay || !S.stream) return;
  flipping = true;
  flipBtn.classList.toggle('spin');
  video.classList.add('swap');
  const gen = S.gen, was = S.deviceId, wasFront = S.front;
  let ok = false;
  try {
    const cams = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
    const tr = S.stream && S.stream.getVideoTracks()[0];
    const facing = tr && tr.getSettings && tr.getSettings().facingMode;
    stopCam();
    if (facing) {                                    // phones: the other side
      try { await openCam(!wasFront); ok = S.deviceId !== was || S.front !== wasFront; } catch (e) { ok = false; }
    }
    if (!ok && cams.length > 1) {                    // laptops and others: the next camera
      const i = cams.findIndex(c => c.deviceId === was);
      const next = cams[(i + 1) % cams.length];
      try { await openCam(!wasFront, next.deviceId); S.front = !wasFront; video.classList.toggle('rear', !S.front); ok = true; } catch (e) { ok = false; }
    }
    if (!ok && gen === S.gen) {
      await openCam(wasFront, was || undefined).catch(() => {});
      say('No other camera on this device', 'soft', 2200);
    }
  } catch (e) {
    if (gen === S.gen && !S.stream) camFail(e);
  } finally {
    flipping = false;
    requestAnimationFrame(() => video.classList.remove('swap'));
  }
};

/* ---------- connection line ---------- */
function setNet(ok) {
  const off = ok === false || (App.state && App.state.online === false);
  if (netEl.hidden === !off) return;
  netEl.hidden = !off;
  renderSub();
}
App.on('statusbar', s => { if (S.open) setNet(!!(s && s.online)); });

/* ---------- keep the screen on while signing ---------- */
async function wakeLock(on) {
  try {
    if (on) {
      if (!S.wake && navigator.wakeLock && document.visibilityState === 'visible') {
        const w = await navigator.wakeLock.request('screen');
        if (!S.open) { w.release().catch(() => {}); return; }
        S.wake = w;
        w.addEventListener('release', () => { if (S.wake === w) S.wake = null; });
      }
    } else if (S.wake) { const w = S.wake; S.wake = null; await w.release(); }
  } catch (e) { /* not supported, or denied by the browser: harmless */ }
}
document.addEventListener('visibilitychange', () => {
  if (!S.open) return;
  if (document.visibilityState === 'hidden') {
    // backgrounded: the camera goes dark until you come back
    S.hidden = true; S.gen++; S.link.stop(); stopCam();
  } else if (S.hidden) {
    S.hidden = false; wakeLock(true); boot();
  }
});

/* ---------- open / close: the black button grows into the view ---------- */
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
function btnGeom() {
  const b = document.getElementById('tray-live');
  const r = b && b.getBoundingClientRect();
  if (!r || !r.width || !r.height || getComputedStyle(b).visibility === 'hidden') return null;
  const d = Math.max(r.width, r.height);
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const R = Math.hypot(Math.max(cx, innerWidth - cx), Math.max(cy, innerHeight - cy));
  return { d, cx, cy, s: (2 * R + 8) / d };
}
function placeMorph(g, scale, animate) {
  morph.style.transition = animate ? '' : 'none';
  morph.style.left = (g.cx - g.d / 2) + 'px';
  morph.style.top = (g.cy - g.d / 2) + 'px';
  morph.style.width = morph.style.height = g.d + 'px';
  morph.style.transform = 'scale(' + scale + ')';
}
function morphIn() {
  sheet.classList.remove('closing', 'shown');
  void viewEl.offsetWidth;                 // the sheet was display:none: commit opacity 0 first
  const g = reduced() ? null : btnGeom();
  if (!g) { morph.hidden = true; requestAnimationFrame(() => sheet.classList.add('shown')); return; }
  morph.hidden = false;
  placeMorph(g, 1, false);
  void morph.offsetWidth;
  requestAnimationFrame(() => {
    placeMorph(g, g.s, true);
    sheet.classList.add('shown');
  });
  const done = () => { if (S.open && sheet.classList.contains('shown')) morph.hidden = true; };
  morph.addEventListener('transitionend', done, { once: true });
  setTimeout(done, 2500);
}
function morphOut() {
  return new Promise(res => {
    sheet.classList.remove('shown');
    sheet.classList.add('closing');
    const g = reduced() ? null : btnGeom();
    if (!g) { setTimeout(res, 140); return; }
    morph.hidden = false;
    placeMorph(g, g.s, false);
    void morph.offsetWidth;
    let fin = false;
    const done = () => { if (fin) return; fin = true; morph.hidden = true; res(); };
    setTimeout(() => {
      placeMorph(g, 1, true);
      morph.addEventListener('transitionend', done, { once: true });
      setTimeout(done, 1500);
    }, 110);
  });
}

async function open() {
  S.open = true; S.gen++; S.openAt = performance.now();
  S.seen.clear(); S.base = false; S.words = []; S.fireAct = {};
  S.linkUp = false; S.wantListen = true; S.userPaused = false; S.pendKey = ''; S.remote = false; S.hidden = false;
  S.energy = 0; S.hands = 0; S.prev = null; S.bankSize = null;
  T.until = 0; guessEl.textContent = ''; whyEl.textContent = '';
  pendEl.hidden = true; pendEl.textContent = ''; viewEl.classList.remove('has-ask', 'replay');
  tagEl.hidden = true; netEl.hidden = true;
  document.body.classList.add('live-open');
  S.link.reset();
  setListening(false);
  setMode(S.mode, false);
  morphIn();
  wakeLock(true);
  loadBank();
  loadApps();
  probeEngine();
  boot();
  clearInterval(S.stT);
  S.stT = setInterval(poll, ST_MS);
  poll();
}
async function close() {
  if (!S.open) return;
  S.open = false; S.gen++;
  S.link.stop();
  clearInterval(S.stT); S.stT = null;
  stopCam();
  if (S.replay) { try { S.replay.close(); } catch (e) {} S.replay = null; }
  S.engine = null;                     // the shared model stays loaded for next time
  wakeLock(false);
  // outside Go Live nothing should type words or keep listening
  App.api.post('/live/listen', { on: false }).catch(() => {});
  App.api.post('/live/mode', { mode: S.textLocal ? 'live' : 'control' }).catch(() => {});
  if (S.remote) App.api.post('/live/source', { source: 'phone' }).catch(() => {});
  S.remote = false;
  hideCard();
  await morphOut();
  document.body.classList.remove('live-open');
  const x = skel.getContext('2d');
  if (x) x.clearRect(0, 0, skel.width, skel.height);
  setListening(false);
  badgeT.textContent = 'off';
}

xBtn.onclick = () => App.back();
addEventListener('keydown', e => { if (e.key === 'Escape' && S.open && coverEl.hidden) App.back(); });
addEventListener('pagehide', () => { if (S.open) { S.link.stop(); stopCam(); } });

App.route('live', { el: sheet, open, close });
