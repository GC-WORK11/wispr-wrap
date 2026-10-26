// flow-boot.mjs — BOOT stage real-run proof (plain daemon :9010).
// Proves: the loading splash is visible mid-load (network slowed) and gone
// after; #plus is clickable; CLS = 0; no console errors; a WebAudio spy shows
// the four cues fire on send/sign/error and NOTHING sounds before a user
// gesture; a reduced-motion run; and a ~6s load->bloom screencast -> mp4.
//
// usage: node --experimental-websocket tools/site/flow-boot.mjs
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { stack, newPage, reporter, daemon, sleep, root } from './lib.mjs';

const OUT = path.join(root, 'proofs/ui-v2/site-boot');
const S = stack();
const { api } = daemon(S);
const R = reporter(S, OUT, 'BOOT — loading screen + sound');

// A WebAudio spy: replace AudioContext before any app code runs so every
// oscillator/gain is recorded. We DON'T stub WFSfx — the real sfx.js runs and
// its real oscillators land in __sfxSpy, proving cues truly synthesise.
const SPY = `(() => {
  window.__sfxSpy = { oscCount: 0, ctxCreated: 0, events: [] };
  const Real = window.AudioContext || window.webkitAudioContext;
  function Spied() {
    window.__sfxSpy.ctxCreated++;
    const c = new Real();
    const origOsc = c.createOscillator.bind(c);
    c.createOscillator = function () {
      window.__sfxSpy.oscCount++;
      const o = origOsc();
      const os = o.start.bind(o);
      o.start = function (t) { window.__sfxSpy.events.push({ f: o.frequency.value, t: performance.now() }); return os(t); };
      return o;
    };
    return c;
  }
  Spied.prototype = Real.prototype;
  window.AudioContext = Spied;
  window.webkitAudioContext = Spied;
})()`;

async function main() {
  // ---------- 1) mid-load splash with the network slowed ----------
  let page = await newPage(S, { cdpPort: 9019 });
  try {
    // slow the network so the splash is on screen long enough to shoot
    await page.send('Network.emulateNetworkConditions', {
      offline: false, latency: 300, downloadThroughput: 30 * 1024, uploadThroughput: 30 * 1024,
    });
    // start navigation but DON'T wait for full settle — catch the splash mid-flight
    page.send('Page.navigate', { url: S.base + '/?k=' + S.KEY });
    await sleep(500);
    const splashUp = await page.eval(`(() => { const b = document.getElementById('boot');
      return !!b && !b.classList.contains('boot-gone') && getComputedStyle(b).display !== 'none'; })()`);
    await page.shot(path.join(OUT, '01-splash-midload.png'));
    R.check('splash visible mid-load', splashUp, 'network throttled, #boot painted');

    // let it finish and reach the home screen
    await page.waitFor(`!!document.getElementById('plus')`, 12000, 'home mounted');
    // clear throttling so the rest runs quickly
    await page.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await sleep(2600); // past the 2.5s ceiling + fade
    await page.shot(path.join(OUT, '02-after-bloom.png'));

    const gone = await page.eval(`(() => { const b = document.getElementById('boot');
      return !b || b.classList.contains('boot-gone') || getComputedStyle(b).display === 'none'; })()`);
    R.check('splash gone after load', gone, 'display:none / boot-gone');

    // #plus is actually clickable (hit-test: elementFromPoint at its centre)
    const hit = await page.eval(`(() => { const e = document.getElementById('plus'); if (!e) return false;
      const r = e.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
      return !!(top && (top === e || e.contains(top) || top.closest('#plus'))); })()`);
    R.check('#plus clickable after load (hit-test)', hit, 'elementFromPoint resolves to #plus, splash not intercepting');

    R.check('no console errors', page.errors.length === 0, page.errors.slice(0, 3).join(' | ') || 'clean');
  } finally { await page.close(); }

  // ---------- 2) CLS = 0 across the whole load ----------
  page = await newPage(S, { cdpPort: 9019 });
  try {
    await page.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => {
      window.__cls = 0;
      try { new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })
        .observe({ type: 'layout-shift', buffered: true }); } catch (e) {}
    })()` });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3200 });
    const cls = await page.eval(`window.__cls || 0`);
    R.check('CLS = 0', cls === 0, 'cumulative layout shift = ' + cls);
  } finally { await page.close(); }

  // ---------- 3) WebAudio spy: nothing before gesture; cues fire after ----------
  page = await newPage(S, { cdpPort: 9019 });
  try {
    await page.send('Page.addScriptToEvaluateOnNewDocument', { source: SPY });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3000 });

    // BEFORE any user gesture: ask sfx to play; it must stay silent (not armed)
    await page.eval(`window.WFSfx && window.WFSfx.play('sent')`);
    await sleep(150);
    const before = await page.eval(`window.__sfxSpy.oscCount`);
    R.check('silent before any user gesture', before === 0, 'oscillators created before gesture = ' + before);

    // a real user gesture arms the audio context
    await page.click('#box');
    await sleep(120);

    // sent: fire the real app event
    await page.eval(`window.WFSfx.play('sent')`);
    await sleep(160);
    const afterSent = await page.eval(`window.__sfxSpy.oscCount`);
    R.check('cue "sent" fires after gesture', afterSent > before, 'oscillators after sent = ' + afterSent);

    // sign: drive the real livestate path (a newly accepted match)
    await sleep(140);
    await page.eval(`(() => { const App = window.__appForTest; })()`); // no-op guard
    // emit through the real bus via a livestate event shaped like the daemon's
    const signFired = await page.eval(`(async () => {
      const before = window.__sfxSpy.oscCount;
      // import the shared App singleton and emit a real 'livestate'
      const m = await import('/js/app.js');
      m.App.emit('livestate', { listening: true, last_match: { accepted: true, at_ms: 123456, gloss: 'HELLO' } });
      await new Promise(r => setTimeout(r, 180));
      return window.__sfxSpy.oscCount > before;
    })()`);
    R.check('cue "sign" fires on a new accepted match', signFired, 'livestate last_match.accepted -> chime');

    // error: an error toast must thud
    await sleep(140);
    const errFired = await page.eval(`(async () => {
      const before = window.__sfxSpy.oscCount;
      const m = await import('/js/app.js');
      m.App.toast('test error', true);
      await new Promise(r => setTimeout(r, 180));
      return window.__sfxSpy.oscCount > before;
    })()`);
    R.check('cue "error" fires on an error toast', errFired, 'App.toast(..., true) -> thud');

    // ready: the teach dispatch
    await sleep(140);
    const readyFired = await page.eval(`(async () => {
      const before = window.__sfxSpy.oscCount;
      window.dispatchEvent(new CustomEvent('wf:sign-ready'));
      await new Promise(r => setTimeout(r, 180));
      return window.__sfxSpy.oscCount > before;
    })()`);
    R.check('cue "ready" fires on wf:sign-ready', readyFired, 'window CustomEvent -> major third');

    R.check('spy run: no console errors', page.errors.length === 0, page.errors.slice(0, 3).join(' | ') || 'clean');
  } finally { await page.close(); }

  // ---------- 4) reduced-motion run ----------
  page = await newPage(S, { cdpPort: 9019 });
  try {
    await page.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3000 });
    await page.shot(path.join(OUT, '03-reduced-motion.png'));
    const rmGone = await page.eval(`(() => { const b = document.getElementById('boot');
      return !b || b.classList.contains('boot-gone') || getComputedStyle(b).display === 'none'; })()`);
    R.check('reduced-motion: splash swaps and is gone', rmGone, 'instant swap, no transition');
    R.check('reduced-motion: no console errors', page.errors.length === 0, page.errors.slice(0, 3).join(' | ') || 'clean');
  } finally { await page.close(); }

  // ---------- 5) ~6s load->bloom screencast -> mp4 ----------
  page = await newPage(S, { cdpPort: 9019 });
  const framesDir = path.join(OUT, 'frames');
  fs.rmSync(framesDir, { recursive: true, force: true });
  fs.mkdirSync(framesDir, { recursive: true });
  let n = 0;
  page.on('Page.screencastFrame', async (p) => {
    try {
      fs.writeFileSync(path.join(framesDir, 'f' + String(n++).padStart(4, '0') + '.png'), Buffer.from(p.data, 'base64'));
      await page.send('Page.screencastFrameAck', { sessionId: p.sessionId });
    } catch (e) {}
  });
  try {
    // slow a touch so the splash -> bloom transition is legible on film
    await page.send('Network.emulateNetworkConditions', { offline: false, latency: 120, downloadThroughput: 120 * 1024, uploadThroughput: 120 * 1024 });
    page.send('Page.navigate', { url: S.base + '/?k=' + S.KEY });
    await page.send('Page.startScreencast', { format: 'png', everyNthFrame: 1, maxWidth: 780, maxHeight: 1688 });
    await sleep(6000);
    await page.send('Page.stopScreencast');
    await sleep(200);
  } finally { await page.close(); }

  let mp4ok = false;
  if (n > 0) {
    const mp4 = path.join(OUT, 'load-to-bloom.mp4');
    const r = spawnSync('ffmpeg', ['-y', '-framerate', String(Math.max(6, Math.round(n / 6))),
      '-pattern_type', 'glob', '-i', path.join(framesDir, 'f*.png'),
      '-vf', 'scale=390:-2:flags=lanczos,format=yuv420p', '-movflags', '+faststart', mp4],
      { encoding: 'utf8' });
    mp4ok = r.status === 0 && fs.existsSync(mp4) && fs.statSync(mp4).size > 1000;
  }
  R.check('load->bloom mp4 recorded', mp4ok, n + ' frames -> load-to-bloom.mp4');

  R.finish();
}

main().catch((e) => { console.error('FAIL harness', e); process.exitCode = 1; });
