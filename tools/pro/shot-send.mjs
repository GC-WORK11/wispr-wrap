// shot-send.mjs — composer send-toast proof at 390x844: type text, send,
// screenshot the plain confirmation toast (no sink name, no ms timing).
// usage: SITE_ENV=<env> OUT=<dir> node --experimental-websocket tools/pro/shot-send.mjs
import { stack, newPage, shotTo, sleep, daemon } from './lib.mjs';
import fs from 'node:fs';
import path from 'node:path';

const S = stack();
const OUT = process.env.OUT || path.join(process.cwd(), 'proofs/ui-v2/send-toast');
fs.mkdirSync(OUT, { recursive: true });
const { sh } = daemon(S);

// focus the scratch sink so the send has a real target
try { sh('xdotool', ['windowfocus', String(S.TERM_WID)]); } catch (e) {}
const page = await newPage(S);
await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
await page.waitFor(`document.querySelector('#box')`, 10000, 'composer');
await page.focus('#box');
await page.type('toast check'); await sleep(300);
await page.click('#go');
await page.waitFor(`document.querySelector('.toast.show')`, 10000, 'toast shown');
await sleep(400);
const toast = await page.eval(`document.querySelector('.toast.show').textContent`);
console.log('toast=' + JSON.stringify(toast));
await shotTo(page, OUT, 'send-toast-m390');
fs.writeFileSync(path.join(OUT, 'RESULT.txt'),
  `# ${new Date().toISOString()} send toast proof (390x844)\n` +
  (toast.trim() === 'Text sent.'
    ? 'PASS\ttoast reads "Text sent." — no sink name, no timing\n'
    : `FAIL\ttoast=${JSON.stringify(toast)}\n`));
process.exit(toast.trim() === 'Text sent.' ? 0 : 1);
