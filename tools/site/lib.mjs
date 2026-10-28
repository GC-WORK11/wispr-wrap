// tools/site/lib.mjs — shared real-run harness for the SITE lane (Teach, @ picker,
// packs, loading screen, logo). Dependency-free (node >= 20, --experimental-websocket).
//
// REAL: the page, the camera permission + fake-device stream, MediaPipe (when Go Live /
// Teach starts the camera), every HTTP call, the daemon's endpointer / DTW bank / act
// layer, and the X11 effects (xdotool typing, app launches) on a private Xvfb display.
// SUBSTITUTED (and labelled so in every proof): only the hand detections. The Chrome
// fake camera draws a test pattern with no hands, so keypoints come from the app's own
// test seam window.WFFakeTrack, which plays synthetic kp42 clips (coordinates only —
// no video, no people). See tools/demo/make_clips.py and tools/site/make-clips.py.
//
// Env: SITE_ENV (default /tmp/wf-realrun-wispr-wrap-site/env), the realrun state file
// written by `REALRUN_DISPLAY=:210 REALRUN_PORT=9010 bash tools/realrun/setup.sh`.
// The pairing key is read from that file and is NEVER printed or written to a proof.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from '../peak/cdp.mjs';
import { loadStack, scrub } from '../peak/env.mjs';

export { sleep, scrub };
export const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
export const CLIPS = [path.join(root, 'tools/site/clips'), path.join(root, 'tools/demo/clips')];

export function stack(file = process.env.SITE_ENV || '/tmp/wf-realrun-wispr-wrap-site/env') {
  return loadStack(file);
}

// results + checks --------------------------------------------------------------
export function reporter(S, outDir, title) {
  const results = [];
  fs.mkdirSync(outDir, { recursive: true });
  const check = (name, ok, detail = '') => {
    results.push({ name, ok: !!ok, detail: scrub(detail, S) });
    console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  - ' + scrub(detail, S) : ''));
    return !!ok;
  };
  const finish = () => {
    const body = results.map(r => `${r.ok ? 'PASS' : 'FAIL'}\t${r.name}\t${r.detail}`).join('\n');
    fs.writeFileSync(path.join(outDir, 'RESULT.txt'), `# ${new Date().toISOString()}  ${title}\n${body}\n`);
    process.exitCode = results.every(r => r.ok) ? 0 : 1;
    return results;
  };
  return { check, finish, results };
}

// daemon helpers ---------------------------------------------------------------
export function daemon(S) {
  const api = async (method, p, body) => {
    const r = await fetch(S.base + p, {
      method, headers: { 'X-WF-Key': S.KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    let j = null; const t = await r.text();
    try { j = JSON.parse(t); } catch (e) {
      try { j = JSON.parse(t.replace(/([:\[,]\s*)-?(?:Infinity|NaN)(?=\s*[,}\]])/g, '$1null')); } catch (e2) { /* not json */ }
    }
    return { status: r.status, json: j };
  };
  const sh = (cmd, args) => execFileSync(cmd, args, { env: { ...process.env, DISPLAY: S.D }, encoding: 'utf8' });
  const sink = () => fs.readFileSync(S.SINK, 'utf8');
  const sinkHas = async (since, text, ms = 5000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (sink().slice(since).includes(text)) return true; await sleep(100); }
    return false;
  };
  const focusEditor = async () => { sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(300); };
  return { api, sh, sink, sinkHas, focusEditor };
}

// in-page fake tracker (the only substituted piece) --------------------------------
export const TRACKER = `(() => {
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

// open a Chrome page on MY cdp port/profile, phone viewport by default ------------
export async function newPage(S, { cdpPort, viewport = [390, 844, true, 2], profile } = {}) {
  const port = Number(cdpPort || process.env.SITE_CDP || 9015);
  const prof = profile || `/tmp/site-chrome-${port}`;
  fs.rmSync(prof, { recursive: true, force: true });
  // SITE_FAKE_CAM=<file.y4m>: feed the fake camera DEVICE a neutral dim clip instead of Chrome's
  // neon test pattern (still a fake device: no real camera, no people; labelled so in proofs)
  const extra = process.env.SITE_FAKE_CAM ? ['--use-file-for-fake-video-capture=' + process.env.SITE_FAKE_CAM] : [];
  const page = await launchChrome({ port, profile: prof, origin: S.base, extra });
  // never touch a sibling lane's kp42 replay seam
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: TRACKER });
  await page.viewport(viewport[0], viewport[1], { mobile: viewport[2], dpr: viewport[3] });
  return page;
}

export async function loadClips(page, names) {
  for (const n of names) {
    const f = CLIPS.map(d => path.join(d, n + '.json')).find(p => fs.existsSync(p));
    if (!f) throw new Error('clip not found: ' + n);
    const j = JSON.parse(fs.readFileSync(f, 'utf8'));
    await page.eval(`window.__clips[${JSON.stringify(n)}] = ${JSON.stringify(j.frames)}`);
  }
}
export const playAndWait = async (page, clip, timeout = 30000) => {
  await page.eval(`window.__play(${JSON.stringify(clip)})`);
  await sleep(300);
  await page.waitFor('window.__idle()', timeout, 'clip ' + clip + ' finished');
};

export const viaDrawer = async (page, sel) => { await page.click('#menu'); await sleep(600); await page.click(sel); await sleep(500); };
export const shotTo = (page, dir, name) => page.shot(path.join(dir, name + '.png'));

// screen recording (CDP screencast -> mp4, real-time pacing) ---------------------------
// const rec = recorder(page, dir); await rec.start(); ...; await rec.stop('flow.mp4')
import { spawnSync } from 'node:child_process';
export function recorder(page, dir) {
  const frames = []; let n = 0;
  fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
  page.on('Page.screencastFrame', async (p) => {
    try {
      const f = path.join(dir, 'f' + String(n++).padStart(5, '0') + '.jpg');
      fs.writeFileSync(f, Buffer.from(p.data, 'base64'));
      frames.push({ f, t: Date.now() });
      await page.send('Page.screencastFrameAck', { sessionId: p.sessionId });
    } catch (e) { /* page closing */ }
  });
  return {
    start: () => page.send('Page.startScreencast', { format: 'jpeg', quality: 82, everyNthFrame: 1, maxWidth: 780, maxHeight: 1688 }),
    async stop(mp4) {
      try { await page.send('Page.stopScreencast'); } catch (e) { /* gone */ }
      await sleep(250);
      if (frames.length < 2) return { ok: false, frames: frames.length };
      // concat demuxer with the real inter-frame gaps (long idle waits are capped at 1.2 s)
      const lines = [];
      frames.forEach((fr, i) => {
        const dur = i + 1 < frames.length ? Math.min(1.2, Math.max(0.03, (frames[i + 1].t - fr.t) / 1000)) : 0.8;
        lines.push(`file '${fr.f}'`, `duration ${dur.toFixed(3)}`);
      });
      lines.push(`file '${frames[frames.length - 1].f}'`);
      const list = path.join(dir, 'list.txt');
      fs.writeFileSync(list, lines.join('\n'));
      const out = path.join(path.dirname(dir), mp4);
      const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list,
        '-vf', 'scale=390:-2:flags=lanczos,fps=24,format=yuv420p', '-movflags', '+faststart', out], { encoding: 'utf8' });
      const ok = r.status === 0 && fs.existsSync(out) && fs.statSync(out).size > 2000;
      if (ok) fs.rmSync(dir, { recursive: true, force: true });
      return { ok, frames: frames.length, file: out };
    },
  };
}

// every string a person can actually SEE inside `rootSel` (visible, painted, not screen-reader-only) -------------
// FOUNDER-RULE-1652: screens carry tile names and button names, never sentences.
export async function visibleText(page, rootSel) {
  return page.eval(`(() => {
    const root = document.querySelector(${JSON.stringify(rootSel)}); if (!root) return [];
    const out = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = n.nodeValue.replace(/\\s+/g, ' ').trim(); if (!t) continue;
      const el = n.parentElement; if (!el) continue;
      let hidden = false;
      for (let e = el; e && e !== document.body; e = e.parentElement) {
        const c = getComputedStyle(e);
        if (c.display === 'none' || c.visibility === 'hidden' || +c.opacity === 0 || e.hidden) { hidden = true; break; }
        const rr = e.getBoundingClientRect();
        if ((rr.width < 3 || rr.height < 3) && c.overflow !== 'visible') { hidden = true; break; }   // sr-only: clipped to a pixel
      }
      const r = el.getBoundingClientRect();
      if (hidden || r.width < 3 || r.height < 3 || getComputedStyle(el).fontSize === '0px') continue;
      out.push(t);
    }
    return [...new Set(out)];
  })()`);
}
// a label is short (at most four words, e.g. "Move window to workspace") and has no sentence punctuation
export const isLabel = (s) => s.split(/\s+/).length <= 4 && !/[.?!,:;\u2026]/.test(s);
