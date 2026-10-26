// base-shots.mjs — look at the CURRENT Teach / @ picker / home on a real daemon (390x844@2x).
// usage: node --experimental-websocket tools/site/base-shots.mjs <outdir> [cdp-port]
import path from 'node:path';
import { stack, newPage, sleep, viaDrawer, shotTo, daemon } from './lib.mjs';

const out = process.argv[2] || '/tmp/site-base';
const S = stack();
const page = await newPage(S, { cdpPort: process.argv[3] });
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await shotTo(page, out, '01-home');
  await viaDrawer(page, '#tray-teach');
  await shotTo(page, out, '02-teach-name');
  await page.focus('#tw-name'); await page.type('open chrome'); await sleep(300);
  await shotTo(page, out, '03-teach-named');
  // jump straight to the picker through the real module
  await page.eval(`import('/js/actions.js').then(m => { m.Actions.pick({ forGloss: 'OPEN-CHROME' }); })`);
  await sleep(1800);
  await shotTo(page, out, '04-picker');
  console.log('errors:', page.errors.slice(0, 3));
} finally { await page.close(); }
