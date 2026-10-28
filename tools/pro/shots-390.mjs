// shots-390.mjs — fresh 390x844 real-run proof shots on the latest head:
// composer, Teach wizard + action grid, @ app-picker row, Go Live control.
// Each view's visible text is scanned for raw backend text / ms timings /
// URLs / helper phrasing before the shot counts.
// usage: SITE_ENV=<env> OUT=<dir> node --experimental-websocket tools/pro/shots-390.mjs
import { stack, newPage, loadClips, shotTo, sleep, daemon } from './lib.mjs';
import fs from 'node:fs';
import path from 'node:path';

const S = stack();
const OUT = process.env.OUT || path.join(process.cwd(), 'proofs/ui-v2/shots-390');
fs.mkdirSync(OUT, { recursive: true });
const { api } = daemon(S);
const R = [];
const check = (n, ok, d) => { R.push([ok ? 'PASS' : 'FAIL', n, String(d || '').slice(0, 120)]); console.log(R[R.length-1].join('  ')); };

// what must never reach the screen (FOUNDER-RULE-1652)
const LEAK = /(\d+\s*ms\b|https?:\/\/|localhost|127\.0\.0\.1|sink\b|daemon|backend|debug|X-WF|keypoints?|dtw|\bd=\s*[\d.])/i;
// collect text a user can actually see: skip [hidden]/display:none, the
// dev seam (body.dev gated fields — clipped to 1px by CSS when off), and
// anything an ancestor clips to zero size
const scan = async (page, name) => {
  const txt = await page.eval(`(()=>{
    const dev = document.body.classList.contains('dev');
    if (dev) return '!! body.dev is ON — dev seam visible';
    const out = [];
    const walk = el => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (cs.display === 'none' || cs.visibility === 'hidden' ||
          el.hidden || (r.width <= 1 && r.height <= 1)) return;
      for (const n of el.childNodes) {
        if (n.nodeType === 3) out.push(n.textContent);
        else if (n.nodeType === 1) walk(n);
      }
    };
    walk(document.body);
    return out.join('\\n');
  })()`);
  const m = txt.match(LEAK);
  check(name + ' clean text', !m, m ? 'leak: ' + JSON.stringify(m[0]) : 'no raw text/urls/ms');
};

const page = await newPage(S);
await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
await loadClips(page, ['teach-OPEN-NOTES', 'palm-hold', 'idle']);

// ---- 1. composer ------------------------------------------------------------
await page.waitFor(`document.querySelector('#box')`, 10000, 'composer');
await page.focus('#box');
await page.type('proof shot', { delay: 30 });
await scan(page, 'composer');
await shotTo(page, OUT, '01-composer');

// ---- 1b. hamburger sidebar (FOUNDER-UI-SPEC-1026 §3 proof) -------------------
await page.eval(`document.querySelector('#menu')?.click()`);
await page.waitFor(`document.querySelector('#drawer').classList.contains('open')`, 8000, 'drawer open');
await sleep(450);
const drRows = await page.eval(`[...document.querySelectorAll('#drawer-nav .dr-item')].filter(r=>!r.hidden).length`);
check('sidebar shows its rows', drRows >= 3, 'rows=' + drRows);
await scan(page, 'sidebar');
await shotTo(page, OUT, '01b-sidebar');
await page.eval(`document.querySelector('#dr-close')?.click()`);
await sleep(400);

// ---- 2. Teach: the one-tap quick-action grid (FOUNDER-SIMPLIFY-1433) ---------
await page.eval(`location.hash='#teach'`);
await sleep(1100);
await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 15000, 'qa grid');
const nTiles = await page.eval(`document.querySelectorAll('.qa-tile').length`);
check('teach grid shows every laptop action', nTiles >= 15, 'tiles=' + nTiles);
await sleep(900);                                   // theme icons settle
await scan(page, 'teach-grid');
await shotTo(page, OUT, '02-teach-grid');

// ---- 3. @ picker — the app.open tile opens the real picker ------------------
await page.eval(`document.querySelector('.qa-tile[data-id="app.open"]')?.click()`);
await sleep(1600);
const hasAppRow = await page.eval(`(()=>{
  const apps=[...document.querySelectorAll('.act-app')];
  if(!apps.length) return 'no-apps';
  const r=apps[1]||apps[0];
  r.scrollIntoView();
  return 'apps:'+apps.slice(0,4).map(x=>x.textContent.trim()).join(',');
})()`);
check('@ picker lists installed apps', /^apps:.+/.test(hasAppRow || ''), hasAppRow);
await sleep(500);
await scan(page, 'app-picker');
await shotTo(page, OUT, '04-app-picker');
await page.eval(`document.querySelector('#act-x')?.click()`);
await sleep(400);

// ---- 4. record step ---------------------------------------------------------
await page.eval(`(() => { const i=document.querySelector('#tw-name'); i.value='shot check'; i.dispatchEvent(new Event('input',{bubbles:true})); })()`);
await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 8000, 'Record enabled');
await page.eval(`document.querySelector('#tw-name-next').click()`);
await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 20000, 'record step');
await page.waitFor(`!!window.__clips && !!window.__q`, 10000, 'fake track armed');
await scan(page, 'teach-record');
await shotTo(page, OUT, '04-teach-record');
await page.eval(`document.querySelector('#tw-x')?.click()`);
await sleep(500);

// ---- 5. Go Live — control mode, listening ------------------------------------
await page.eval(`location.hash='#live'`);
await sleep(2500);
await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
await page.eval(`document.querySelector('#lv-seg [aria-selected],#lv-seg button:last-child')?.click()`).catch(()=>{});
// switch to Control explicitly if the seg exposes two buttons
await page.eval(`(()=>{const b=[...document.querySelectorAll('#lv-seg *')].find(x=>/^Control$/i.test(x.textContent));b&&(b.closest('button')||b).click()})()`).catch(()=>{});
await page.eval(`window.__play('palm-hold')`).catch(()=>{});
await sleep(1600);
await scan(page, 'go-live');
await shotTo(page, OUT, '05-go-live');

fs.writeFileSync(path.join(OUT, 'RESULT.txt'),
  `# ${new Date().toISOString()} 390x844 proof shots — latest head\n` +
  R.map(r => r.join('\t')).join('\n') + '\n');
const bad = R.filter(r => r[0] === 'FAIL').length;
console.log(bad ? 'FAIL ' + bad : 'ALL PASS ' + R.length + '/' + R.length);
process.exit(bad ? 1 : 0);
