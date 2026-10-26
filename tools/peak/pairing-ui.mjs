// pairing-ui.mjs — the pairing layout needs no explanation: 390x844 proof + end-to-end checks.
// A phone-like session (no loopback auto-pair): connect screen -> wrong code -> the Laptop page's own
// code field -> wrong code -> right code -> connected Laptop page -> Settings. Real wfd.py, real Chrome.
// usage: node --experimental-websocket tools/peak/pairing-ui.mjs <outdir> [cdp-port]
// The pairing code is typed but never printed or screenshotted.
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || 'proofs/ui-v2/pairing';
const S = loadStack();
fs.mkdirSync(out, { recursive: true });
fs.rmSync('/tmp/peak-chrome-pui', { recursive: true, force: true });
const page = await launchChrome({ port: Number(process.argv[3] || 8951), profile: '/tmp/peak-chrome-pui', origin: S.base });
let fails = 0;
const check = (name, ok, extra = '') => { if (!ok) fails++; console.log((ok ? 'PASS ' : 'FAIL ') + name + (ok || !extra ? '' : '  -> ' + extra)); };
const shot = (n) => page.shot(path.join(out, n + '.png'));
const txt = sel => page.eval(`(document.querySelector(${JSON.stringify(sel)}) || {}).textContent || ''`);
const vis = sel => page.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return false; const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return !e.hidden && cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0; })()`);
try {
  // a phone cannot reach the loopback-only /pair-info, so it never auto-pairs
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', '*/pair-info*'] });
  await page.viewport(390, 844, { mobile: true, dpr: 2 });
  await page.goto(S.base + '/', { settle: 2600 });
  await page.eval(`localStorage.clear()`);
  await page.goto(S.base + '/', { settle: 2800 });

  // 1) first run: the connect screen
  check('first run shows the connect screen', await vis('#onb'));
  check('connect screen = lotus + field + Connect, no paragraphs', await page.eval(`(() => { const o = document.querySelector('#onb'); return !o.querySelector('h1, p, .onb-help') && o.querySelectorAll('input').length === 1 && o.querySelectorAll('button:not([hidden])').length === 1; })()`));
  await shot('p1-connect');
  await page.focus('#onb-code'); await page.type('ZZZZZZZZ'); await page.click('#onb-go'); await sleep(900);
  check('wrong code: short error under the button', /isn.t right/i.test(await txt('#onb-err')), await txt('#onb-err'));
  check('wrong code stores nothing', !(await page.eval(`!!localStorage.getItem('wfk')`)));
  await shot('p2-connect-wrong');

  // 2) the Laptop page, not connected: the same field, right there
  await page.eval(`document.getElementById('onb').hidden = true; location.hash = '#/laptop';`);
  await sleep(900);
  check('Laptop page shows the code field and Connect', (await vis('#lp-code')) && (await vis('#lp-connect')));
  check('Laptop page has no paragraph or helper line', await page.eval(`(() => { const b = document.querySelector('#sheet-laptop'); return [...b.querySelectorAll('p')].every(p => { const r = p.getBoundingClientRect(); return r.width <= 1 || p.hidden; }); })()`));
  check('Forget is not offered when not connected', !(await vis('#lp-forget')));
  await shot('p3-laptop-not-connected');
  await sleep(1300);
  await page.focus('#lp-code'); await page.type('QQQQQQQQ'); await page.click('#lp-connect'); await sleep(900);
  check('Laptop page, wrong code: clear error, still unpaired', /isn.t right/i.test(await txt('#lp-err')) && !(await page.eval(`!!localStorage.getItem('wfk')`)), await txt('#lp-err'));
  await shot('p4-laptop-wrong-code');

  // 3) the right code, typed on the Laptop page
  await sleep(1300);
  await page.eval(`(() => { const c = document.getElementById('lp-code'); c.value = ''; })()`);
  await page.focus('#lp-code'); await page.type(S.PAIR_CODE); await page.click('#lp-connect');
  await page.waitFor(`!!localStorage.getItem('wfk')`, 8000, 'key stored after the right code');
  check('right code connects from the Laptop page', true);
  await sleep(3200);
  const back = (await page.eval(`location.hash`)) === '#/laptop' && (await vis('#lp-forget')) && !(await vis('#lp-code'));
  check('after the reload the app is on the Laptop page, connected', back,
    JSON.stringify({ hash: await page.eval(`location.hash`), key: await page.eval(`!!localStorage.getItem('wfk')`), forget: await vis('#lp-forget'), code: await vis('#lp-code'), state: await txt('#lp-state'), onb: await vis('#onb'), sheetHidden: await page.eval(`document.getElementById('sheet-laptop').hidden`), htmlLen: await page.eval(`document.getElementById('sheet-laptop').innerHTML.length`), errs: page.errors.slice(0, 3), sw: await page.eval(`!!(navigator.serviceWorker && navigator.serviceWorker.controller)`), views: await page.eval(`[...document.querySelectorAll('.sheet')].filter(e => !e.hidden).map(e => e.id).join(',')`) }));
  await sleep(900);
  if (back) await shot('p5-laptop-connected');   // never while the typed code is on screen

  // 4) Settings and the drawer in the same state
  await page.eval(`location.hash = '#/settings'`); await sleep(900);
  check('Settings = rows only, no group headings or paragraphs', await page.eval(`(() => { const b = document.querySelector('#sheet-settings'); return !b.querySelector('.pn-group, .pn-foot, p:not([hidden])') || [...b.querySelectorAll('p')].every(p => p.getBoundingClientRect().width <= 1); })()`));
  await shot('p6-settings');
  console.log('page errors:', JSON.stringify(page.errors.slice(0, 3)));
  check('no page errors', page.errors.length === 0);
} finally { await page.close(); }
console.log(fails ? `${fails} FAILED` : 'ALL PASS');
process.exit(fails ? 1 : 0);
