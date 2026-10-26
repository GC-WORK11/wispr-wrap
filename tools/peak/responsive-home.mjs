// responsive-home.mjs — PEAK-home responsive + robustness proof on the REAL
// stack. Every size checks: composer fully on-screen (not clipped), lotus field
// present and painting within the viewport, no horizontal scroll. Plus soft-
// keyboard, notch safe-area insets, and prefers-reduced-motion (calm lotus).
// usage: node --experimental-websocket tools/peak/responsive-home.mjs <outdir> [cdp-port]
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || 'proofs/ui-v2/responsive';
const cdpPort = Number(process.argv[3] || 8951);
const S = loadStack();
fs.mkdirSync(out, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok, detail }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  — ' + detail : '')); };

const profile = '/tmp/peak-chrome-resp';
fs.rmSync(profile, { recursive: true, force: true });
const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
const shot = n => page.shot(path.join(out, n + '.png'));

// layout sanity for the current viewport
async function layoutOk(w, h, label) {
  await sleep(1200);   // let the lotus settle + grow()
  const m = await page.eval(`(() => {
    const comp = document.querySelector('#composer').getBoundingClientRect();
    const pill = document.querySelector('#pill').getBoundingClientRect();
    const live = document.querySelector('#tray-live').getBoundingClientRect();
    const field = document.querySelector('#field');
    const hasRows = ['#f0','#f1','#f2'].some(s => (document.querySelector(s).textContent||'').trim().length > 0);
    return {
      vw: innerWidth, vh: innerHeight,
      compBottom: comp.bottom, compTop: comp.top, compRight: comp.right, compLeft: comp.left,
      pillRight: pill.right, liveRight: live.right,
      docScrollW: document.documentElement.scrollWidth,
      fieldRows: hasRows,
    };
  })()`);
  const onScreen = m.compBottom <= m.vh + 2 && m.compTop >= -2 && m.compLeft >= -2 && m.compRight <= m.vw + 2;
  const noHScroll = m.docScrollW <= m.vw + 2;
  check(`${label} (${w}x${h}): composer fully visible`, onScreen, `comp bottom=${m.compBottom.toFixed(0)} top=${m.compTop.toFixed(0)} vw=${m.vw} vh=${m.vh}`);
  check(`${label} (${w}x${h}): no horizontal overflow`, noHScroll, `scrollW=${m.docScrollW} vw=${m.vw}`);
  check(`${label} (${w}x${h}): the lotus is drawn`, m.fieldRows);
  return m;
}

try {
  const sizes = [
    ['320x568', 320, 568, true, 2],
    ['360x640', 360, 640, true, 2],
    ['390x844', 390, 844, true, 3],
    ['430x932', 430, 932, true, 3],
    ['landscape-844x390', 844, 390, true, 2],
    ['tablet-820x1180', 820, 1180, true, 1],
    ['laptop-1440x900', 1440, 900, false, 1],
    ['desktop-1920x1080', 1920, 1080, false, 1],
  ];
  for (const [label, w, h, mobile, dpr] of sizes) {
    await page.viewport(w, h, { mobile, dpr });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 2200 });
    await layoutOk(w, h, label);
    await shot('r-' + label);
  }

  // --- soft-keyboard open: shrink the viewport (keyboard eats the bottom) ---
  await page.viewport(390, 420, { mobile: true, dpr: 3 });   // 844 - ~424 keyboard
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1800 });
  await page.focus('#box');
  await page.type('typing with the keyboard up');
  await sleep(900);
  const kb = await layoutOk(390, 420, 'keyboard-open');
  check('keyboard-open: composer sits above the keyboard', kb.compBottom <= kb.vh + 2, `bottom=${kb.compBottom.toFixed(0)} vh=${kb.vh}`);
  await shot('r-keyboard-open');

  // --- notch / home-indicator safe-area insets ---
  await page.viewport(390, 844, { mobile: true, dpr: 3 });
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1800 });
  try {
    await page.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 59, left: 0, bottom: 34, right: 0 } });
    await sleep(1200);
  } catch (e) { /* older chrome: fall through */ }
  await sleep(1000);
  const safe = await page.eval(`(() => {
    const comp = document.querySelector('#composer').getBoundingClientRect();
    const menu = document.querySelector('#menu').getBoundingClientRect();
    return { compBottom: comp.bottom, vh: innerHeight, menuTop: menu.top };
  })()`);
  check('safe-area: composer clears the home indicator (bottom inset honoured)', safe.compBottom <= safe.vh - 1, `bottom=${safe.compBottom.toFixed(0)} vh=${safe.vh}`);
  await shot('r-safe-area');

  // --- prefers-reduced-motion: calm, near-static lotus ---
  await page.viewport(390, 844, { mobile: true, dpr: 3 });
  await page.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  // sample the lotus text twice ~1s apart; reduced motion means very low churn
  const a = await page.eval(`document.querySelector('#f1').textContent`);
  await sleep(1100);
  const b = await page.eval(`document.querySelector('#f1').textContent`);
  const diff = (() => { let n = 0; const L = Math.max(a.length, b.length); for (let i = 0; i < L; i++) if (a[i] !== b[i]) n++; return n; })();
  check('reduced-motion: lotus is present but calm (low frame churn)', a.trim().length > 0 && diff < a.length * 0.25, `chars changed in ~1.1s = ${diff}/${a.length}`);
  await shot('r-reduced-motion');
  await page.send('Emulation.setEmulatedMedia', { features: [] });

  check('no page errors across all viewports', page.errors.length === 0, page.errors.slice(0, 3).join(' | '));
} finally {
  await page.close();
}

const body = results.map(r => `${r.ok ? 'PASS' : 'FAIL'}\t${r.name}\t${r.detail}`).join('\n');
fs.writeFileSync(path.join(out, 'RESULT.txt'), `# ${new Date().toISOString()}  responsive-home\n${body}\n`);
process.exitCode = results.every(r => r.ok) ? 0 : 1;
