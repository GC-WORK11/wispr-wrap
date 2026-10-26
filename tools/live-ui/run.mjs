// run.mjs — real-run proofs of the Go Live view (live lane).
//
// What is real: the page in a real Chrome; the camera permission and stream
// (Chrome's fake camera device fed by a file: an abstract soft-focus scene, no
// people); the vendored hand model loading on the page; every HTTP call; the
// laptop daemon's endpointer, sign bank, text typing and action layer; the X11
// windows that open, close and receive keystrokes on the stack's own display.
// What is substituted: the hand detections. The fake camera has no hands in it,
// so hand positions come from the page's test seam (window.WFFakeTrack) playing
// synthetic clips (tools/live-ui/clips.py). The "open" flow runs WITHOUT the
// seam to show the real model reading the real camera frames.
//
// usage: node --experimental-websocket tools/live-ui/run.mjs [outdir] [flow,flow,...]
//   flows: seed open text control states perms responsive perf empty
// needs: tools/live-ui/boot.sh (writes $LIVE_STATE/env) and clips.py output.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { launchChrome, sleep } from './cdp.mjs';
import * as HS from './handsynth.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const ROOT = path.resolve(HERE, '../..');
const STATE = process.env.LIVE_STATE || '/tmp/wf-live';
const E = Object.fromEntries(fs.readFileSync(path.join(STATE, 'env'), 'utf8').split('\n')
  .filter(l => l.includes('=')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const OUT = path.resolve(process.argv[2] || path.join(ROOT, 'proofs/ui-v2'));
const FLOWS = (process.argv[3] || 'seed,open,text,control,states,hands,offline,perms,responsive,perf,empty').split(',');
const CLIPS = path.join(STATE, 'clips');
const RAW = path.join(STATE, 'raw');
fs.mkdirSync(OUT, { recursive: true });
fs.mkdirSync(RAW, { recursive: true });

/* ---------- bookkeeping ---------- */
const results = [];
const notes = [];
const scrub = s => String(s).split(E.KEY).join('<key>');
function check(flow, name, ok, detail = '') {
  results.push({ flow, name, ok: !!ok, detail: scrub(detail) });
  console.log((ok ? 'PASS ' : 'FAIL ') + flow + ' · ' + name + (detail ? '  (' + scrub(detail) + ')' : ''));
  return !!ok;
}
const note = s => { notes.push(scrub(s)); console.log('     ' + scrub(s)); };

/* ---------- laptop side ---------- */
async function api(method, p, body) {
  const r = await fetch(E.BASE + p, {
    method, headers: { 'X-WF-Key': E.KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const t = await r.text();
  let j = null;
  try { j = JSON.parse(t); } catch (e) { /* not json */ }
  return { status: r.status, json: j };
}
const X = (cmd, args) => execFileSync(cmd, args, { env: { ...process.env, DISPLAY: E.D }, encoding: 'utf8' }).trim();
/* managed top-level windows by WM_CLASS, read with xprop (wmctrl segfaults now and
   then on this box while a window is being destroyed) */
function windows() {
  let ids = [];
  try { ids = (X('xprop', ['-root', '_NET_CLIENT_LIST']).split('#')[1] || '').split(',').map(x => x.trim()).filter(Boolean); } catch (e) { return []; }
  return ids.map(id => { try { return id + ' ' + X('xprop', ['-id', id, 'WM_CLASS']); } catch (e) { return id + ' ?'; } });
}
const terms = () => windows().filter(l => /xfce4-terminal/i.test(l));
const notesText = () => { try { return fs.readFileSync(E.SINK, 'utf8'); } catch (e) { return ''; } };
function xshot(file) {
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'x11grab', '-video_size', '1280x800', '-i', E.D, '-frames:v', '1', file]);
  return file;
}
function compose(phone, laptop, name, caption) {
  const out = path.join(OUT, name + '.png');
  execFileSync('python3', [path.join(HERE, 'compose.py'), phone, laptop, out, caption || '']);
  return out;
}
const focusNotes = () => { try { X('xdotool', ['windowactivate', '--sync', E.SINK_WID]); } catch (e) { /* WM busy */ } };
async function until(fn, timeout, what) {
  const t0 = Date.now();
  for (;;) {
    let v = null;
    try { v = await fn(); } catch (e) { /* retry */ }
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error('timeout: ' + what);
    await sleep(120);
  }
}
const liveState = async () => (await api('GET', '/live/state')).json || {};

/* ---------- browser side ---------- */
const TRACKER = `(() => {
  const ABSENT = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));
  window.__q = []; window.__clips = {};
  window.WFFakeTrack = {
    start(video, cb) {
      const iv = setInterval(() => {
        const f = window.__q.shift();
        cb({ t_ms: performance.now(), points: f ? f.points : ABSENT });
      }, window.__tick || 66);
      return { stop() { clearInterval(iv); } };
    },
  };
  window.__play = name => { for (const f of window.__clips[name]) window.__q.push(f); return window.__q.length; };
  window.__idle = () => window.__q.length === 0;
})()`;
const FPS = `window.__fps = ms => new Promise(res => {
  const d = []; let last = performance.now(); const end = last + ms;
  const f = now => { d.push(now - last); last = now; if (now < end) requestAnimationFrame(f); else res(d); };
  requestAnimationFrame(f);
})`;

function freePort() {
  const used = execFileSync('ss', ['-tln'], { encoding: 'utf8' });
  for (const p of [8965, 8966, 8967, 8968, 8969]) if (!new RegExp('[:.]' + p + '\\b').test(used)) return p;
  throw new Error('no free CDP port in 8965-8969');
}
async function newPage({ viewport = [390, 844, true, 2], tracker = true, fakeUi = true, origin = E.BASE, cams = 1, file = true, tick = 0 } = {}) {
  const port = freePort();
  const page = await launchChrome({
    port, profile: path.join(STATE, 'chrome-' + port), origin,
    extra: ['--disable-gpu', `--use-fake-device-for-media-stream=device-count=${cams}`, ...(file ? [`--use-file-for-fake-video-capture=${E.CAM}`] : []),
      ...(fakeUi ? ['--use-fake-ui-for-media-stream'] : [])],
  });
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });   // never touch a sibling's replay service
  await page.send('Network.setBypassServiceWorker', { bypass: true });
  if (tick) await page.send('Page.addScriptToEvaluateOnNewDocument', { source: `window.__tick = ${tick};` });
  if (tracker) await page.send('Page.addScriptToEvaluateOnNewDocument', { source: TRACKER });
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: FPS });
  await page.viewport(viewport[0], viewport[1], { mobile: viewport[2], dpr: viewport[3] });
  page.kp = 0; page.listenLog = [];
  page.on('Network.requestWillBeSent', p => {
    if (/\/live\/kp$/.test(p.request.url)) page.kp++;
    if (/\/live\/(listen|mode)$/.test(p.request.url)) page.listenLog.push(p.request.url.split('/').pop() + ' ' + (p.request.postData || ''));
  });
  return page;
}
async function loadClips(page, names) {
  for (const n of names) {
    const j = JSON.parse(fs.readFileSync(path.join(CLIPS, n + '.json'), 'utf8'));
    await page.eval(`window.__clips[${JSON.stringify(n)}] = ${JSON.stringify(j.frames)}`);
  }
}
async function play(page, clip, timeout = 30000) {
  await page.eval(`window.__play(${JSON.stringify(clip)})`);
  await sleep(200);
  await page.waitFor('window.__idle()', timeout, 'clip ' + clip);
}
/* play a sign until the page shows `cond` (a box freeze can stall one try). Before
   each try the view must be listening; if it is not after 3 s, tap the hand
   button like a person would, and say so in the notes. */
async function perform(page, clip, cond, what, tries = 3) {
  for (let i = 1; i <= tries; i++) {
    const on = await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 3000, 'listening').catch(() => false);
    if (!on) { note(`${what}: view was paused before try ${i}; tapped the hand button`); await page.tap('#lv-listen'); await sleep(600); }
    await play(page, clip);
    try { await page.waitFor(cond, 9000, what); if (i > 1) note(`${what}: needed try ${i}`); return i; }
    catch (e) { note(`${what}: try ${i} timed out`); }
  }
  throw new Error('timeout: ' + what);
}
const shot = (page, name) => page.shot(path.join(OUT, name + '.png'));
const rawShot = (page, name) => page.shot(path.join(RAW, name + '.png'));
const text = (page, sel) => page.eval(`(document.querySelector(${JSON.stringify(sel)}) || {}).textContent || ''`);
const visible = (page, sel) => page.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e || e.hidden) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; })()`);
/* the words a person can see in the Go Live view: text nodes whose element is
   laid out, not clipped to 1px (screen-reader only), and not faded out */
const VISIBLE = `(() => {
  const root = document.querySelector('#sheet-live'), out = [];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const t = n.textContent.replace(/\\s+/g, ' ').trim();
    if (!t) continue;
    let el = n.parentElement, ok = true;
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) ok = false;
    for (let e = el; ok && e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e), b = e.getBoundingClientRect();
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity < .05 || e.hidden) ok = false;
      // screen-reader-only containers: clipped to a 1px box
      if ((cs.overflow === 'hidden' || cs.clip !== 'auto') && (b.width < 3 || b.height < 3)) ok = false;
    }
    if (ok) out.push(t);
  }
  return out;
})()`;
const visibleWords = page => page.eval(VISIBLE);
async function onlyShows(F, page, what, allowed) {
  const seen = await visibleWords(page);
  const extra = seen.filter(t => !allowed.some(a => a instanceof RegExp ? a.test(t) : a === t));
  return check(F, what, extra.length === 0, 'visible: ' + JSON.stringify(seen) + (extra.length ? ' · not allowed: ' + JSON.stringify(extra) : ''));
}
async function home(page) {
  await page.goto(E.BASE + '/?k=' + E.KEY, { settle: 2600 });
}
async function goLive(page, { tap = true } = {}) {
  if (tap) await page.tap('#tray-live'); else await page.click('#tray-live');
  await page.waitFor(`location.hash === '#/live'`, 5000, 'route #/live');
  await page.waitFor(`document.querySelector('#lv-cover').hidden && !!document.querySelector('#lv-cam').srcObject`, 45000, 'camera on, model loaded');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await sleep(700);
}
function stats(d) {
  const s = [...d].sort((a, b) => a - b);
  const q = p => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { frames: d.length, p50: +q(.5).toFixed(1), p95: +q(.95).toFixed(1), max: +s[s.length - 1].toFixed(1),
           over25: d.filter(x => x > 25).length, fps: +(1000 * d.length / d.reduce((a, b) => a + b, 0)).toFixed(1) };
}

/* ======================================================================
   seed: teach four signs through the daemon's real /teach endpoint
   ====================================================================== */
async function stream(clip, session) {
  const frames = JSON.parse(fs.readFileSync(path.join(CLIPS, clip + '.json'), 'utf8')).frames;
  for (const f of frames) {
    const t = Math.round(Date.now() - 1.78e12);       // the page's own clock domain
    const r = await api('POST', '/live/kp', { t_ms: t, points: f.points, session });
    if (r.status === 429) await sleep(250);
    await sleep(64);
  }
}
async function flowSeed() {
  const F = 'seed';
  const cat = (await api('GET', '/act')).json || {};
  const term = (cat.apps || []).find(a => a.id === 'xfce4-terminal');
  const closeRow = (cat.tools || []).find(t => t.id === 'app.close');
  check(F, 'laptop app list has the Terminal app and the "close app window" tool', term && closeRow, term ? term.name : 'missing');
  // exactly the dicts the @ picker builds (web/js/actions.js appAction / toolAction)
  const openAct = { kind: 'app', tool: 'app.open', type: 'open_app', args: { id: term.id }, id: term.id, label: 'open ' + term.name, tier: 1 };
  const closeAct = { kind: closeRow.kind || 'app', tool: 'app.close', type: 'close_window', label: closeRow.label, tier: closeRow.tier, args: { app: term.id } };
  const plan = [['HELLO', 'hello', null], ['WORLD', 'world', null], ['TERMINAL', 'open terminal', openAct], ['CLOSE', 'close terminal', closeAct]];
  for (const [g, label, action] of plan) {
    await api('DELETE', '/bank?gloss=' + g);
    const sid = 'seed-' + g.toLowerCase() + '-' + Date.now().toString(36);
    const b = await api('POST', '/teach/begin', { name: g, takes: 3, label, action, session: sid });
    await stream('idle', sid);
    await stream('teach-' + g, sid);
    await stream('idle', sid);
    const st = await liveState();
    if (st.teach && st.teach.active) await api('POST', '/teach/end', {});
    const rows = ((await api('GET', '/bank')).json || {}).signs || [];
    const row = rows.find(r => r.gloss === g);
    check(F, `taught ${g} ("${label}") x3 through /teach/begin + /live/kp`, b.status === 200 && row && row.n_exemplars >= 3,
      row ? `${row.n_exemplars} exemplars${action ? ', bound to ' + action.tool : ', a word only'}` : 'no bank row');
  }
  // a fresh stream for the page: a new session id resets the daemon's segmenter
  await api('POST', '/live/kp', { t_ms: Math.round(Date.now() - 1.78e12), points: Array.from({ length: 42 }, () => ({ x: 0, y: 0, score: 0 })), session: 'page-fresh-' + Date.now().toString(36) });
  await api('POST', '/live/listen', { on: false });
}

/* ======================================================================
   open: composer button -> Go Live, real camera + real model, close, reopen
   ====================================================================== */
async function flowOpen() {
  const F = 'open';
  const page = await newPage({ tracker: false });
  try {
    await home(page);
    await shot(page, 'live-01-home-composer');
    check(F, 'the black waveform button is on the composer', await visible(page, '#tray-live'));
    // freeze the open transition at two moments (Web Animations API) for the proof frames
    const freeze = ms => page.eval(`(async () => {
      for (let i = 0; i < 40 && !document.getAnimations().some(a => a.effect && a.effect.target && a.effect.target.id === 'lv-morph'); i++) await new Promise(r => setTimeout(r, 10));
      const all = document.getAnimations(); all.forEach(a => { a.pause(); a.currentTime = ${ms}; }); return all.length; })()`);
    const thaw = () => page.eval(`document.getAnimations().forEach(a => a.play())`);
    await page.tap('#tray-live');
    const nAnim = await freeze(150);
    await sleep(250);
    await shot(page, 'live-02-opening-from-the-button');
    await page.eval(`document.getAnimations().forEach(a => { a.currentTime = 330; })`);
    await sleep(250);
    await shot(page, 'live-02b-opening-later');
    await thaw();
    check(F, 'the black button grows into the camera view (transform transition)', nAnim > 0, nAnim + ' running animations frozen at 150 ms and 330 ms for the frames');
    await page.waitFor(`document.querySelector('#lv-cover').hidden && !!document.querySelector('#lv-cam').srcObject`, 45000, 'camera on, model loaded');
    await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
    await sleep(1500);
    await shot(page, 'live-03-control-listening');
    const cam = await page.eval(`(() => { const v = document.querySelector('#lv-cam'); const t = v.srcObject && v.srcObject.getVideoTracks()[0]; return { w: v.videoWidth, h: v.videoHeight, live: !!t && t.readyState === 'live', label: t ? t.label : '' }; })()`);
    check(F, 'camera stream is live in the view', cam.live && cam.w > 0, `${cam.w}x${cam.h} ${cam.label}`);
    await sleep(1500);                       // first detections warm the model up
    const k0 = page.kp; await sleep(3000); const n = page.kp - k0;
    const det = await page.eval(`(async () => {
      const { Pose } = await import('/js/pose.js'); const eng = await Pose.load(); const v = document.querySelector('#lv-cam'); const ts = [];
      for (let i = 0; i < 9; i++) { await new Promise(r => setTimeout(r, 40)); const a = performance.now(); eng.detectFrame(v, a); ts.push(performance.now() - a); }
      ts.sort((a, b) => a - b); return Math.round(ts[4]); })()`);
    check(F, 'the real hand model reads the real camera frames and streams them to the laptop', n >= 2, `${n} frames in 3.0 s; one detection takes ${det} ms here (CPU wasm, headless, shared box)`);
    const mp = page.logs.filter(l => /graph|xnnpack|delegate|landmark/i.test(l)).slice(0, 2);
    if (mp.length) note('model log: ' + mp.join(' | ').slice(0, 160));
    let st = await liveState();
    const why = st.listening ? '' : ` · view: ${await text(page, '#lv-badge-t')} "${await text(page, '#lv-act-t')}" · last_match ${JSON.stringify(st.last_match || {}).slice(0, 120)} · feed ${JSON.stringify((st.feed || []).slice(-3)).slice(0, 200)} · listen calls ${JSON.stringify(page.listenLog)}`;
    check(F, 'opening Go Live starts listening and puts the laptop in Control', st.listening === true && st.text_mode === false, `listening=${st.listening} text_mode=${st.text_mode}${why}`);
    await onlyShows(F, page, 'nothing but Text and Control on screen (no captions, tips or labels)', ['Text', 'Control']);
    await page.tap('#lv-x');
    await page.waitFor(`document.querySelector('#sheet-live').hidden && location.hash !== '#/live'`, 5000, 'closed');
    await sleep(500);
    const off = await page.eval(`(() => { const v = document.querySelector('#lv-cam'); return { src: !!v.srcObject }; })()`);
    const k1 = page.kp; await sleep(1500);
    st = await liveState();
    check(F, 'close: camera off, frames stop, laptop stops listening, back in Control', !off.src && page.kp - k1 === 0 && st.listening === false && st.text_mode === false,
      `camera=${off.src ? 'on' : 'off'} frames=${page.kp - k1} listening=${st.listening}`);
    await shot(page, 'live-04-closed-back-home');
    await page.tap('#tray-live');
    await page.waitFor(`document.querySelector('#lv-cover').hidden && !!document.querySelector('#lv-cam').srcObject`, 20000, 'reopen');
    await sleep(600);
    await sleep(800);
    const k2 = page.kp; await sleep(3000);
    check(F, 'reopen: the hand model keeps reading (it is shared, never closed)', page.kp - k2 >= 2, `${page.kp - k2} frames in 3.0 s`);
    await page.tap('#lv-x');
    await sleep(800);
    check(F, 'no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  } finally { await page.close(); }
}

/* ======================================================================
   text + control on one page, like a person would use it
   ====================================================================== */
let shared = null;
async function sharedPage() {
  if (shared) return shared;
  shared = await newPage();
  await home(shared);
  await loadClips(shared, ['palm-hold', 'namaste', 'idle', 'reject', 'perform-HELLO', 'perform-WORLD', 'perform-TERMINAL', 'perform-CLOSE']);
  await goLive(shared);
  return shared;
}
async function flowText() {
  const F = 'text';
  const page = await sharedPage();
  await page.tap('#lv-m-text');
  const st = await until(async () => { const s = await liveState(); return s.text_mode === true && s; }, 5000, 'laptop in text mode');
  check(F, 'Text tab tells the laptop to type words', st.text_mode === true, 'text_mode=true');
  await sleep(700);
  await shot(page, 'live-05-text-ready');
  focusNotes(); await sleep(300);
  const before = notesText();
  await perform(page, 'perform-HELLO', `/hello/i.test(document.querySelector('#lv-guess').textContent)`, 'caption hello');
  await until(() => notesText().slice(before.length).includes('hello'), 8000, 'laptop typed hello');
  check(F, 'sign "hello" -> caption + typed into the laptop editor', true, JSON.stringify(notesText().slice(before.length)));
  await sleep(500);
  await perform(page, 'perform-WORLD', `/hello\\s+world/i.test(document.querySelector('#lv-guess').textContent)`, 'caption hello world');
  await until(() => /hello world/.test(notesText().slice(before.length)), 8000, 'laptop typed hello world');
  await sleep(600);
  const phone = await shot(page, 'live-06-text-hello-world');
  const lap = xshot(path.join(RAW, 'text-laptop.png'));
  compose(phone, lap, 'live-06b-text-phone-and-laptop', 'the signed words typed into the focused editor');
  const cap = (await text(page, '#lv-guess')).replace(/\s+/g, ' ').trim();
  const sub = await text(page, '#lv-act-t');
  check(F, 'sign "world" -> the caption builds the sentence; the laptop has it too', /hello world/i.test(cap) && /hello world/.test(notesText()), `caption "${cap}" · status "${sub}" · editor ${JSON.stringify(notesText())}`);
  await onlyShows(F, page, 'Text mode shows only your words and the two mode labels', ['Text', 'Control', 'hello', 'world']);
}
async function flowControl() {
  const F = 'control';
  const page = await sharedPage();
  await page.tap('#lv-m-ctl');
  await until(async () => { const s = await liveState(); return s.text_mode === false; }, 5000, 'laptop in control mode');
  check(F, 'Control tab tells the laptop to run actions', true, 'text_mode=false');
  await sleep(500);
  const n0 = terms().length;
  await perform(page, 'perform-TERMINAL', `/open terminal/i.test(document.querySelector('#lv-guess').textContent)`, 'caption Open terminal');
  await until(() => terms().length > n0, 12000, 'a Terminal window opened');
  await page.waitFor(`/opened/i.test(document.querySelector('#lv-act-t').textContent)`, 8000, 'caption Opened ...');
  await sleep(1200);
  let phone = await shot(page, 'live-07-control-open-terminal');
  let lap = xshot(path.join(RAW, 'control-open.png'));
  compose(phone, lap, 'live-07b-control-phone-and-laptop', 'the sign opened a real Terminal window');
  check(F, 'sign "open terminal" -> a real Terminal window opens on the laptop', terms().length === n0 + 1,
    `windows ${n0} -> ${terms().length} · caption "${await text(page, '#lv-guess')}" · "${await text(page, '#lv-act-t')}"`);
  await sleep(1500);
  await perform(page, 'perform-CLOSE', `!document.querySelector('#lv-pend').hidden`, 'needs-your-OK row');
  await sleep(700);
  const ask = (await text(page, '#lv-pend')).replace(/\s+/g, ' ').trim();
  phone = await shot(page, 'live-08-control-needs-your-ok');
  lap = xshot(path.join(RAW, 'control-ask.png'));
  compose(phone, lap, 'live-08b-needs-ok-phone-and-laptop', 'tier-2 action held: the Terminal is still open');
  check(F, 'sign "close terminal" (tier 2) waits for an OK; nothing closed yet', terms().length === n0 + 1 && /close/i.test(ask), `"${ask}" · windows ${terms().length}`);
  await onlyShows(F, page, 'the OK card is just the question and two buttons', [/^Close .+\?$/, 'Not now', 'Run', 'Text', 'Control']);
  await page.tap('#lv-ok-run');
  await until(() => terms().length === n0, 10000, 'Terminal window closed');
  await page.waitFor(`/closed/i.test(document.querySelector('#lv-act-t').textContent)`, 6000, 'caption Closed ...');
  await sleep(700);
  phone = await shot(page, 'live-09-control-closed-after-ok');
  lap = xshot(path.join(RAW, 'control-closed.png'));
  compose(phone, lap, 'live-09b-closed-phone-and-laptop', 'after Run: the Terminal window is gone');
  check(F, 'Run -> the laptop closes the window; the caption says so', terms().length === n0, `windows ${terms().length} · "${await text(page, '#lv-act-t')}"`);
  await sleep(1200);
  await perform(page, 'reject', `/catch/i.test(document.querySelector('#lv-act-t').textContent)`, 'Didn’t catch that');
  await sleep(400);
  await shot(page, 'live-10-didnt-catch-that');
  check(F, 'an untaught movement fires nothing and says so quietly', terms().length === n0, `"${await text(page, '#lv-act-t')}"`);
}
async function flowStates() {
  const F = 'states';
  const page = await sharedPage();
  await sleep(2600);
  await play(page, 'namaste');
  await page.waitFor(`!document.querySelector('#lv-badge').classList.contains('listen')`, 10000, 'paused by palms together');
  await sleep(900);
  await shot(page, 'live-11-paused');
  check(F, 'palms together pauses (daemon + view agree)', (await liveState()).listening === false, await text(page, '#lv-act-t'));
  await play(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 10000, 'listening again');
  check(F, 'an open palm resumes', (await liveState()).listening === true);
  await page.tap('#lv-listen');
  await until(async () => (await liveState()).listening === false, 4000, 'paused by the button');
  await page.tap('#lv-listen');
  await until(async () => (await liveState()).listening === true, 4000, 'resumed by the button');
  check(F, 'the hand button pauses and resumes', true);
  await page.tap('#lv-flip');
  await page.waitFor(`/no other camera/i.test(document.querySelector('#lv-act-t').textContent)`, 8000, 'one-camera note');
  await page.waitFor(`(() => { const s = document.querySelector('#lv-cam').srcObject; return !!s && s.getVideoTracks()[0].readyState === 'live'; })()`, 8000, 'camera back');
  check(F, 'flip on a one-camera device: says so, camera stays on', true, await text(page, '#lv-act-t'));
  await sleep(1200);
  check(F, 'no page errors on the shared page', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  await page.tap('#lv-x');
  await page.waitFor(`document.querySelector('#sheet-live').hidden`, 5000, 'closed');
  const st = await liveState();
  check(F, 'closing restores Control and stops listening', st.text_mode === false && st.listening === false, `text_mode=${st.text_mode} listening=${st.listening}`);
  await shared.close(); shared = null;
  // a device with two cameras (Chrome's two built-in fake cameras: a test pattern, no people)
  const two = await newPage({ tracker: false, cams: 2, file: false });
  try {
    await home(two);
    await two.tap('#tray-live');
    await two.waitFor(`document.querySelector('#lv-cover').hidden && !!document.querySelector('#lv-cam').srcObject`, 45000, 'camera on');
    const before = await two.eval(`document.querySelector('#lv-cam').srcObject.getVideoTracks()[0].getSettings().deviceId`);
    const m0 = await two.eval(`document.querySelector('#lv-cam').classList.contains('rear')`);
    await two.tap('#lv-flip');
    await two.waitFor(`(() => { const s = document.querySelector('#lv-cam').srcObject; return s && s.getVideoTracks()[0].getSettings().deviceId !== ${JSON.stringify(before)}; })()`, 8000, 'other camera');
    // the mirror flips once the new stream plays (the view is faded out meanwhile)
    const m1 = await two.waitFor(`document.querySelector('#lv-cam').classList.contains('rear') && !document.querySelector('#lv-cam').classList.contains('swap')`, 4000, 'rear view').catch(() => false);
    check(F, 'flip on a two-camera device: switches camera; the selfie view is mirrored, the other is not', !m0 && m1, `mirrored before=${!m0} after=${!m1}`);
    await two.tap('#lv-x');
    await sleep(600);
  } finally { await two.close(); }
}

/* ======================================================================
   hands: one hand controls the laptop, the way a person would use it:
   hand lines -> air-write C (Chrome opens) -> V sign (YouTube opens) -> point at
   the search box and pinch (the phone types the search through its own text
   path) -> finger slides scroll the results -> point at a video -> pinch opens it.
   Every check reads the laptop's real screen or window titles; nothing
   instruments the laptop's browser. Recorded: phone (CDP screencast) | laptop.
   ====================================================================== */
const ZERO = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));
const raw = frames => frames.map(f => ({ points: f.lm ? HS.toKp42(f.lm) : ZERO }));
function palmMove({ x0 = .46, y0 = .62, x1 = .54, y1 = .56, ms = 1600 } = {}) {
  const n = Math.round(ms / 33), out = [];
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1), e = u * u * (3 - 2 * u);
    out.push({ t_ms: i * 33, lm: HS.handPose('palm', { x: x0 + (x1 - x0) * e, y: y0 + (y1 - y0) * e + .04 * Math.sin(u * 6.28) }, { scale: .09 }) });
  }
  return out;
}
const titles = () => windows().map(l => { try { return X('xprop', ['-id', l.split(' ')[0], 'WM_NAME']).split(' = ')[1] || ''; } catch (e) { return ''; } });
const chromes = () => windows().filter(l => /google-chrome/i.test(l)).length;
const SCREEN = path.join(HERE, 'screen.py');
const scr = (...a) => execFileSync('python3', [SCREEN, ...a.map(String)], { encoding: 'utf8' }).trim();
function findThumb(png) { try { const [x, y] = scr('thumb', png).split(/\s+/).map(Number); return Number.isFinite(x) ? { x, y } : null; } catch (e) { return null; } }
/* laptop screen px -> where the finger must be in the camera (inverse of live.js lapX/lapY) */
const camAt = (sx, sy) => ({ x: .12 + .76 * sx / 1280, y: .10 + .62 * sy / 800 });
async function pointAndPinch(page, from, to) {
  const clip = [...raw(HS.hover({ x0: from.x, y0: from.y, x1: to.x, y1: to.y, ms: 1100 })),
    ...raw(HS.dwellAt({ x: to.x, y: to.y, ms: 300 }))];
  await page.eval(`window.__clips.go = ${JSON.stringify(clip)}; window.__clips.pinch = ${JSON.stringify(raw(HS.pinchAt(to)))}`);
  await page.eval(`window.__play('go')`);
  await page.waitFor('window.__idle()', 15000, 'hover');
}
function startLaptopRec(file) {
  const p = spawn('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'x11grab', '-framerate', '8', '-video_size', '1280x800', '-i', E.D,
    '-vf', 'scale=960:600', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '31', '-pix_fmt', 'yuv420p', file], { stdio: ['pipe', 'ignore', 'ignore'] });
  return () => new Promise(r => { p.on('exit', r); try { p.stdin.write('q'); p.stdin.end(); } catch (e) { p.kill('SIGINT'); } setTimeout(() => { try { p.kill('SIGKILL'); } catch (e) { /* gone */ } r(); }, 6000); });
}
async function startPhoneRec(page, dir) {
  fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
  const frames = [];
  page.on('Page.screencastFrame', async p => {
    const f = path.join(dir, String(frames.length).padStart(5, '0') + '.jpg');
    fs.writeFileSync(f, Buffer.from(p.data, 'base64'));
    frames.push({ f, t: p.metadata.timestamp });
    try { await page.send('Page.screencastFrameAck', { sessionId: p.sessionId }); } catch (e) { /* closing */ }
  });
  await page.send('Page.startScreencast', { format: 'jpeg', quality: 70, maxWidth: 780, maxHeight: 1688, everyNthFrame: 1 });
  return async out => {
    try { await page.send('Page.stopScreencast'); } catch (e) { /* gone */ }
    if (frames.length < 2) return null;
    const list = frames.map((x, i) => `file '${x.f}'\nduration ${Math.max(.02, ((frames[i + 1] || x).t - x.t) || .1).toFixed(3)}`).join('\n') + `\nfile '${frames[frames.length - 1].f}'\n`;
    fs.writeFileSync(path.join(dir, 'list.txt'), list);
    execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', path.join(dir, 'list.txt'),
      '-vf', 'fps=15,scale=390:-2', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '30', '-pix_fmt', 'yuv420p', out]);
    return out;
  };
}
async function flowHands() {
  const F = 'hands';
  const page = await newPage({ tick: 33 });
  let stopLap = null, stopPhone = null, tLap = 0, tBlur = 0;
  try {
    await home(page);
    stopPhone = await startPhoneRec(page, path.join(RAW, 'hands-phone'));
    stopLap = startLaptopRec(path.join(RAW, 'hands-laptop.mp4')); tLap = Date.now();
    await goLive(page);
    const C = raw(HS.airC({ cx: .5, cy: .42, r: .15 }));
    const clips = { palm: raw(palmMove()), airC: C, vsign: raw(HS.vSign(1000)), idle: raw(HS.idle(600)),
      slideUp: raw(HS.slide({ x: .5, y0: .72, y1: .3, ms: 330 })) };
    for (const [k, v] of Object.entries(clips)) await page.eval(`window.__clips[${JSON.stringify(k)}] = ${JSON.stringify(v)}`);

    // 1) the hand lines follow the hand
    await page.eval(`window.__play('palm')`);
    await sleep(900);
    await shot(page, 'live-20-hand-lines');
    const inked = await page.eval(`(() => { const c = document.querySelector('#lv-lines'), d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 16) if (d[i] > 40) n++; return n; })()`);
    await page.waitFor('window.__idle()', 8000, 'palm clip');
    check(F, 'thin hand lines follow the hand on the camera view', inked > 150, `${inked} drawn pixels (sampled)`);

    // 2) air-write C -> Chrome
    const c0 = chromes();
    await page.eval(`window.__play('airC')`);
    await sleep(Math.round(C.length * 33 * .5));
    await shot(page, 'live-21-air-C-drawing');
    await page.waitFor(`/opening chrome|opened chrome/i.test(document.querySelector('#lv-act-t').textContent)`, 15000, 'C recognised');
    await shot(page, 'live-21b-air-C-ignites');
    await sleep(380);
    await shot(page, 'live-21c-air-C-glyph');
    await page.waitFor(`!!document.querySelector('#lv-guess .lv-st.ok')`, 15000, 'the laptop confirmed it ran');
    await sleep(350);
    await shot(page, 'live-21d-air-C-done');
    await until(() => chromes() > c0, 25000, 'a Chrome window on the laptop');
    await page.waitFor(`document.querySelector('#lv-view').classList.contains('browse')`, 8000, 'pointing after Chrome opened');
    await sleep(4500);
    let phone = await shot(page, 'live-22-air-C-chrome-opened');
    compose(phone, xshot(path.join(RAW, 'hands-chrome.png')), 'live-22b-air-C-phone-and-laptop', 'air-written C opened Google Chrome');
    check(F, 'air "C" ignites into a glyph and Google Chrome opens on the laptop', true, `"${await text(page, '#lv-guess')}" · Chrome windows ${c0} -> ${chromes()}`);
    await onlyShows(F, page, 'after C: only the action (with its check) and the mode labels', ['Open Chrome', 'Text', 'Control']);

    // 3) V sign -> YouTube
    await sleep(1500);
    await page.eval(`window.__play('vsign')`);
    await page.waitFor(`/youtube/i.test(document.querySelector('#lv-guess').textContent)`, 15000, 'V recognised');
    const yt = await until(() => titles().find(t => /YouTube/.test(t)), 40000, 'YouTube on the laptop');
    await sleep(6000);
    phone = await shot(page, 'live-23-V-youtube-opened');
    compose(phone, xshot(path.join(RAW, 'hands-youtube.png')), 'live-23b-V-phone-and-laptop', 'V sign opened YouTube');
    check(F, 'V sign opens YouTube on the laptop', !!yt, yt);

    // 4) point at YouTube's search box, pinch; the phone sends the search as text
    await sleep(1200);
    const box = camAt(580, 114);
    await pointAndPinch(page, { x: .5, y: .55 }, box);
    phone = await shot(page, 'live-24-point-search');
    compose(phone, xshot(path.join(RAW, 'hands-search.png')), 'live-24b-point-phone-and-laptop', 'the finger moved the laptop cursor to the search box');
    await page.eval(`window.__play('pinch')`);
    await page.waitFor('window.__idle()', 8000, 'pinch');
    await sleep(900);
    const sent = await api('POST', '/send', { text: 'lofi girl\n', mode: 'type' });
    const res = await until(() => titles().find(t => /^"?lofi girl - YouTube/.test(t)), 30000, 'search results on the laptop');
    check(F, 'pinch on the search box focuses it; the phone types the search', sent.status === 200 && !!res, res);
    await sleep(5000);

    // 5) finger slides scroll the results
    const before = xshot(path.join(RAW, 'hands-scroll-a.png'));
    for (let i = 0; i < 3; i++) { await page.eval(`window.__play('slideUp'); window.__play('idle')`); await sleep(1100); }
    await sleep(1500);
    phone = await shot(page, 'live-25-finger-scroll');
    const after = xshot(path.join(RAW, 'hands-scroll-b.png'));
    compose(phone, after, 'live-25b-scroll-phone-and-laptop', 'three upward finger slides scrolled the results');
    const moved = +scr('diff', before, after, 620, 150, 1270, 790);
    check(F, 'one finger sliding up scrolls the real laptop page', moved > 3, `the results text column changed by ${moved} (mean abs pixel difference)`);

    // 6) point at a fully visible video thumbnail, pinch: the video opens
    let t = findThumb(after);
    if (!t) { await sleep(2500); t = findThumb(xshot(path.join(RAW, 'hands-scroll-c.png'))); }
    if (!t) throw new Error('no fully visible video thumbnail on the laptop screen');
    const target = camAt(t.x, t.y);
    await pointAndPinch(page, { x: .5, y: .5 }, target);
    phone = await shot(page, 'live-26-point-to-video');
    compose(phone, xshot(path.join(RAW, 'hands-hover.png')), 'live-26b-point-phone-and-laptop', `the finger moved the laptop cursor onto a video (${t.x}, ${t.y})`);
    tBlur = Date.now();
    await page.eval(`window.__play('pinch')`);
    const video = await until(() => titles().find(t2 => / - YouTube/.test(t2) && !/^"?(lofi girl - )?YouTube - Google Chrome/.test(t2)), 30000, 'the video page opened');
    await sleep(4500);
    phone = await shot(page, 'live-27-pinch-opens-video');
    const watch = xshot(path.join(RAW, 'hands-watch.png'));
    scr('redact', watch, watch);                // pre-roll ads can show real people
    compose(phone, watch, 'live-27b-select-phone-and-laptop', 'pinch opened the video (player and side column blurred: ads can show people)');
    check(F, 'point + pinch selects a video on the laptop', !!video, video);
    check(F, 'no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
    await page.tap('#lv-x');
    await sleep(900);
  } finally {
    const ph = stopPhone ? await stopPhone(path.join(RAW, 'hands-phone.mp4')).catch(() => null) : null;
    if (stopLap) await stopLap();
    await page.close();
    if (ph && fs.existsSync(path.join(RAW, 'hands-laptop.mp4'))) {
      const T = tBlur ? Math.max(0, (tBlur - tLap) / 1000 - .3).toFixed(2) : '99999';
      try {
        execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', ph, '-i', path.join(RAW, 'hands-laptop.mp4'),
          '-filter_complex', `[0:v]scale=-2:600,setsar=1[a];[1:v]scale=960:600,setsar=1,split[b0][b1];[b1]crop=960:495:0:105,gblur=sigma=18[bb];[b0][bb]overlay=0:105:enable='gte(t,${T})'[b];[a][b]hstack=inputs=2,format=yuv420p[v]`,
          '-map', '[v]', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '30', '-shortest', path.join(OUT, 'live-hands-demo.mp4')]);
        note(`recording: proofs/ui-v2/live-hands-demo.mp4 (phone | laptop; the laptop page is blurred from ${T} s, when the video opens)`);
      } catch (e) { note('recording merge failed: ' + e.message.slice(0, 160)); }
    }
  }
}

/* ======================================================================
   offline: no laptop connected -> Control offers to connect and sends nothing
   ====================================================================== */
async function flowOffline() {
  const F = 'offline';
  const page = await newPage({ tick: 33 });
  try {
    let events = 0;
    page.on('Network.requestWillBeSent', p => { if (/\/live\/event$/.test(p.request.url)) events++; });
    await home(page);
    await goLive(page);
    // the laptop link drops while Go Live is open (the shell hides Go Live when it starts unconnected)
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', '*/health*'] });
    await page.waitFor(`!document.querySelector('#lv-cta').hidden && /connect your laptop/i.test(document.querySelector('#lv-cta').textContent)`, 20000, 'Connect your laptop');
    await page.eval(`window.__clips.airC = ${JSON.stringify(raw(HS.airC({ cx: .5, cy: .42, r: .15 })))}`);
    await page.eval(`window.__play('airC')`);
    await page.waitFor(`/connect your laptop/i.test(document.querySelector('#lv-act-t').textContent)`, 15000, 'asks to connect');
    await sleep(800);
    await shot(page, 'live-17-not-connected');
    check(F, 'laptop link lost: Control offers "Connect your laptop" and a drawn C sends nothing', events === 0, `${events} laptop events sent · status "${await text(page, '#lv-act-t')}"`);
    await onlyShows(F, page, 'not connected shows only Connect your laptop, the drawn action and the mode labels', ['Connect your laptop', 'Open Chrome', 'Text', 'Control']);
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
    await page.tap('#lv-x');
    await sleep(600);
  } finally { await page.close(); }
}

/* ======================================================================
   perms: blocked camera, the browser asking, an insecure link
   ====================================================================== */
async function flowPerms() {
  const F = 'perms';
  let page = await newPage({ fakeUi: false });
  try {
    await page.send('Browser.setPermission', { permission: { name: 'camera' }, setting: 'denied', origin: E.BASE });
    await home(page);
    await page.tap('#tray-live');
    await page.waitFor(`/camera is off/i.test(document.querySelector('#lv-card-t').textContent) && !document.querySelector('#lv-cover').hidden`, 10000, 'blocked card');
    await sleep(700);
    await shot(page, 'live-12-camera-blocked');
    check(F, 'blocked camera: an icon and Try again (the sentence is for screen readers)', /try again/i.test(await text(page, '#lv-card-b')), 'aria label: ' + await text(page, '#lv-card-t'));
    await onlyShows(F, page, 'blocked camera shows only the Try again button', ['Try again']);
    await page.send('Browser.setPermission', { permission: { name: 'camera' }, setting: 'granted', origin: E.BASE });
    await page.eval(`[...document.querySelectorAll('#lv-card-b button')].find(b => /try again/i.test(b.textContent)).click()`);
    await page.waitFor(`document.querySelector('#lv-cover').hidden && !!document.querySelector('#lv-cam').srcObject`, 30000, 'camera on after allowing');
    check(F, 'allow + Try again -> the camera comes on without reloading', true);
  } finally { await page.close(); }

  // first use: headless Chrome answers a camera prompt at once, so the test holds
  // the browser's question open (getUserMedia never settles) to photograph the card
  page = await newPage({ fakeUi: false });
  try {
    await page.send('Page.addScriptToEvaluateOnNewDocument', { source: `navigator.mediaDevices.getUserMedia = () => new Promise(() => {});` });
    await home(page);
    await page.tap('#tray-live');
    await page.waitFor(`/allow the camera/i.test(document.querySelector('#lv-card-t').textContent) && !document.querySelector('#lv-cover').hidden`, 8000, 'allow card');
    await sleep(800);
    await shot(page, 'live-13-allow-camera');
    const perm = await page.eval(`navigator.permissions.query({ name: 'camera' }).then(p => p.state)`);
    check(F, 'first use: a camera icon while the browser asks', /allow the camera/i.test(await text(page, '#lv-card-t')), `permission state "${perm}"; the test held the browser prompt open`);
    await onlyShows(F, page, 'while the browser asks: no words at all', []);
  } finally { await page.close(); }

  const lan = os.networkInterfaces();
  const ip = Object.values(lan).flat().find(a => a && a.family === 'IPv4' && !a.internal);
  if (!ip) { note('no LAN address: insecure-link check skipped'); return; }
  const origin = `http://${ip.address}:${E.PORT}`;
  page = await newPage({ origin });
  try {
    await page.goto(origin + '/?k=' + E.KEY, { settle: 2600 });
    await page.tap('#tray-live');
    await page.waitFor(`/secure/i.test(document.querySelector('#lv-card-t').textContent)`, 10000, 'secure-link card');
    await sleep(700);
    await shot(page, 'live-14-needs-secure-link');
    check(F, 'plain http on the LAN: a lock icon instead of a broken camera', true, 'aria: ' + await text(page, '#lv-card-t'));
    await onlyShows(F, page, 'insecure link: no words on screen', ['Use the laptop camera']);
  } finally { await page.close(); }
}

/* ======================================================================
   responsive: tablet + laptop browser
   ====================================================================== */
async function flowResponsive() {
  const F = 'responsive';
  for (const [name, vp, mode] of [['tablet', [820, 1180, true, 2], 'text'], ['laptop', [1440, 900, false, 1], 'control']]) {
    const page = await newPage({ viewport: vp });
    try {
      await home(page);
      await loadClips(page, ['perform-HELLO', 'perform-WORLD', 'perform-TERMINAL', 'perform-CLOSE']);
      await goLive(page, { tap: vp[2] });
      const sel = mode === 'text' ? '#lv-m-text' : '#lv-m-ctl';
      if (vp[2]) await page.tap(sel); else await page.click(sel);
      await sleep(800);
      if (mode === 'text') {
        focusNotes();
        await play(page, 'perform-HELLO'); await sleep(400); await play(page, 'perform-WORLD');
        await page.waitFor(`/world/i.test(document.querySelector('#lv-guess').textContent)`, 12000, 'caption');
      } else {
        const n0 = terms().length;
        await play(page, 'perform-TERMINAL');
        await page.waitFor(`/opened/i.test(document.querySelector('#lv-act-t').textContent)`, 12000, 'opened');
        await until(() => terms().length > n0, 8000, 'terminal');
      }
      await sleep(900);
      await shot(page, `live-15-${name}-${mode}`);
      const box = await page.eval(`(() => { const r = document.querySelector('.lv-row').getBoundingClientRect(); const s = document.querySelector('#lv-seg').getBoundingClientRect(); return { rowW: Math.round(r.width), segX: Math.round(s.left + s.width / 2), vw: innerWidth }; })()`);
      check(F, `${name} ${vp[0]}x${vp[1]}: controls centred and sized for the screen`, Math.abs(box.segX - box.vw / 2) <= 2 && box.rowW <= 440, JSON.stringify(box));
      if (mode === 'control') {
        await play(page, 'perform-CLOSE');
        await page.waitFor(`!document.querySelector('#lv-pend').hidden`, 12000, 'ask');
        await page.click('#lv-ok-run');
        await sleep(1500);
      }
      check(F, `${name}: no page errors`, page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
    } finally { await page.close(); }
  }
}

/* ======================================================================
   perf: frame pacing while the view animates (plain, and with a 4x slower CPU)
   ====================================================================== */
async function flowPerf() {
  const F = 'perf';
  const lines = [];
  for (const rate of [1, 4]) {
    const page = await newPage();
    try {
      await home(page);
      await loadClips(page, ['perform-HELLO', 'perform-WORLD']);
      if (rate > 1) await page.send('Emulation.setCPUThrottlingRate', { rate });
      const pOpen = page.eval('window.__fps(1200)');
      await page.tap('#tray-live');
      const open = stats(await pOpen);
      await page.waitFor(`document.querySelector('#lv-cover').hidden && document.querySelector('#lv-badge').classList.contains('listen')`, 60000, 'ready');
      await sleep(800);
      const idle = stats(await page.eval('window.__fps(3000)'));
      await page.tap('#lv-m-text');
      await sleep(500);
      const pSign = page.eval('window.__fps(4000)');
      await page.eval(`window.__play('perform-HELLO')`);
      const sign = stats(await pSign);
      for (const [what, s] of [['open (button grows into the view)', open], ['listening, camera live', idle], ['signing: caption + hand ring animating', sign]]) {
        lines.push(`cpu x${rate}  ${what.padEnd(38)} ${String(s.fps).padStart(5)} fps  p50 ${s.p50} ms  p95 ${s.p95} ms  max ${s.max} ms  frames>25ms ${s.over25}/${s.frames}`);
      }
      check(F, `cpu x${rate}: listening + signing hold ~60 fps (p95 frame <= 20 ms)`, idle.p95 <= 20 && sign.p95 <= 20, `idle p95 ${idle.p95} ms, signing p95 ${sign.p95} ms`);
      if (rate > 1) await page.send('Emulation.setCPUThrottlingRate', { rate: 1 });
      await page.tap('#lv-x');
      await sleep(700);
    } finally { await page.close(); }
  }
  fs.writeFileSync(path.join(OUT, 'live-PERF.txt'), [
    'Go Live frame pacing (requestAnimationFrame intervals in headless Chrome, 390x844 @2x, software compositing on a shared box).',
    'cpu x4 = DevTools CPU throttling, a rough stand-in for a mid-range phone.', '', ...lines, ''].join('\n'));
  lines.forEach(l => note(l));
}

/* ======================================================================
   empty: a laptop with no signs taught yet (runs last: it clears the bank)
   ====================================================================== */
async function flowEmpty() {
  const F = 'empty';
  for (const g of ['HELLO', 'WORLD', 'TERMINAL', 'CLOSE']) await api('DELETE', '/bank?gloss=' + g);
  const page = await newPage();
  try {
    await home(page);
    await goLive(page);
    await page.tap('#lv-m-text');
    await page.waitFor(`/no signs yet/i.test(document.querySelector('#lv-act-t').textContent)`, 8000, 'no-signs hint');
    await sleep(600);
    await shot(page, 'live-16-no-signs-yet');
    check(F, 'no signs yet: one Teach a sign button', await visible(page, '#lv-cta'), await text(page, '#lv-cta'));
    await onlyShows(F, page, 'no signs yet shows only Teach a sign and the mode labels', ['Teach a sign', 'Text', 'Control']);
    await page.tap('#lv-cta');
    await page.waitFor(`location.hash === '#/teach'`, 5000, 'teach route');
    check(F, 'Teach a sign opens the Teach page', true);
  } finally { await page.close(); }
}

/* ---------- main ---------- */
const FN = { seed: flowSeed, open: flowOpen, text: flowText, control: flowControl, states: flowStates, hands: flowHands, offline: flowOffline, perms: flowPerms, responsive: flowResponsive, perf: flowPerf, empty: flowEmpty };
const t0 = Date.now();
for (const f of FLOWS) {
  if (!FN[f]) { console.log('unknown flow ' + f); continue; }
  console.log('---- ' + f);
  try { await FN[f](); }
  catch (e) {
    check(f, 'flow ran to the end', false, e.message);
    if (shared) { try { await shared.shot(path.join(RAW, 'fail-' + f + '.png')); } catch (e2) { /* gone */ } }
  }
}
if (shared) { await shared.close(); shared = null; }
const pass = results.filter(r => r.ok).length, fail = results.length - pass;
const head = [
  `Go Live real-run proofs  ${new Date().toISOString()}  (${((Date.now() - t0) / 1000).toFixed(0)} s)`,
  `stack: real Chrome ${execFileSync('google-chrome', ['--version'], { encoding: 'utf8' }).trim()} · daemon server/wfd.py @ ${execFileSync('git', ['-C', ROOT, 'rev-parse', '--short', 'HEAD'], { encoding: 'utf8' }).trim()} · own Xvfb ${E.D} + xfwm4 · scratch HOME`,
  `page: web/ from ${E.WEB_SRC}${E.WEB_SRC.startsWith(ROOT) ? '' : ' with this tree\'s js/live.js + css/live.css'}`,
  'hand positions: synthetic clips through the page test seam (no video, no people); camera + model are real',
  `RESULT: ${fail ? 'FAIL' : 'PASS'}  ${pass} pass / ${fail} fail`, '',
];
const body = results.map(r => `${r.ok ? 'PASS' : 'FAIL'}  ${r.flow.padEnd(10)} ${r.name}${r.detail ? '\n      ' + r.detail : ''}`);
fs.writeFileSync(path.join(OUT, 'live-RESULTS.txt'), [...head, ...body, '', ...(notes.length ? ['notes:', ...notes.map(n => '  ' + n)] : []), ''].join('\n'));
console.log(`\nRESULT: ${fail ? 'FAIL' : 'PASS'}  ${pass} pass / ${fail} fail  -> ${path.join(OUT, 'live-RESULTS.txt')}`);
process.exit(fail ? 1 : 0);
