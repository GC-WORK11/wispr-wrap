// uiv2.mjs — 390x844 proof shots of the founder-spec UI (original wispr-wrap
// lotus home, composer, drawer, Go Live Text/Control, Teach), in a real
// headless Chrome against the real daemon. Real camera device (Chrome fake
// device), real MediaPipe; hand points from the app's own test seam.
// usage: node --experimental-websocket tools/peak/uiv2.mjs <outdir> <set[,set]> [cdp-port]
//   sets: flower composer drawer unpaired live teach wide
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || 'proofs/ui-v2';
const sets = (process.argv[3] || 'flower,composer,drawer').split(',');
const cdpPort = Number(process.argv[4] || 8951);
const S = loadStack();
fs.mkdirSync(out, { recursive: true });

const TRACKER = fs.readFileSync(new URL('./flows.mjs', import.meta.url), 'utf8').match(/const TRACKER = `([\s\S]*?)`;/)[1];
const profile = '/tmp/peak-chrome-uiv2';
fs.rmSync(profile, { recursive: true, force: true });
const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
await page.send('Page.addScriptToEvaluateOnNewDocument', { source: TRACKER });
const shot = n => page.shot(path.join(out, n + '.png'));
const phone = () => page.viewport(390, 844, { mobile: true, dpr: 2 });
try {
  if (sets.includes('flower')) {
    await phone();
    // frames of the lotus opening: load, then sample
    const t0 = Date.now();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 150 });
    for (const [label, at] of [['0.3s', 300], ['0.6s', 600], ['1s', 1000], ['2s', 2000], ['4s', 4000]]) {
      const wait = at - (Date.now() - t0); if (wait > 0) await sleep(wait);
      await shot(`01-flower-opening-${label}`);
    }
  }
  if (sets.includes('composer')) {
    await phone();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3200 });
    await shot('02-composer-empty');
    await page.focus('#box');
    await page.type('Hey, can you send me the build when it is ready?');
    await sleep(600);
    await shot('03-composer-with-text');
  }
  if (sets.includes('drawer')) {
    await phone();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1800 });
    await page.click('#menu'); await sleep(700);
    await shot('05-sidebar-open');
  }
  if (sets.includes('live')) {
    await phone();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1200 });
    const clips = {};
    for (const n of ['palm-hold', 'perform-TYPE-HELLO', 'namaste', 'reject']) {
      clips[n] = JSON.parse(fs.readFileSync(new URL('../demo/clips/' + n + '.json', import.meta.url), 'utf8')).frames;
      await page.eval(`window.__clips[${JSON.stringify(n)}] = ${JSON.stringify(clips[n])}`);
    }
    const play = async n => { await page.eval(`window.__play(${JSON.stringify(n)})`); await sleep(300); await page.waitFor('window.__idle()', 30000, n); };
    await page.click('#tray-live');
    await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
    await sleep(900);
    await shot('07-golive-control-idle');
    await play('palm-hold'); await sleep(900);
    await shot('08-golive-control-listening');
    await play('perform-TYPE-HELLO'); await sleep(1200);
    await shot('09-golive-control-sign-ran');
    await play('namaste'); await sleep(600);
    // Text mode
    await page.click('#lv-m-text'); await sleep(700);
    await shot('10-golive-text-idle');
    await play('palm-hold'); await sleep(700);
    await play('perform-TYPE-HELLO'); await sleep(1500);
    await shot('11-golive-text-word');
    await page.click('#lv-m-ctl'); await sleep(500);
    await page.click('#lv-x'); await sleep(900);
    await shot('12-back-home-after-live');
  }
  if (sets.includes('layouts')) {
    const L = (n, w, h, mobile, dpr) => async () => {
      await page.viewport(w, h, { mobile, dpr });
      await page.goto(S.base + '/?k=' + S.KEY, { settle: 3200 });
      await shot('layouts/' + n);
    };
    await L('tablet-820x1180', 820, 1180, true, 1)();
    await L('laptop-1440x900', 1440, 900, false, 1)();
    await L('small-phone-360x640', 360, 640, true, 2)();
    await L('keyboard-open-390x520', 390, 520, true, 2)();   // visual height with the soft keyboard up
    await page.viewport(1440, 900, { mobile: false, dpr: 1 });
    await page.click('#menu'); await sleep(700);
    await shot('layouts/laptop-sidebar-open');
  }
  if (sets.includes('burst')) {
    await phone();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3500 });
    await page.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    try { execFileSync('xdotool', ['windowfocus', S.TERM_WID], { env: { ...process.env, DISPLAY: S.D } }); } catch (e) { /* editor focus is best-effort here */ }
    await page.focus('#box');
    await page.type('Ship it');
    await sleep(500);
    await page.click('#go');
    for (const [n, ms] of [['0.25s', 250], ['0.7s', 450], ['1.3s', 600]]) { await sleep(ms); await shot('16-send-burst-' + n); }
  }
  if (sets.includes('pages')) {
    await phone();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
    await page.click('#menu'); await sleep(600); await page.click('#dr-laptop'); await sleep(900);
    await shot('17-connected-laptop');
    await page.click('[data-close]').catch(() => {}); await sleep(500);
    await page.eval(`location.hash = '#/settings'`); await sleep(900);
    await shot('18-settings');
    await page.eval(`location.hash = '#/signs'`); await sleep(1200);
    await shot('19-my-signs');
    console.log('signs-page errors:', JSON.stringify(page.errors.slice(0, 4)));
  }
  if (sets.includes('offline')) {
    // the laptop is unreachable: block /health so the app sees it as offline
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', S.base + '/health'] });
    await phone();
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 4500 });
    await shot('13-laptop-offline');
    await page.click('#menu'); await sleep(700);
    await shot('14-sidebar-laptop-not-connected');
  }
  if (sets.includes('unpaired')) {
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', S.base + '/pair-info'] });
    await phone();
    await page.goto(S.base + '/', { settle: 600 });
    await page.eval('localStorage.clear()');
    await page.goto(S.base + '/', { settle: 1500 });
    await shot('06-connect-laptop');
  }
  console.log('errors:', JSON.stringify(page.errors.slice(0, 6)));
  console.log('console:', page.consoleLog.filter(l => !/gl_context|Graph succ|landmark_proj|ExperimentalWarning/.test(l)).slice(0, 8).join('\n'));
} finally {
  await page.close();
}
