// flow-packs.mjs — Ready-made signs (Hello / Namaste / Thank you), real run on the integrated server.
// Hands = synthetic kp42 clips (labelled); everything else is real. No recorded takes ship with the laptop yet,
// so Hello and Thank you are taught from the list in three takes; Namaste is the built-in palms-together pause.
import path from 'node:path';
import { stack, newPage, loadClips, playAndWait, sleep, shotTo, reporter, daemon, root } from './lib.mjs';
const OUT = path.join(root, 'proofs/ui-v2/site-packs');
const S = stack(process.env.SITE_ENV || '/tmp/wf-site-overlay/env');
const { check, finish } = reporter(S, OUT, 'Ready-made signs - overlay daemon (live-merge server)');
const { api, sink, sinkHas, focusEditor } = daemon(S);
const clear = async () => { for (const r of (((await api('GET', '/bank')).json || {}).signs || [])) await api('DELETE', '/bank?gloss=' + encodeURIComponent(r.gloss)); };
await clear();
const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9016) });
const openGrid = async () => {
  await page.click('#menu'); await sleep(600);
  await page.click('#tray-teach'); await sleep(700);
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 12000, 'grid');
  await sleep(900);
};
const teachPack = async (tileId, tag) => {
  await openGrid();
  await page.eval(`document.querySelector('#sheet-teach .sheet-body').scrollTo(0, 99999)`); await sleep(400);
  await shotTo(page, OUT, tag + '-1-grid');
  await page.eval(`document.querySelector('.qa-tile[data-id="${tileId}"]').scrollIntoView({ block: 'center' })`); await sleep(300);
  await page.click(`.qa-tile[data-id="${tileId}"]`); await sleep(500);
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 10000, 'record');
  await sleep(700);
  await shotTo(page, OUT, tag + '-2-record');
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 45000, 'ready');
  await sleep(900);
  await shotTo(page, OUT, tag + '-3-ready');
  return page.eval(`document.querySelector('#tw-done-m').textContent`);
};
const perform = async (gloss, expect) => {
  await page.click('#tw-done-live');
  await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera');
  await sleep(600); await focusEditor();
  const before = sink().length;
  await api('POST', '/live/listen', { on: true }); await sleep(500);
  await playAndWait(page, 'perform-TYPE-HELLO');
  const m = await (async () => { for (let i = 0; i < 60; i++) { const lm = (((await api('GET', '/live/state')).json || {}).last_match || {}); if (lm.gloss === gloss && lm.accepted) return lm; await sleep(250); } return null; })();
  check(gloss + ': the daemon recognised it', !!m, m ? 'd=' + (+m.dist).toFixed(3) : 'no accepted match');
  check(gloss + ': the words landed in the real editor', await sinkHas(before, expect, 7000), expect);
  return m;
};
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await loadClips(page, ['teach-TYPE-HELLO', 'perform-TYPE-HELLO', 'palm-hold', 'namaste', 'idle']);
  // the list is the laptop's own
  const packs = (((await api('GET', '/packs')).json || {}).packs) || [];
  check('GET /packs lists Hello, Namaste, Thank you', ['Hello', 'Namaste', 'Thank you'].every(t => packs.some(p => p.title === t)), packs.map(p => p.title + ':' + p.state).join(', '));
  // Hello
  const said = await teachPack('pack.hello', '01-hello');
  check('Hello: Ready names the action', /type .?hello/i.test(said), said);
  const bank1 = (((await api('GET', '/bank')).json || {}).signs || []).find(s => s.gloss === 'HELLO');
  check('Hello: saved with 3 takes and the type-text action', bank1 && bank1.n_exemplars >= 3 && /hello/.test(JSON.stringify(bank1.action || bank1)), JSON.stringify(bank1 || {}).slice(0, 120));
  await perform('HELLO', 'hello');
  await shotTo(page, OUT, '01-hello-4-live');
  const list2 = (((await api('GET', '/packs')).json || {}).packs) || [];
  check('Hello now reads as ready on the laptop', (list2.find(p => p.id === 'hello') || {}).state === 'ready', JSON.stringify((list2.find(p => p.id === 'hello') || {}).state));
  await page.click('#lv-x'); await sleep(700);
  await openGrid();
  check('Hello is taught, so its tile is gone from the grid', !!(await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15 && !document.querySelector('.qa-tile[data-id="pack.hello"]')`, 12000, 'tile gone').catch(() => false)));
  await shotTo(page, OUT, '01-hello-5-grid-after');
  await page.click('#tw-x'); await sleep(600);
  // Introduce yourself (the bank holds one sign at a time: every proof sign reuses one synthetic motion)
  await api('DELETE', '/bank?gloss=HELLO');
  const said2 = await teachPack('pack.intro', '02-intro');
  check('Introduce yourself: Ready names the action', /Introduce yourself/i.test(said2), said2);
  await perform('INTRO', 'Introduce yourself');
  await shotTo(page, OUT, '02-intro-4-live');
  // Namaste: built in, palms together pauses listening
  await api('POST', '/live/listen', { on: true }); await sleep(400);
  const was = ((await api('GET', '/live/state')).json || {}).listening;
  await playAndWait(page, 'namaste');
  let paused = false; for (let i = 0; i < 40 && !paused; i++) { paused = ((await api('GET', '/live/state')).json || {}).listening === false; if (!paused) await sleep(250); }
  check('Namaste: palms together pauses listening', was === true && paused, 'listening ' + was + ' -> ' + !paused);
  await sleep(700);
  await shotTo(page, OUT, '03-namaste-paused');
  const errs = page.errors.filter(e => !/favicon|ServiceWorker|manifest/i.test(e));
  check('no page errors', errs.length === 0, errs.slice(0, 3).join(' | '));
} catch (e) {
  check('flow completed', false, String(e && e.message || e).slice(0, 300));
} finally { await page.close(); }
finish();
