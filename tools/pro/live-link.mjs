// live-link.mjs — 25% live-link proof on a TRUE fresh clone.
// Chrome at 390x844 opens the printed LAN URL (the same link the QR encodes),
// pairs, sends composer text into a real X11 sink, teaches a sign in the real
// wizard, binds a laptop action, and performs it in Go Live — the daemon's act
// layer types the bound phrase into the focused editor. Shots + RESULT.txt.
//
// REAL: clone'd daemon, LAN URL, pairing, UI, endpointer, sign bank, act layer,
// X11 effects. SUBSTITUTED (labelled): hand detections only (WFFakeTrack seam).
// usage: SITE_ENV=/tmp/fc-live25/env node --experimental-websocket tools/pro/live-link.mjs <out-dir>
import { stack, newPage, loadClips, sleep, daemon, reporter, shotTo } from './lib.mjs';
import fs from 'node:fs';

const OUT = process.argv[2];
const S = stack();
const { api, sh, sink, sinkHas, focusEditor } = daemon(S);
const R = reporter(S, OUT, 'fresh-clone live-link: pair -> send -> teach -> act -> live');
const page = await newPage(S, { cdpPort: 9472 });

try {
  /* pair via the printed LAN link (QR encodes the same URL) */
  await page.goto(`${S.base}/?k=${S.KEY}`, { settle: 2200 });
  await shotTo(page, OUT, '01-paired-composer');
  R.check('LAN link pairs: composer renders at 390x844',
    await page.eval(`!!document.querySelector('#box') && !document.querySelector('#pair, .pair, [data-pair]')`));

  /* composer -> real sink */
  const s0 = sink().length;
  await focusEditor();
  await page.eval(`document.querySelector('#box').focus()`);
  await page.type('LIVE-LINK proof from a fresh clone', { delay: 42 });
  await sleep(350);
  await page.click('#go');
  await shotTo(page, OUT, '02-send-toast');
  R.check('composer send types into the real laptop editor',
    await sinkHas(s0, 'LIVE-LINK proof from a fresh clone', 6000));
  const toast = await page.eval(`document.querySelector('#toast')?.textContent?.trim()`);
  R.check('send toast is the plain confirmation', toast === 'Text sent.', JSON.stringify(toast));

  /* teach: name -> 3 takes -> bind type.text -> finish (all through the real UI) */
  await loadClips(page, ['teach-OPEN-NOTES', 'perform-OPEN-NOTES', 'palm-hold']);
  await page.eval(`location.hash='#teach'`);
  await sleep(1100);
  await page.eval(`document.querySelector('#tw-name').focus()`);
  await page.type('fresh proof', { delay: 42 });
  await sleep(400);
  await page.eval(`document.querySelector('#tw-name-next').click()`);
  await sleep(1600);
  for (let t = 0; t < 3; t++) {
    await page.eval(`window.__play('teach-OPEN-NOTES')`);
    await sleep(300); await page.waitFor('window.__idle()', 30000);
    await sleep(1800);
  }
  await shotTo(page, OUT, '03-teach-takes');
  await page.waitFor(`document.querySelector('.tw-step[data-step="bind"]').classList.contains('on')`, 20000, 'bind step');
  await page.eval(`document.querySelector('#tw-pick').click()`);
  await sleep(1400);
  await shotTo(page, OUT, '04-act-picker');
  await ev0(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));const i=r&&r.querySelector('.act-input');if(i){i.value='fresh clone fires';i.dispatchEvent(new Event('input',{bubbles:true}))}})()`);
  await ev0(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));r&&r.querySelector('.act-use')?.click()})()`);
  await sleep(700);
  await page.eval(`document.querySelector('#tw-save').click()`);
  await sleep(1600);
  const done = await page.eval(`document.querySelector('#tw-done-t')?.textContent`);
  await shotTo(page, OUT, '05-teach-done');
  R.check('sign taught and bound through the real wizard', /is live/i.test(done || ''), done);

  /* the binding really landed in the bank, with the action dict */
  const bank = await api('GET', '/bank');
  const row = (bank.json?.signs || []).find(s => s.gloss === 'FRESH-PROOF');
  R.check('/bank carries the taught sign + type_text action',
    !!row && /type/.test(JSON.stringify(row.action || row.argv || row)),
    row ? JSON.stringify(row).slice(0, 120) : 'missing');

  /* go live: palm opens listening, the sign fires the action through /act layer */
  await focusEditor();
  await page.eval(`location.hash='#live'`);
  await sleep(1600);
  const s1 = sink().length;
  let guess = '';
  await page.eval(`window.__play('palm-hold')`);
  await page.waitFor('window.__idle()', 30000);
  await page.eval(`window.__play('perform-OPEN-NOTES')`);
  await page.waitFor('window.__idle()', 30000);
  await sleep(1500);
  guess = await page.eval(`document.querySelector('#lv-guess')?.textContent?.trim()`);
  await shotTo(page, OUT, '06-live-match');
  R.check('Go Live matches the freshly taught sign', /fresh.?proof/i.test(guess || ''), 'guess=' + JSON.stringify(guess));
  R.check('the bound action fired through the act layer into the real editor',
    await sinkHas(s1, 'fresh clone fires', 6000));

  /* UI rule: nothing dev-flavoured on screen */
  const ui = await page.eval(`document.body.innerText`);
  R.check('no helper text, URLs, ms timings or dev labels on screen',
    !/demo|recorded hand|localhost|127\.0\.0\.1|\d+\s?ms\b|debug|wispr/i.test(ui), '');
} finally {
  console.log('console errors:', page.errors.length ? page.errors : 'none');
  R.check('zero page errors', page.errors.length === 0, page.errors.join('; ').slice(0, 160));
  R.finish();
  await page.close();
}
async function ev0(js) { return page.eval(js); }
