// flows.mjs — real-run proofs of the hero flows in a real browser against a
// real wfd.py + real X11 (the realrun stack on MY display/port):
//
//   send     type text, attach a photo, a video and a file -> real sink / clipboard / uploads
//   teach    Teach a sign through the UI (3 takes through the daemon's own endpointer),
//            bind "Type text" with the @ picker (keyboard only), Finish -> live
//   live     Go Live on a real (fake-device) camera + the real MediaPipe model,
//            open palm -> listening, perform the sign -> the real OS action lands
//
// What is real: the page, the camera permission + stream, MediaPipe load, every
// HTTP call, the daemon's endpointer/DTW/action layer, the X11 typing.
// What is substituted: the hand detections only. The Chrome fake camera shows a
// test pattern (no hands), so keypoints come from the app's own test seam
// (window.WFFakeTrack) playing synthetic kp42 clips (tools/demo/clips, no video,
// no people). Labelled as such in every proof.
//
// usage: node --experimental-websocket tools/peak/flows.mjs <outdir> <flow[,flow]> [cdp-port]
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack, scrub } from './env.mjs';

const out = process.argv[2] || 'proofs/peak/flows';
const flows = (process.argv[3] || 'send,teach,live').split(',');
const cdpPort = Number(process.argv[4] || 8951);
const S = loadStack();
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const CLIPS = path.join(root, 'tools/demo/clips');
fs.mkdirSync(out, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  — ' + detail : ''));
};
const sh = (cmd, args) => execFileSync(cmd, args, { env: { ...process.env, DISPLAY: S.D }, encoding: 'utf8' });
const sink = () => fs.readFileSync(S.SINK, 'utf8');
const api = async (method, p, body) => {
  const r = await fetch(S.base + p, { method, headers: { 'X-WF-Key': S.KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  let j = null; const t = await r.text();
  try { j = JSON.parse(t); } catch (e) {
    try { j = JSON.parse(t.replace(/([:\[,]\s*)-?(?:Infinity|NaN)(?=\s*[,}\]])/g, '$1null')); } catch (e2) { /* not json */ }
  }
  return { status: r.status, json: j };
};

// ---- in-page fake tracker: plays synthetic kp42 clips at wall-clock pace ----
const TRACKER = `(() => {
  const ABSENT = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));
  window.__q = []; window.__clips = {};
  window.WFFakeTrack = {
    start(video, cb) {
      const iv = setInterval(() => {
        const f = window.__q.shift();
        cb({ t_ms: performance.now(), points: f ? f.points : ABSENT });
      }, 66);
      return { stop() { clearInterval(iv); } };
    },
  };
  window.__play = (name) => { for (const f of window.__clips[name]) window.__q.push(f); return window.__q.length; };
  window.__idle = () => window.__q.length === 0;
})()`;

async function newPage(viewport = [1280, 800, false, 1]) {
  const profile = '/tmp/peak-chrome-flows';
  fs.rmSync(profile, { recursive: true, force: true });
  const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });   // never touch a sibling's replay seam
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: TRACKER });
  await page.viewport(viewport[0], viewport[1], { mobile: viewport[2], dpr: viewport[3] });
  return page;
}
async function loadClips(page, names) {
  for (const n of names) {
    const j = JSON.parse(fs.readFileSync(path.join(CLIPS, n + '.json'), 'utf8'));
    await page.eval(`window.__clips[${JSON.stringify(n)}] = ${JSON.stringify(j.frames)}`);
  }
}
const playAndWait = async (page, clip, timeout = 30000) => {
  await page.eval(`window.__play(${JSON.stringify(clip)})`);
  await sleep(300);
  await page.waitFor('window.__idle()', timeout, 'clip ' + clip + ' finished');
};
const shot = (page, dir, n) => page.shot(path.join(out, dir, n + '.png'));

// ======================================================================
async function flowSend() {
  const dir = 'send';
  const page = await newPage();
  try {
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
    await shot(page, dir, '01-home');
    // focus the real editor on the laptop so xdotool has somewhere to type
    sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(300);
    const before = sink().length;
    await page.focus('#box');
    await page.type('Hello from the phone, typed for real');
    await shot(page, dir, '02-typed');
    await page.click('#go');
    await page.waitFor(`document.querySelectorAll('#feed .msg.ok').length >= 1`, 8000, 'text delivered');
    await sleep(600);
    await shot(page, dir, '03-text-delivered');
    check('text lands in the real editor', sink().slice(before).includes('Hello from the phone, typed for real'), 'sink.txt got the text');
    const meta = await page.eval(`document.querySelector('#feed .msg.ok .msg-m').textContent`);
    check('feed shows delivery + latency', /Typed into/.test(meta) && /(ms|s)$/.test(meta.trim()), meta.trim());

    // photo (a generated PNG: no people), video (tiny generated webm), file
    const dirTmp = '/tmp/peak-send'; fs.mkdirSync(dirTmp, { recursive: true });
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=0x15493f:s=640x400', '-frames:v', '1', dirTmp + '/photo.png']);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=320x200:d=1', '-c:v', 'libvpx', '-b:v', '100k', dirTmp + '/clip.webm']);
    fs.writeFileSync(dirTmp + '/notes.txt', 'peak proof file\n');
    for (const [label, input, f, nth] of [['photo', '#file', dirTmp + '/photo.png', 2], ['video', '#file', dirTmp + '/clip.webm', 3], ['file', '#fileany', dirTmp + '/notes.txt', 4]]) {
      await page.setFiles(input, [f]);
      await page.waitFor(`!document.querySelector('#attach').hidden`, 4000, label + ' staged');
      await shot(page, dir, `0${nth + 1}-${label}-staged`);
      sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(200);
      await page.click('#go');
      await page.waitFor(`document.querySelectorAll('#feed .msg.ok').length >= ${nth}`, 15000, label + ' delivered');
      await sleep(500);
      await shot(page, dir, `0${nth + 1}-${label}-delivered`);
      const m = await page.eval(`[...document.querySelectorAll('#feed .msg.ok .msg-m')].pop().textContent`);
      check(label + ' delivered', true, m.trim());
    }
    const up = fs.readdirSync(S.WFHOME + '/wf-uploads').filter(n => /clip|notes|photo/i.test(n));
    check('files saved in ~/wf-uploads on the laptop', up.length >= 1, up.slice(0, 3).join(', '));
    await shot(page, dir, '09-feed');
    check('no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  } finally { await page.close(); }
}

// ======================================================================
async function flowTeach() {
  const dir = 'teach';
  await api('DELETE', '/bank?gloss=TYPE-HELLO');
  const page = await newPage();
  try {
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1200 });
    await loadClips(page, ['teach-TYPE-HELLO', 'palm-hold', 'perform-TYPE-HELLO', 'idle']);
    await page.click('#tray-teach'); await sleep(600);
    await shot(page, dir, '01-name');
    await page.focus('#tw-name'); await page.type('type hello'); await sleep(300);
    await shot(page, dir, '02-named');
    await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 4000, 'name accepted');
    await page.click('#tw-name-next'); await sleep(900);
    await shot(page, dir, '03-record-armed');
    await page.eval(`window.__play('teach-TYPE-HELLO')`);
    for (let n = 1; n <= 3; n++) {
      await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length >= ${n}`, 25000, 'take ' + n);
      await shot(page, dir, `04-take-${n}`);
    }
    check('3 takes stored by the daemon\'s own endpointer', true);
    await page.waitFor(`document.querySelector('.tw-step[data-step="bind"]').classList.contains('on')`, 8000, 'bind step');
    await shot(page, dir, '05-bind');
    // the @ picker, keyboard only
    await page.click('#tw-pick');
    await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 4000, 'picker open');
    await sleep(1200);
    await shot(page, dir, '06-picker');
    await page.focus('#act-q'); await page.type('type text'); await sleep(500);
    await shot(page, dir, '07-picker-search');
    await page.tab(); await sleep(200);                       // Tab completes into the row's field
    await page.type('hello-from-peak'); await sleep(250);
    await shot(page, dir, '08-picker-arg');
    await page.enter();
    await page.waitFor(`document.querySelector('#sheet-actions').hidden`, 4000, 'picker closed');
    await page.waitFor(`!document.querySelector('#tw-save').disabled`, 4000, 'Finish enabled');
    await shot(page, dir, '09-ready-to-finish');
    await page.click('#tw-save');
    await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 6000, 'done step');
    await sleep(700);
    await shot(page, dir, '10-finished');
    const bank = await api('GET', '/bank');
    const row = ((bank.json || {}).signs || []).find(s => s.gloss === 'TYPE-HELLO');
    check('sign is in the daemon bank with 3 exemplars and an action', row && (row.n_exemplars || row.exemplar_count || 0) >= 3 && !!(row.action || row.argv), JSON.stringify(row || {}).slice(0, 160));
    check('no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  } finally { await page.close(); }
}

// ======================================================================
async function flowLive() {
  const dir = 'live';
  const page = await newPage();
  try {
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1200 });
    await loadClips(page, ['palm-hold', 'perform-TYPE-HELLO', 'namaste', 'idle', 'reject']);
    await page.click('#tray-live');
    await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera + model ready');
    await sleep(800);
    await shot(page, dir, '01-live-ready');
    check('camera stream is live on a real device path', await page.eval(`(() => { const v = document.querySelector('#lv-cam'); return !!(v.srcObject && v.videoWidth > 0); })()`), 'video element has a stream');
    sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(300);
    // start from a stopped state (the daemon keeps listening across page loads)
    if (await page.eval(`document.querySelector('#lv-badge').classList.contains('listen')`)) {
      await playAndWait(page, 'namaste');
      await page.waitFor(`!document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'stopped by palms together');
      check('palms together -> stop listening', true);
      await sleep(800);
    }
    const before = sink().length;
    await playAndWait(page, 'palm-hold');
    await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
    await sleep(900);
    await shot(page, dir, '02-listening');
    check('open palm -> listening', true);
    const m0 = (((await api('GET', '/live/state')).json || {}).last_match || {}).at_ms;
    await playAndWait(page, 'perform-TYPE-HELLO');
    for (let i = 0; i < 60; i++) {
      const m = (((await api('GET', '/live/state')).json || {}).last_match || {});
      if (m.at_ms !== m0 && m.gloss === 'TYPE-HELLO' && m.accepted) { check('daemon matched a NEW performance', true, `d=${(+m.dist).toFixed(3)} accepted`); break; }
      if (i === 59) check('daemon matched a NEW performance', false, JSON.stringify(m).slice(0, 120));
      await sleep(250);
    }
    await page.waitFor(`/type hello/i.test(document.querySelector('#lv-guess').textContent)`, 12000, 'sign read');
    await sleep(500);
    await shot(page, dir, '03-sign-read-and-ran');
    const guess = await page.eval(`document.querySelector('#lv-guess').textContent`);
    check('the taught sign is read', /type hello/i.test(guess), guess);
    await sleep(800);
    check('the real OS action ran (typed into the laptop editor)', sink().slice(before).includes('hello-from-peak'), 'sink.txt got hello-from-peak');
    await playAndWait(page, 'reject');
    await sleep(900);
    await shot(page, dir, '04-unknown-rejected');
    const after = sink().slice(before);
    check('an untaught motion does not fire anything', (after.match(/hello-from-peak/g) || []).length === 1, 'exactly one typed');
    await page.click('#tray-compose'); await sleep(1800);
    await shot(page, dir, '05-feed-shows-the-sign');
    check('the sign shows up in the home feed', await page.eval(`!![...document.querySelectorAll('#feed .msg.sys')].length`), 'sys message present');
    check('no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  } finally { await page.close(); }
}

try {
  for (const f of flows) {
    console.log('=== flow:', f);
    if (f === 'send') await flowSend();
    if (f === 'teach') await flowTeach();
    if (f === 'live') await flowLive();
  }
} catch (e) {
  check('flow crashed', false, String(e.message || e).slice(0, 300));
}
const body = results.map(r => `${r.ok ? 'PASS' : 'FAIL'}\t${r.name}\t${scrub(r.detail, S)}`).join('\n');
fs.writeFileSync(path.join(out, 'RESULT.txt'), `# ${new Date().toISOString()}  flows=${flows.join(',')}\n${body}\n`);
process.exitCode = results.every(r => r.ok) ? 0 : 1;
