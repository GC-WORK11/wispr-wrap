// baseline.mjs — screenshot every screen of the app, desktop + mobile.
// usage: node --experimental-websocket tools/peak/baseline.mjs <outdir> [cdp-port]
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || '/tmp/peak-shots/baseline';
const cdpPort = Number(process.argv[3] || 8951);
const S = loadStack();

const page = await launchChrome({ port: cdpPort, profile: '/tmp/peak-chrome-baseline', origin: S.base });
const shot = (n) => page.shot(path.join(out, n + '.png'));
try {
  // 1) first open, not paired
  await page.viewport(1280, 800);
  await page.goto(S.base + '/');
  await shot('d-00-unpaired');
  await page.goto(S.base + '/pair.html');
  await shot('d-01-pair-page');

  for (const [tag, w, h, mobile, dpr] of [['d', 1280, 800, false, 1], ['m', 390, 844, true, 2]]) {
    await page.viewport(w, h, { mobile, dpr });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1200 });
    await shot(`${tag}-10-compose`);
    // type something to see the composer states
    await page.focus('#box');
    await page.type('Hello from the phone — ship it');
    await sleep(200);
    await shot(`${tag}-11-compose-typed`);
    await page.eval(`document.querySelector('#box').value=''; document.querySelector('#box').dispatchEvent(new Event('input'))`);
    await page.click('#plus'); await sleep(300);
    await shot(`${tag}-12-plus`);
    await page.esc();
    await page.goto(S.base + '/', { settle: 800 });
    await page.click('#tray-live'); await sleep(1500);
    await shot(`${tag}-20-live`);
    await page.goto(S.base + '/', { settle: 800 });
    await page.click('#tray-teach'); await sleep(900);
    await shot(`${tag}-30-teach`);
    await page.goto(S.base + '/', { settle: 800 });
    await page.click('#tray-signs'); await sleep(900);
    await shot(`${tag}-40-signs`);
  }
  console.log('errors:', JSON.stringify(page.errors.slice(0, 8)));
  console.log('console:', page.consoleLog.slice(0, 12).join('\n'));
} finally {
  await page.close();
}
