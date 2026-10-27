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

// ---- 2. Teach wizard: record step -------------------------------------------
await page.eval(`location.hash='#teach'`);
await sleep(1100);
await page.eval(`document.querySelector('#tw-name').focus()`);
await page.type('shot check', { delay: 30 });
await page.eval(`document.querySelector('#tw-name-next').click()`);
await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 20000, 'record step');
await page.waitFor(`!!window.__clips && !!window.__q`, 10000, 'fake track armed');
await scan(page, 'teach-record');
await shotTo(page, OUT, '02-teach-record');

// ---- 3. action grid (the tile sheet) ----------------------------------------
await page.eval(`document.querySelector('#tw-pick')?.click()`);
await sleep(1400);
await scan(page, 'action-grid');
await shotTo(page, OUT, '03-teach-grid');

// ---- 4. @ picker — app tile grid listing the laptop's real installed apps ---
const hasAppRow = await page.eval(`(()=>{
  const tiles=[...document.querySelectorAll('.act-app')];
  if(!tiles.length) return 'no-tiles';
  const names=tiles.map(t=>t.textContent.trim()).filter(Boolean);
  return 'apps:'+names.slice(0,4).join(',');
})()`);
check('@ picker lists installed apps', /^apps:.+/.test(hasAppRow || ''), hasAppRow);
await sleep(500);
await scan(page, 'app-picker');
await shotTo(page, OUT, '04-app-picker');
await page.eval(`document.querySelector('#act-x')?.click()`);
await sleep(400);

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
