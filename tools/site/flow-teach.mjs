// flow-teach.mjs — real-run proof for Teach v3 on the OVERLAY daemon :9011.
// usage: SITE_ENV=/tmp/wf-site-overlay/env node --experimental-websocket tools/site/flow-teach.mjs
//
// Proves, all real (only the hand detections are synthetic kp42 clips, labelled):
//  (a) ready-made signs render from the real GET /packs
//  (b) a full Teach "type hello site" end to end: pick an action through the
//      picker, 3 takes through the daemon's own endpointer, the training view,
//      done
//  (c) IMMEDIATE USE with no restart: Go Live -> palm-hold -> perform the new
//      sign -> the text lands in the real SinkEdit editor
//  (d) the old path still works: name -> record -> bind -> done (old ids)
//  (e) 390x844@2x screenshots of every stage + start screen at 360/768/1280.
import path from 'node:path';
import { stack, newPage, loadClips, playAndWait, sleep, shotTo, reporter, daemon, root, recorder } from './lib.mjs';

const OUT = path.join(root, 'proofs/ui-v2/site-teach');
const S = stack(process.env.SITE_ENV || '/tmp/wf-site-overlay/env');
const { check, finish } = reporter(S, OUT, 'Teach v3 — overlay daemon');
const { api, sh, sink, sinkHas } = daemon(S);

const GLOSS = 'TYPE-HELLO-SITE';
const OLD = 'OLD-PATH-SITE';

const openTeach = async (page) => {
  await page.click('#menu'); await sleep(600);
  await page.click('#tray-teach'); await sleep(600);
  await page.waitFor(`!document.querySelector('#sheet-teach').hidden`, 8000, 'teach sheet open');
};

// clean any prior runs so names are free
// scratch daemon: start from an empty bank (every proof sign reuses the same synthetic motion, so an older
// sign with that motion would correctly win the match)
for (const row of (((await api('GET', '/bank')).json || {}).signs || [])) await api('DELETE', '/bank?gloss=' + encodeURIComponent(row.gloss));

const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9016) });
const rec = process.env.SITE_RECORD ? recorder(page, path.join(OUT, '_rec')) : null;
if (rec) await rec.start();
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await loadClips(page, ['teach-TYPE-HELLO', 'palm-hold', 'perform-TYPE-HELLO', 'idle']);

  /* ---------- (a) ready-made signs from GET /packs ---------- */
  await openTeach(page);
  // ready-made signs sit behind one quiet text link under the grid
  await page.waitFor(`!document.querySelector('#tw-packs-toggle').hidden`, 8000, 'ready-made link');
  await page.click('#tw-packs-toggle'); await sleep(500);
  await page.waitFor(`!document.querySelector('#tw-packs').hidden && document.querySelectorAll('#tw-packs .tw-pk').length > 0`, 8000, 'packs rendered');
  const packNames = await page.eval(`[...document.querySelectorAll('#tw-packs .tw-pk .tw-pk-n')].map(n => n.textContent)`);
  check('ready-made signs render from the real GET /packs', packNames.length >= 3, packNames.join(', '));
  check('Hello / Namaste / I am Govinda listed', ['Hello', 'Namaste', 'I am Govinda'].every(t => packNames.includes(t)), packNames.join(', '));
  const builtin = await page.eval(`[...document.querySelectorAll('#tw-packs .tw-pk')].some(r => /Namaste/.test(r.textContent) && r.querySelector('.tw-pk-off'))`);
  check('Namaste shows as built in (not selectable)', builtin, '');
  await shotTo(page, OUT, '01-start');

  /* ---------- (b) full Teach: pick action -> 3 takes -> train -> done ---------- */
  // pick the action with the one-tap grid: the "Type text" tile + the words (FOUNDER-SIMPLIFY-1433)
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 10000, 'quick grid');
  await page.click('.qa-tile[data-id="text"]'); await sleep(400);
  await page.focus('#qa-text-in'); await page.type('hello-from-site'); await sleep(400);
  check('an action was chosen through the quick grid', await page.eval(`!document.querySelector('#tw-name-next').disabled`), await page.eval(`document.querySelector('#tw-pick-tx').textContent`));

  // name it + Record
  await page.focus('#tw-name');
  await page.eval(`(() => { const i=document.querySelector('#tw-name'); i.value='type hello site'; i.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(300);
  check('gloss preview matches the id contract', await page.eval(`document.querySelector('#tw-gloss').textContent`) === GLOSS, await page.eval(`document.querySelector('#tw-gloss').textContent`));
  await shotTo(page, OUT, '02-action-chosen');
  await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 4000, 'Record enabled');
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 8000, 'record step');
  await sleep(700);
  await shotTo(page, OUT, '03-record-armed');

  // 3 takes through the daemon's endpointer
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  for (let n = 1; n <= 3; n++) {
    await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length >= ${n}`, 30000, 'take ' + n);
    if (n === 2) await shotTo(page, OUT, '04-take-2');
  }
  check('3 takes stored by the daemon\'s own endpointer', true);

  // the training view (screenshot mid-way)
  await page.waitFor(`document.querySelector('.tw-step[data-step="train"]').classList.contains('on') || document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 10000, 'train/done');
  const sawTrain = await page.eval(`document.querySelector('.tw-step[data-step="train"]').classList.contains('on')`);
  if (sawTrain) {
    // let the entry animation settle and the paths finish drawing, then shoot mid-training
    await page.waitFor(`document.querySelector('#tw-train li[data-k="read"]').classList.contains('done')`, 8000, 'reading stage done').catch(() => {});
    await sleep(350);
    await shotTo(page, OUT, '05-training');
  }
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 12000, 'done step');
  if (!sawTrain) await shotTo(page, OUT, '05-training');
  await sleep(900);                       // entry animation settled
  await shotTo(page, OUT, '06-done');
  check('training view led to the done screen', true, sawTrain ? 'train view shown' : 'train info absent — no false check');

  // verify it is really in the bank
  const bank = (await api('GET', '/bank')).json || {};
  const sign = (bank.signs || []).find(s => s.gloss === GLOSS);
  check('the new sign is in the laptop bank', !!sign, sign ? (sign.exemplar_count + ' takes') : 'missing');

  /* ---------- (c) immediate use with no restart ---------- */
  await page.eval(`window.dispatchEvent(new Event('focus'))`);
  await page.click('#tw-done-live');
  await page.waitFor(`document.querySelector('#lv-cover') ? document.querySelector('#lv-cover').hidden : !document.querySelector('#sheet-live').hidden`, 40000, 'Go Live ready');
  await sleep(900);
  sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(300);
  // start listening if not already
  if (await page.eval(`document.querySelector('#lv-badge') && !document.querySelector('#lv-badge').classList.contains('listen')`)) {
    await playAndWait(page, 'palm-hold');
    await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  }
  const before = sink().length;
  await playAndWait(page, 'perform-TYPE-HELLO');
  let matched = false;
  for (let i = 0; i < 60; i++) {
    const m = (((await api('GET', '/live/state')).json || {}).last_match) || {};
    if (m.gloss === GLOSS && m.accepted) { matched = true; break; }
    await sleep(250);
  }
  const landed = await sinkHas(before, 'hello-from-site', 6000);
  check('immediate use: the just-taught sign fired without a restart', matched || landed, matched ? 'daemon matched it' : 'via sink');
  check('the text landed in the real SinkEdit editor', landed, 'sink.txt got hello-from-site');
  await shotTo(page, OUT, '07-live-result');
  // back home
  if (await page.eval(`!!document.querySelector('#lv-x')`)) { await page.click('#lv-x'); await sleep(700); }

  /* ---------- (d) old path: name -> record -> bind -> done ---------- */
  await openTeach(page);
  await page.focus('#tw-name');
  await page.eval(`(() => { const i=document.querySelector('#tw-name'); i.value='old path site'; i.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(300);
  check('old path: gloss preview', await page.eval(`document.querySelector('#tw-gloss').textContent`) === OLD, await page.eval(`document.querySelector('#tw-gloss').textContent`));
  await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 4000, 'Record enabled (no action)');
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 8000, 'record step (old)');
  await sleep(600);
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  for (let n = 1; n <= 3; n++) await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length >= ${n}`, 30000, 'old take ' + n);
  await page.waitFor(`document.querySelector('.tw-step[data-step="bind"]').classList.contains('on')`, 10000, 'bind step (old path)');
  check('old path reaches the bind step after 3 takes', true);
  check('three take previews drawn (tw-prev)', await page.eval(`document.querySelectorAll('#tw-prev .tkv canvas').length`) === 3, String(await page.eval(`document.querySelectorAll('#tw-prev .tkv canvas').length`)));
  await page.click('#tw-pick2');
  await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 8000, 'picker open (old)');
  await sleep(800);
  const haveOwn2 = await page.eval(`!!document.querySelector('.act-own-in')`);
  if (haveOwn2) {
        await page.eval(`(() => { const i=document.querySelector('.act-own-in'); i.value='type old-path-site'; i.dispatchEvent(new Event('input',{bubbles:true})); })()`);
        await sleep(400);
        await page.eval(`(() => { const b=document.querySelector('.act-own-use'); if(b) b.click(); })()`);
  } else {
    await page.eval(`(() => { const r=[...document.querySelectorAll('#act-list .act-row')].find(r=>/type text/i.test(r.textContent)); if(!r) return; r.click(); const inp=r.querySelector('.act-input'); if(inp){ inp.value='old-path-site'; inp.dispatchEvent(new Event('input',{bubbles:true})); } const use=r.querySelector('.act-use'); if(use) use.click(); })()`);
  }
  await page.waitFor(`document.querySelector('#sheet-actions').hidden && !document.querySelector('#tw-save').disabled`, 8000, 'action bound + save enabled');
  await page.click('#tw-save');
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 14000, 'done step (old path)');
  check('old path (name->record->bind->done) completes', true);
  const bank2 = (await api('GET', '/bank')).json || {};
  check('old-path sign is in the bank', (bank2.signs || []).some(s => s.gloss === OLD), '');

  /* ---------- (e) responsive start-screen screenshots ---------- */
  for (const [w, h, mob, dpr, tag] of [[360, 640, true, 2, '08-start-360'], [768, 1024, false, 2, '09-start-768'], [1280, 800, false, 2, '10-start-1280']]) {
    await page.viewport(w, h, { mobile: mob, dpr });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
    await openTeach(page);
    await page.waitFor(`document.querySelector('.tw-step[data-step="name"]').classList.contains('on')`, 6000, 'start screen at ' + w);
    await sleep(700);
    await shotTo(page, OUT, tag);
    const noScroll = await page.eval(`document.documentElement.scrollWidth <= ${w} + 1`);
    check('no horizontal scroll at ' + w + 'px', noScroll, 'scrollWidth=' + await page.eval(`document.documentElement.scrollWidth`));
  }

  const errs = page.errors.filter(e => !/favicon|ServiceWorker|manifest/i.test(e));
  check('no page errors', errs.length === 0, errs.slice(0, 3).join(' | '));
} catch (e) {
  check('flow completed', false, String(e && e.message || e).slice(0, 300));
} finally {
  if (rec) { const r = await rec.stop('teach-flow.mp4'); check('screen recording of the whole flow (mp4)', r.ok, r.frames + ' frames -> teach-flow.mp4'); }
  await page.close();
}
finish();
