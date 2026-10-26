// run.mjs — real-run proofs of the Go Live view (Kiro LIVE lane).
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
import { launchChrome, sleep, Page } from './cdp.mjs';
import * as HS from './handsynth.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const ROOT = path.resolve(HERE, '../..');
const STATE = process.env.LIVE_STATE || '/tmp/kiro-live';
const E = Object.fromEntries(fs.readFileSync(path.join(STATE, 'env'), 'utf8').split('\n')
  .filter(l => l.includes('=')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
const OUT = path.resolve(process.argv[2] || path.join(ROOT, 'proofs/ui-v2'));
const FLOWS = (process.argv[3] || 'seed,open,text,control,states,hands,perms,responsive,perf,empty').split(',');
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
    check(F, 'the real hand model reads the real camera frames and streams them to the laptop', n >= 4, `${n} frames in 3.0 s; one detection takes ${det} ms here (CPU wasm, headless, shared box)`);
    const mp = page.logs.filter(l => /graph|xnnpack|delegate|landmark/i.test(l)).slice(0, 2);
    if (mp.length) note('model log: ' + mp.join(' | ').slice(0, 160));
    let st = await liveState();
    const why = st.listening ? '' : ` · view: ${await text(page, '#lv-badge-t')} "${await text(page, '#lv-act-t')}" · last_match ${JSON.stringify(st.last_match || {}).slice(0, 120)} · feed ${JSON.stringify((st.feed || []).slice(-3)).slice(0, 200)} · listen calls ${JSON.stringify(page.listenLog)}`;
    check(F, 'opening Go Live starts listening and puts the laptop in Control', st.listening === true && st.text_mode === false, `listening=${st.listening} text_mode=${st.text_mode}${why}`);
    const words = await page.eval(`document.querySelector('#sheet-live').innerText`);
    check(F, 'no developer words on screen', !/python|daemon|wfd|keypoint|confidence|port\b|engine|dtw|d=/i.test(words), JSON.stringify(words.replace(/\s+/g, ' ').trim()).slice(0, 120));
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
    check(F, 'reopen: the hand model keeps reading (it is shared, never closed)', page.kp - k2 >= 4, `${page.kp - k2} frames in 3.0 s`);
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
  check(F, 'sign "world" -> the caption builds the sentence; the laptop has it too', /hello world/i.test(cap) && /hello world/.test(notesText()), `caption "${cap}" · "${sub}" · editor ${JSON.stringify(notesText())}`);
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
   hands: one hand controls the laptop. Air-write C -> Chrome, V -> YouTube,
   point + pinch selects a video, a finger slide scrolls the real page.
   Recorded: phone (CDP screencast) and laptop (x11grab).
   ====================================================================== */
const ZERO = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));
const raw = frames => frames.map(f => ({ points: f.lm ? HS.toKp42(f.lm) : ZERO }));
function palmMove({ x0 = .42, y0 = .5, x1 = .58, y1 = .44, ms = 1600 } = {}) {
  const n = Math.round(ms / 33), out = [];
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1), e = u * u * (3 - 2 * u);
    out.push({ t_ms: i * 33, lm: HS.handPose('palm', { x: x0 + (x1 - x0) * e, y: y0 + (y1 - y0) * e + .04 * Math.sin(u * 6.28) }) });
  }
  return out;
}
async function laptopChrome() {                       // the laptop Chrome's DevTools (test targeting only)
  const list = await (await fetch('http://127.0.0.1:8962/json/list')).json();
  const t = list.find(x => x.type === 'page' && /youtube\.com/.test(x.url)) || list.find(x => x.type === 'page');
  if (!t) return null;
  const pg = new Page(t.webSocketDebuggerUrl, 'https://www.youtube.com');
  await pg.ready;
  return pg;
}
async function lapEval(expr) {
  const pg = await laptopChrome(); if (!pg) return null;
  try { return await pg.eval(expr); } finally { try { pg.ws.close(); } catch (e) { /* closed */ } }
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
  const stopLap = startLaptopRec(path.join(RAW, 'hands-laptop.mp4'));
  let stopPhone = null;
  try {
    await home(page);
    stopPhone = await startPhoneRec(page, path.join(RAW, 'hands-phone'));
    await goLive(page);
    const clips = {
      palm: raw(palmMove()), airC: raw(HS.airC({ cx: .5, cy: .42, r: .15 })), vsign: raw(HS.vSign(1000)),
      idle: raw(HS.idle(600)), slideUp: raw(HS.slide({ x: .5, y0: .72, y1: .3, ms: 330 })),
    };
    for (const [k, v] of Object.entries(clips)) await page.eval(`window.__clips[${JSON.stringify(k)}] = ${JSON.stringify(v)}`);
    // 1) the hand lines follow the hand
    await page.eval(`window.__play('palm')`);
    await sleep(900);
    await shot(page, 'live-20-hand-lines');
    await page.waitFor('window.__idle()', 8000, 'palm clip');
    check(F, 'hand lines follow the hand (60 fps canvas, springs between frames)', await page.eval(`(() => { const c = document.querySelector('#lv-lines'); return c.width > 0 && c.height > 0; })()`), 'lines canvas live');
    // 2) air-write C -> Chrome
    const before = windows().filter(l => /google-chrome/i.test(l)).length;
    await page.eval(`window.__play('airC')`);
    await page.waitFor(`/opening chrome|opened google chrome/i.test(document.querySelector('#lv-act-t').textContent)`, 15000, 'C recognised');
    await sleep(380);
    await shot(page, 'live-21-air-C-ignites');
    await until(() => windows().filter(l => /google-chrome/i.test(l)).length > before, 20000, 'a Chrome window on the laptop');
    await page.waitFor(`/opened google chrome/i.test(document.querySelector('#lv-act-t').textContent)`, 8000, 'Opened Google Chrome');
    await sleep(4500);
    let phone = await shot(page, 'live-22-air-C-chrome-opened');
    compose(phone, xshot(path.join(RAW, 'hands-chrome.png')), 'live-22b-air-C-phone-and-laptop', 'air-written C opened Google Chrome');
    check(F, 'air "C" -> the stroke ignites into a C and Google Chrome opens on the laptop', true, `"${await text(page, '#lv-guess')}" · "${await text(page, '#lv-act-t')}" · chrome windows ${before} -> ${windows().filter(l => /google-chrome/i.test(l)).length}`);
    // 3) V sign -> YouTube
    await sleep(1800);
    await page.eval(`window.__play('vsign')`);
    await page.waitFor(`/opened youtube/i.test(document.querySelector('#lv-act-t').textContent)`, 15000, 'Opened YouTube');
    const yt = await until(async () => { try { return await lapEval(`location.host.endsWith('youtube.com') && document.readyState === 'complete' && location.href`); } catch (e) { return null; } }, 30000, 'YouTube loaded on the laptop');
    await sleep(5000);
    phone = await shot(page, 'live-23-V-youtube-opened');
    compose(phone, xshot(path.join(RAW, 'hands-youtube.png')), 'live-23b-V-phone-and-laptop', 'V sign opened YouTube');
    check(F, 'V sign -> YouTube opens on the laptop', !!yt, String(yt));
    // a search, typed through the phone's own text path (/send): "/" focuses YouTube search
    await api('POST', '/send', { text: '/', mode: 'type' }); await sleep(600);
    await api('POST', '/send', { text: 'lofi hip hop\n', mode: 'type' });
    const thumb = await until(() => lapEval(`(() => { if (location.pathname !== '/results') return null;
      const a = [...document.querySelectorAll('ytd-video-renderer a#thumbnail')].find(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.top > 120 && r.bottom < innerHeight; });
      if (!a) return null; const r = a.getBoundingClientRect(); const top = window.screenY + (outerHeight - innerHeight);
      return { x: Math.round(window.screenX + r.left + r.width / 2), y: Math.round(top + r.top + r.height / 2), href: a.href.split('&')[0] }; })()`), 30000, 'search results with a thumbnail');
    await sleep(1500);
    // 4) select: point at that thumbnail, pinch
    const tx = .12 + .76 * thumb.x / 1280, ty = .10 + .62 * thumb.y / 800;
    await page.eval(`window.__clips.hover = ${JSON.stringify(raw(HS.hover({ x0: .5, y0: .5, x1: tx, y1: ty, ms: 1100 })))}`);
    await page.eval(`window.__clips.hold = ${JSON.stringify(raw(HS.dwellAt({ x: tx, y: ty, ms: 700 })))}`);
    await page.eval(`window.__clips.pinch = ${JSON.stringify(raw(HS.pinchAt({ x: tx, y: ty })))}`);
    await page.eval(`window.__play('hover'); window.__play('hold')`);
    await sleep(1500);
    phone = await shot(page, 'live-24-point-to-select');
    compose(phone, xshot(path.join(RAW, 'hands-hover.png')), 'live-24b-point-phone-and-laptop', 'the finger moves the laptop cursor onto a video');
    await page.waitFor('window.__idle()', 8000, 'hover');
    await page.eval(`window.__play('pinch')`);
    const watch = await until(() => lapEval(`location.pathname === '/watch' && location.href.split('&')[0]`), 20000, 'the video page opened');
    await sleep(4000);
    phone = await shot(page, 'live-25-pinch-selected-video');
    compose(phone, xshot(path.join(RAW, 'hands-watch.png')), 'live-25b-select-phone-and-laptop', 'pinch clicked: the video opened');
    check(F, 'point + pinch selects a video on the laptop', watch && watch === thumb.href, `${thumb.href} -> ${watch}`);
    // 5) finger slide scrolls the real page
    const y0 = await lapEval('Math.round(scrollY)');
    for (let i = 0; i < 3; i++) { await page.eval(`window.__play('slideUp'); window.__play('idle')`); await sleep(900); }
    await sleep(1200);
    const y1 = await lapEval('Math.round(scrollY)');
    phone = await shot(page, 'live-26-finger-scroll');
    compose(phone, xshot(path.join(RAW, 'hands-scrolled.png')), 'live-26b-scroll-phone-and-laptop', `finger slides scrolled the page (scrollY ${y0} -> ${y1})`);
    check(F, 'one finger sliding up scrolls the real laptop page', y1 > y0 + 100, `scrollY ${y0} -> ${y1}`);
    check(F, 'no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
    await page.tap('#lv-x');
    await sleep(900);
  } finally {
    if (stopPhone) { const f = await stopPhone(path.join(RAW, 'hands-phone.mp4')).catch(() => null); if (f) note('phone recording ' + f); }
    await stopLap();
    await page.close();
    try {
      execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', path.join(RAW, 'hands-phone.mp4'), '-i', path.join(RAW, 'hands-laptop.mp4'),
        '-filter_complex', '[0:v]scale=-2:600,setsar=1[a];[1:v]scale=960:600,setsar=1[b];[a][b]hstack=inputs=2,format=yuv420p[v]', '-map', '[v]',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '30', '-shortest', path.join(OUT, 'live-hands-demo.mp4')]);
      note('recording: proofs/ui-v2/live-hands-demo.mp4 (phone | laptop)');
    } catch (e) { note('recording merge failed: ' + e.message.slice(0, 120)); }
  }
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
    check(F, 'blocked camera: a calm card with Try again', /try again/i.test(await text(page, '#lv-card-b')), await text(page, '#lv-card-t'));
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
    check(F, 'first use: "Allow the camera" while the browser asks', true, `permission state "${perm}"; the test held the browser prompt open`);
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
    check(F, 'plain http on the LAN: explains the secure link instead of failing silently', true, await text(page, '#lv-card-m'));
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
    await page.waitFor(`/taught any signs/i.test(document.querySelector('#lv-act-t').textContent)`, 8000, 'no-signs hint');
    await sleep(600);
    await shot(page, 'live-16-no-signs-yet');
    check(F, 'no signs yet: says so and offers Teach a sign', await visible(page, '#lv-cta'), await text(page, '#lv-cta'));
    await page.tap('#lv-cta');
    await page.waitFor(`location.hash === '#/teach'`, 5000, 'teach route');
    check(F, 'Teach a sign opens the Teach page', true);
  } finally { await page.close(); }
}

/* ---------- main ---------- */
const FN = { seed: flowSeed, open: flowOpen, text: flowText, control: flowControl, states: flowStates, hands: flowHands, perms: flowPerms, responsive: flowResponsive, perf: flowPerf, empty: flowEmpty };
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
