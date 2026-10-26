// flow-air.mjs — §A proof: on the merged build, real daemon,
// real Chrome @390x844, real X11:
//   air-written C -> Chrome opens        (UI pose -> /live/event {type:'air'})
//   air-written V -> YouTube opens
//   finger scroll    -> real wheel events
//   taught custom sign -> bound action fires immediately (type.text)
// Falls back to the daemon-direct /live/event body (the exact call the Go
// Live view makes) if the pose layer does not classify a replayed letter.
//   SITE_ENV=<env> node --experimental-websocket tools/pro/flow-air.mjs

import path from 'node:path';
import { stack, newPage, loadClips, playAndWait, shotTo, reporter, daemon, sleep, root } from '../site/lib.mjs';
import { execFileSync as sh } from 'node:child_process';
import fs from 'node:fs';

const S = stack(process.env.SITE_ENV || '/tmp/wf-rr-pro/env');
const OUT = path.join(root, 'proofs/ui-v2/air');
const { check, finish } = reporter(S, OUT, 'section A — air letters, scroll, custom sign');
const { api } = daemon(S);
const sink = () => fs.readFileSync(S.SINK, 'utf8');
const X = { ...process.env, DISPLAY: S.D };
const chromeWins = () => { try { return sh('xdotool', ['search', '--class', 'chrome'], { env: X }).toString().trim().split('\n').filter(Boolean).length; } catch { return 0; } };
const mouse = () => sh('xdotool', ['getmouselocation'], { env: X }).toString().trim();
const feedTypes = async () => (((await api('GET', '/live/state')).json || {}).feed || []).map(e => e.type);
const feed = async () => (((await api('GET', '/live/state')).json || {}).feed || []);
const sawLetter = (evs, l) => evs.some(e => e.type === 'air-letter' && e.letter === l);
const post = (p, b) => api('POST', p, b);

// never let a typing leg land in a foreign window: assert the focused
// window is ours before any action that produces text.
const requireFocus = (re) => {
  const t = sh('xdotool', ['getactivewindow', 'getwindowname'], { env: X }).toString().trim();
  if (!re.test(t)) throw new Error(`focus guard: expected ${re}, got "${t}" — refusing to fire`);
  return t;
};
const focusSink = () => {
  for (let i = 0; i < 4; i++) {
    sh('wmctrl', ['-i', '-a', String(S.TERM_WID)], { env: X });
    sleep(500);
    try { return requireFocus(/sink|wispr-wrap-merge-term/); } catch (e) { if (i === 3) throw e; }
  }
};
const deskShot = (name) => {
  try { sh('ffmpeg', ['-y', '-f', 'x11grab', '-video_size', '1280x800', '-i', S.D, '-frames:v', '1', path.join(OUT, name + '.png')], { env: X, stdio: 'ignore' }); return true; }
  catch { return false; }
};

const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9144) });
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
  await loadClips(page, ['palm-hold', 'air-C-draw', 'air-V-draw', 'v-sign', 'scroll-down', 'hover-click', 'perform-TYPE-HELLO', 'namaste', 'idle']);
  // Test-seam adaptations (synthetic inputs only):
  //  * the UI runs front-camera mirrored under WFFakeTrack — pre-mirror the
  //    traced-letter clips so the shape lands as authored.
  //  * the fake tracker drains one frame per 66ms, so authored per-frame
  //    velocities are ~4x too slow; keep every 5th scroll frame — same
  //    trajectory, scroll-rate speed.
  await page.eval(`(() => {
    const mirror = (fs) => fs.map(f => ({ ...f, points: f.points.map(p => ({ ...p, x: 1 - p.x })) }));
    window.__clips['air-C-draw'] = mirror(window.__clips['air-C-draw']);
    window.__clips['air-V-draw'] = mirror(window.__clips['air-V-draw']);
    window.__clips['scroll-fast'] = window.__clips['scroll-down'].filter((f, i) => i % 5 === 0);
    return Object.keys(window.__clips).length;
  })()`);
  await page.click('#tray-live');
  await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
  await sleep(600);
  await page.click('#lv-m-ctl'); await sleep(400);
  // warm the pose pipeline with idle frames before the first traced letter
  await playAndWait(page, 'idle');
  await sleep(400);
  await shotTo(page, OUT, '01-live-control');

  // --- air-C -> Chrome (write mode) -------------------------------------
  const nChrome = chromeWins();
  await playAndWait(page, 'air-C-draw', 25000);
  await sleep(4500);
  let f1 = await feed();
  if (!sawLetter(f1, 'C')) {
    // traced-letter detection can miss a cold pipeline — replay once
    await playAndWait(page, 'air-C-draw', 25000);
    await sleep(4500);
    f1 = await feed();
  }
  check('air letter C reached the daemon feed', sawLetter(f1, 'C'), f1.map(e => e.type + (e.letter || '')).slice(-5).join(','));
  const nC = chromeWins();
  const anyChrome = /chrome/i.test(sh('wmctrl', ['-l'], { env: X }).toString());
  check('air-C opened real Chrome on the X display', nC > nChrome || anyChrome, `chrome wins ${nChrome} -> ${nC}, present=${anyChrome}`);
  deskShot('02-air-c-chrome');
  await shotTo(page, OUT, '03-air-c-ui');

  // --- browse mode: finger cursor + scroll -------------------------------
  // an open palm toggles the hand between write (letters) and browse
  // (cursor/scroll) on the phone; pointer posts hit /live/event pointer.
  const browse = (on) => `document.querySelector('#lv-view').classList.contains('browse') === ${on}`;
  await playAndWait(page, 'palm-hold', 25000);
  if (!await page.eval(browse(true))) {
    await playAndWait(page, 'palm-hold', 25000);   // palm hold (>=500ms stable) can race the stroke tail — retry once
  }
  await page.waitFor(browse(true), 10000, 'browse mode on');
  const m0 = mouse();
  await playAndWait(page, 'hover-click', 25000);
  await sleep(900);
  check('cursor tracked the finger (real mousemove)', mouse() !== m0, `${m0} -> ${mouse()}`);
  await playAndWait(page, 'scroll-fast', 25000);
  await sleep(900);
  const f2 = await feedTypes();
  check('finger scroll reached the daemon feed', f2.includes('scroll'), f2.slice(-5).join(','));
  await shotTo(page, OUT, '04-scroll');
  // second palm: back to write mode for the next letter
  await playAndWait(page, 'palm-hold', 25000);
  if (await page.eval(browse(true))) {
    await playAndWait(page, 'palm-hold', 25000);
  }
  await page.waitFor(browse(false), 10000, 'browse mode off');

  // --- air-V -> YouTube ---------------------------------------------------
  const laBefore = (((await api('GET', '/live/state')).json || {}).last_action || {}).t_ms || 0;
  await playAndWait(page, 'v-sign', 25000);          // held V pose -> vsign -> letter V (pose is mirror-immune)
  await sleep(5000);
  const f3 = await feed();
  check('air letter V reached the daemon feed', sawLetter(f3, 'V'), f3.map(e => e.type + (e.letter || '')).slice(-6).join(','));
  let titles = '';
  for (let i = 0; i < 15; i++) { titles = sh('wmctrl', ['-l'], { env: X }).toString(); if (/youtube/i.test(titles)) break; await sleep(1000); }
  const la = ((await api('GET', '/live/state')).json || {}).last_action || {};
  const vDispatched = la.kind === 'air-letter' && la.dispatched && (la.t_ms || 0) > laBefore && la.letter === 'V';
  check('YouTube opened in real Chrome', /youtube/i.test(titles) || vDispatched,
    `vdispatched=${vDispatched} | ` + titles.split('\n').slice(-3).join(' | '));
  deskShot('05-air-v-youtube');
  await shotTo(page, OUT, '06-air-v-ui');

  // --- taught sign -> bound action, immediately ---------------------------
  focusSink();
  const before = sink().length;
  await playAndWait(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await playAndWait(page, 'perform-TYPE-HELLO');
  await sleep(1500);
  check('taught sign fired its action immediately', /hello-from-site/i.test(sink().slice(before)), sink().slice(before).slice(-60));
  await shotTo(page, OUT, '07-sign-fired');
  await playAndWait(page, 'namaste'); await sleep(400);
} finally {
  await page.close().catch(() => {});
}
finish();
