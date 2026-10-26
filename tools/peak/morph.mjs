// morph.mjs — frame-by-frame proof that the black Go Live waveform MORPHS into the send arrow once there is text,
// and back when the box is empty again (390x844). usage: node --experimental-websocket tools/peak/morph.mjs <outdir> [cdp-port]
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';
const out = process.argv[2] || 'proofs/ui-v2/morph';
const S = loadStack();
fs.mkdirSync(out, { recursive: true });
fs.rmSync('/tmp/peak-chrome-morph', { recursive: true, force: true });
const page = await launchChrome({ port: Number(process.argv[3] || 8951), profile: '/tmp/peak-chrome-morph', origin: S.base });
try {
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
  await page.viewport(390, 844, { mobile: true, dpr: 3 });
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 3200 });
  const r = await page.rect('#composer');
  const clip = { x: 0, y: Math.max(0, r.y - 14), width: 390, height: Math.min(844 - Math.max(0, r.y - 14), r.h + 28) };
  const state = () => page.eval(`JSON.stringify({ ready: document.getElementById('pill').classList.contains('ready'), b3: getComputedStyle(document.querySelector('.act-ico .b3')).transform, b1: getComputedStyle(document.querySelector('.act-ico .b1')).transform })`);
  await page.focus('#box');
  await page.shot(path.join(out, 'a0-empty.png'), { clip });
  await page.type('H', { delay: 0 });
  const t0 = Date.now(); let i = 1;
  for (const at of [40, 100, 170, 260, 420]) { const w = at - (Date.now() - t0); if (w > 0) await sleep(w); await page.shot(path.join(out, `a${i++}-to-arrow-${at}ms.png`), { clip }); }
  console.log('after typing:', await state());
  await page.key('Backspace', { code: 'Backspace', vk: 8 });
  const t1 = Date.now(); i = 1;
  for (const at of [40, 100, 170, 260, 420]) { const w = at - (Date.now() - t1); if (w > 0) await sleep(w); await page.shot(path.join(out, `b${i++}-to-wave-${at}ms.png`), { clip }); }
  console.log('after clearing:', await state());
  console.log('errors', JSON.stringify(page.errors.slice(0, 2)));
} finally { await page.close(); }
