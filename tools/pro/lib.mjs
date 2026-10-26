// tools/pro/lib.mjs — shared real-run harness for the proof scripts in this
// directory (act-tiles, flow-air). Self-contained: depends only on tools/peak/
// (cdp.mjs, env.mjs) — no cross-lane imports.
//
// REAL: the page, MediaPipe, every HTTP call, the daemon's endpointer / DTW
// bank / act layer, and the X11 effects on the display named in the env.
// SUBSTITUTED (labelled in every proof): only the hand detections — kp42 clips
// play through the app's own test seam window.WFFakeTrack.
//
// Env: SITE_ENV — a key=value file with PORT, KEY, D, TERM_WID, SINK, WFHOME.
// The pairing key is read from that file and is NEVER printed or written to a
// proof artifact.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from '../peak/cdp.mjs';
import { loadStack, scrub } from '../peak/env.mjs';

export { sleep, scrub };
export const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
export const CLIPS = [path.join(root, 'tools/site/clips'), path.join(root, 'tools/demo/clips')];

export function stack(file = process.env.SITE_ENV || '/tmp/wf-rr-pro/env') {
  return loadStack(file);
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

// open a Chrome page, phone viewport by default -------------------------------------
export async function newPage(S, { cdpPort, viewport = [390, 844, true, 2], profile } = {}) {
  const port = Number(cdpPort || process.env.SITE_CDP || 9015);
  const prof = profile || `/tmp/pro-chrome-${port}`;
  fs.rmSync(prof, { recursive: true, force: true });
  const extra = process.env.SITE_FAKE_CAM ? ['--use-file-for-fake-video-capture=' + process.env.SITE_FAKE_CAM] : [];
  const page = await launchChrome({ port, profile: prof, origin: S.base, extra });
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

export const shotTo = (page, dir, name) => page.shot(path.join(dir, name + '.png'));

// results + checks ------------------------------------------------------------------
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

// daemon helpers --------------------------------------------------------------------
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
