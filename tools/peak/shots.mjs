// shots.mjs — fast visual loop: home (hero), typed, sent (feed), mobile home.
// usage: node --experimental-websocket tools/peak/shots.mjs <outdir> [cdp-port] [which]
import path from 'node:path';
import fs from 'node:fs';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || '/tmp/peak-shots/v1';
const cdpPort = Number(process.argv[3] || 8951);
const which = (process.argv[4] || 'home').split(',');
const S = loadStack();
const profile = '/tmp/peak-chrome-shots';
fs.rmSync(profile, { recursive: true, force: true });   // fresh profile: no service-worker cache

const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });   // never touch a sibling's replay seam
const shot = (n) => page.shot(path.join(out, n + '.png'));
try {
  if (which.includes('home')) {
    await page.viewport(1280, 800);
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1600 });
    await shot('d-home');
    await page.focus('#box');
    await page.type('Remind me to push the build before 6');
    await sleep(250);
    await shot('d-home-typed');
    await page.click('#go');
    await sleep(1200);
    await shot('d-home-sent');
    await page.viewport(390, 844, { mobile: true, dpr: 2 });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1600 });
    await shot('m-home');
  }
  if (which.includes('sheets')) {
    for (const [tag, w, h, mobile, dpr] of [['d', 1280, 800, false, 1], ['m', 390, 844, true, 2]]) {
      await page.viewport(w, h, { mobile, dpr });
      await page.goto(S.base + '/?k=' + S.KEY, { settle: 900 });
      await page.click('#tray-live'); await sleep(2600);
      await shot(`${tag}-live`);
      await page.goto(S.base + '/', { settle: 700 });
      await page.click('#tray-teach'); await sleep(900);
      await shot(`${tag}-teach-name`);
      await page.focus('#tw-name'); await page.type('open browser'); await sleep(300);
      await shot(`${tag}-teach-typed`);
      await page.goto(S.base + '/', { settle: 700 });
      await page.click('#tray-signs'); await sleep(900);
      await shot(`${tag}-signs`);
      await page.goto(S.base + '/', { settle: 700 });
      await page.eval(`import('/js/actions.js').then(m => { window.__p = m.Actions.pick({ forGloss: 'OPEN-BROWSER' }); })`);
      await sleep(1400);
      await shot(`${tag}-picker`);
      await page.eval(`(() => { const q = document.querySelector('#act-q'); if (q) { q.value = 'term'; q.dispatchEvent(new Event('input', {bubbles:true})); } })()`);
      await sleep(700);
      await shot(`${tag}-picker-term`);
    }
  }
  if (which.includes('unpaired')) {
    // a remote device: pretend /pair-info is refused (non-loopback) so the code screen shows
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', S.base + '/pair-info'] });
    await page.viewport(1280, 800);
    await page.eval(`localStorage.clear()`).catch(() => {});
    await page.goto(S.base + '/', { settle: 1400 });
    await page.eval(`localStorage.clear()`);
    await page.goto(S.base + '/', { settle: 1400 });
    await shot('d-onboard');
    await page.viewport(390, 844, { mobile: true, dpr: 2 });
    await page.goto(S.base + '/', { settle: 1400 });
    await shot('m-onboard');
  }
  console.log('errors:', JSON.stringify(page.errors.slice(0, 6)));
  console.log('console:', page.consoleLog.filter(l => !/ExperimentalWarning/.test(l)).slice(0, 8).join('\n'));
} finally {
  await page.close();
}
