// shots.mjs — landing-page proof shots: every section at 390x844 (m390) and
// 1440x900 (w1440), in a real headless Chrome against the built site.
//   python3 -m http.server 8395 -d landing/dist &
//   node --experimental-websocket landing/scripts/shots.mjs
// Writes proofs/ui-v2/landing/{m390,w1440}-<n>-<section>.png + RESULT.txt.
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from '../../tools/peak/cdp.mjs';

const BASE = process.argv[2] || 'http://127.0.0.1:8395/';
const OUT = process.argv[3] || path.resolve(new URL('.', import.meta.url).pathname, '../../proofs/ui-v2/landing');
const PORT = Number(process.env.SHOT_CDP || 9417);
fs.mkdirSync(OUT, { recursive: true });

const SECTIONS = [
  ['hero', 'header.hero'], ['send', '.send'], ['teach', '.teach'],
  ['try', '.try'], ['how', '.how'], ['footer', '.footer'],
];
const VIEWS = [
  ['m390', 390, 844, true, 2],
  ['w1440', 1440, 900, false, 1],
];

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  - ' + detail : ''));
};

const page = await launchChrome({ port: PORT, profile: `/tmp/landing-shots-${PORT}`, origin: BASE });
// headless Chrome defaults prefers-reduced-motion=reduce; emulate no-preference
// so the autoplay loops are actually playing in proof shots.
await page.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });
const logErrors = [];
page.on('Log.entryAdded', (e) => {
  if (e.entry && (e.entry.level === 'error')) logErrors.push(e.entry.source + ': ' + e.entry.text);
});

try {
  for (const [tag, w, h, mobile, dpr] of VIEWS) {
    await page.viewport(w, h, { mobile, dpr });
    await page.goto(BASE, { settle: 1200 });
    await sleep(2600); // lotus bloom + fonts

    // --- global checks -------------------------------------------------------
    const css = await page.eval(`(() => {
      const out = { sbw: false, wk: false, smooth: getComputedStyle(document.documentElement).scrollBehavior };
      for (const sh of document.styleSheets) {
        try {
          for (const r of sh.cssRules) {
            const t = r.cssText || '';
            if (/scrollbar-width\\s*:\\s*none/.test(t)) out.sbw = true;
            if (/::-webkit-scrollbar/.test(t) && /display\\s*:\\s*none/.test(t)) out.wk = true;
          }
        } catch (e) { /* cross-origin sheet */ }
      }
      return out;
    })()`);
    check(`${tag} scrollbar hidden (scrollbar-width:none + ::-webkit-scrollbar{display:none})`, css.sbw && css.wk, JSON.stringify(css));
    check(`${tag} smooth scroll`, css.smooth === 'smooth', css.smooth);

    // --- section shots -------------------------------------------------------
    for (let i = 0; i < SECTIONS.length; i++) {
      const [name, sel] = SECTIONS[i];
      const top = await page.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); return e ? e.getBoundingClientRect().top + scrollY : -1; })()`);
      if (top < 0) { check(`${tag}-${i + 1}-${name} exists`, false, sel); continue; }
      await page.eval(`scrollTo({ top: ${Math.max(0, top - 6)}, behavior: 'instant' })`);
      // let reveal animations land (How nodes stagger to ~3.4s, footer ~3s)
      if (name === 'how') {
        try {
          await page.waitFor(`+getComputedStyle(document.querySelector('.node-live')).opacity > 0.95`, 9000, 'how nodes in');
        } catch (e) { /* shot anyway; RESULT records it */ }
        await sleep(600);
      } else if (name === 'footer') {
        await sleep(3400);
      } else {
        await sleep(900);
      }
      const file = path.join(OUT, `${tag}-${i + 1}-${name}.png`);
      await page.shot(file);

      // no taglines/helper paragraphs: visible <p> allowed only inside terminal
      // cards (.tc) and the Try HUD (vendored pipeline text)
      const stray = await page.eval(`(() => {
        const bad = [];
        for (const p of document.querySelectorAll('${sel} p')) {
          const r = p.getBoundingClientRect(), c = getComputedStyle(p);
          const vis = r.width > 1 && r.height > 1 && c.display !== 'none' && c.visibility !== 'hidden' && +c.opacity > 0.05 && r.bottom > 0 && r.top < innerHeight;
          if (!vis) continue;
          if (p.closest('.tc, .hud, .teachbox') || p.hasAttribute('data-try-status') || p.hasAttribute('data-try-note') || p.hasAttribute('data-try-words')) continue;
          bad.push(p.className + ':' + p.textContent.trim().slice(0, 60));
        }
        return bad;
      })()`);
      check(`${tag}-${i + 1}-${name}: no taglines/helper paragraphs`, stray.length === 0, stray.join(' | '));

      if (name === 'how') {
        const hw = await page.eval(`(() => {
          const txt = (s) => (document.querySelector(s) || {}).textContent || '';
          return {
            clone: txt('.node-clone .cmd'), install: txt('.node-install .cmd'),
            start: txt('.node-start .cmd'),
            url: /https?:\\/\\/[\\d.]+:\\d+/.test(txt('.node-start')),
            qr: (document.querySelector('.node-start .qr path') || {}).getAttribute ? document.querySelector('.node-start .qr path').getAttribute('d').length : 0,
          };
        })()`);
        check(`${tag} how: git clone -> install -> wrap start -> LAN URL + QR`,
          /git clone/.test(hw.clone) && /npm install/.test(hw.install) && /wrap start/.test(hw.start) && hw.url && hw.qr > 500,
          JSON.stringify(hw).slice(0, 220));
      }
    }
    // back to top for the next viewport's first paint sanity
    const errs = page.errors.concat(page.consoleLog.filter((l) => l.startsWith('[error]')), logErrors)
      .filter((e) => !/favicon|ERR_ABORTED|Autofill/i.test(e));
    check(`${tag} zero console errors`, errs.length === 0, errs.slice(0, 3).join(' | '));
    page.errors.length = 0; page.consoleLog.length = 0; logErrors.length = 0;
  }
} finally {
  await page.close();
}

const body = results.map((r) => `${r.ok ? 'PASS' : 'FAIL'}\t${r.name}\t${r.detail}`).join('\n');
fs.writeFileSync(path.join(OUT, 'RESULT.txt'),
  `# ${new Date().toISOString()}  Sign-Flow landing proofs\n# ${BASE}  viewports: 390x844@2 (m390), 1440x900 (w1440)\n${body}\n`);
process.exitCode = results.every((r) => r.ok) ? 0 : 1;
