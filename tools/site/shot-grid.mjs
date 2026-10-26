// shot-grid.mjs — the endpoint-driven Teach grid with the laptop's own icons (390x844@2x). usage: SITE_ENV=... node ... <out>
import path from 'node:path';
import { stack, newPage, sleep, shotTo, daemon } from './lib.mjs';
const out = process.argv[2] || '/tmp/site-grid';
const S = stack(process.env.SITE_ENV || '/tmp/wf-site-overlay/env');
const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9016) });
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await page.click('#menu'); await sleep(600);
  await page.click('#tray-teach'); await sleep(700);
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 10000, 'grid');
  await sleep(1200);
  const info = await page.eval(`[...document.querySelectorAll('.qa-tile')].map(b => b.dataset.id + (b.classList.contains('real') ? ':real' : ':glyph'))`);
  console.log(info.join(' '));
  await shotTo(page, out, '01-grid-real-icons');
  await page.eval(`document.querySelector('#sheet-teach .sheet-body').scrollTo(0, 99999)`); await sleep(400);
  await shotTo(page, out, '02-grid-bottom');
  console.log('errors:', page.errors.slice(0, 3));
} finally { await page.close(); }
