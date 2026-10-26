// flow-quick.mjs — real-run proof for the simplified Teach start (FOUNDER-SIMPLIFY-1433) on the OVERLAY daemon.
// One tap on a tile -> Record -> 3 takes through the daemon's endpointer -> training -> Ready -> the sign fires
// the real action (Enter and Type text land in the real SinkEdit editor). Hands are synthetic kp42 clips (labelled).
import path from 'node:path';
import { stack, newPage, loadClips, playAndWait, sleep, shotTo, reporter, daemon, root, recorder } from './lib.mjs';
const OUT = path.join(root, 'proofs/ui-v2/site-quick');
const S = stack(process.env.SITE_ENV || '/tmp/wf-site-overlay/env');
const { check, finish } = reporter(S, OUT, 'Quick actions - overlay daemon');
const { api, sink, sinkHas, focusEditor } = daemon(S);
// scratch daemon: start from an empty bank, because every proof sign reuses the same synthetic motion
// and an older sign with that motion would (correctly) win the match
for (const row of (((await api('GET', '/bank')).json || {}).signs || [])) await api('DELETE', '/bank?gloss=' + encodeURIComponent(row.gloss));
const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9016) });
const rec = process.env.SITE_RECORD ? recorder(page, path.join(OUT, '_rec')) : null;
if (rec) await rec.start();
const teach = async (tileSel, afterTile) => {
  await page.click('#menu'); await sleep(600);
  await page.click('#tray-teach'); await sleep(700);
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 10000, 'grid rendered');
  await sleep(500);
  if (tileSel) await page.click(tileSel);
  if (afterTile) await afterTile();
};
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await loadClips(page, ['teach-TYPE-HELLO', 'palm-hold', 'perform-TYPE-HELLO', 'idle']);
  await page.click('#menu'); await sleep(600);
  await page.click('#tray-teach'); await sleep(700);
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 10000, 'grid rendered');
  await sleep(600);
  const labels = await page.eval(`[...document.querySelectorAll('.qa-tile .qa-l')].map(e => e.textContent)`);
  check('the grid lists the one-tap actions', ['Switch window', 'Maximize', 'Minimize', 'Close window', 'Show desktop', 'New tab', 'Close tab', 'Copy', 'Paste', 'Undo', 'Enter', 'Open app', 'Type text'].every(l => labels.includes(l)), labels.join(' | '));
  const minH = await page.eval(`Math.min(...[...document.querySelectorAll('.qa-tile')].map(e => e.getBoundingClientRect().height))`);
  check('tap targets are at least 48px', minH >= 48, 'min tile height ' + Math.round(minH));
  check('no developer words on the start screen', !/(gloss|daemon|argv|wfd|keypoint|python|wm_class|tier)/i.test(await page.eval(`document.querySelector('.tw-step[data-step="name"]').innerText`)));
  check('Record is disabled until a tile is selected', await page.eval(`document.querySelector('#tw-name-next').disabled`));
  await shotTo(page, OUT, '01-grid');
  // --- Enter: a real, checkable effect in the real editor ---
  await page.click('.qa-tile[data-id="enter"]'); await sleep(400);
  check('tap selects the tile and enables the one primary button', await page.eval(`document.querySelector('.qa-tile[data-id="enter"]').getAttribute('aria-pressed') === 'true' && !document.querySelector('#tw-name-next').disabled`));
  await shotTo(page, OUT, '02-selected');
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 8000, 'record');
  await sleep(700);
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  for (let n = 1; n <= 3; n++) await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length >= ${n}`, 30000, 'take ' + n);
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 15000, 'done');
  await sleep(900);
  await shotTo(page, OUT, '03-ready');
  const t = await page.eval(`document.querySelector('#tw-done-m').textContent`);
  check('Ready says what the laptop will do', /press Enter/i.test(t), t);
  const bank = await api('GET', '/bank');
  const row = ((bank.json || {}).signs || []).find(s => s.gloss === 'ENTER');
  check('the sign is in the laptop bank with 3 takes and the Enter action', row && (row.n_exemplars || 0) >= 3 && /Return/.test(JSON.stringify(row)), JSON.stringify(row || {}).slice(0, 140));
  // immediate use, no restart: Go Live -> palm -> perform -> a real Enter lands in the real editor
  await page.click('#tw-done-live');
  await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
  await focusEditor();
  const nl = () => (sink().match(/\n/g) || []).length;
  const before = nl();
  await playAndWait(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await playAndWait(page, 'perform-TYPE-HELLO');
  let fired = false, matched = '';
  for (let i = 0; i < 48 && !fired; i++) {
    fired = nl() > before;
    const m = (((await api('GET', '/live/state')).json || {}).last_match || {});
    matched = m.gloss ? m.gloss + (m.accepted ? ' accepted' : ' rejected') : matched;
    if (!fired) await sleep(250);
  }
  check('immediate use: the daemon matched the new sign', /ENTER accepted/.test(matched), matched);
  check('immediate use: the new sign pressed Enter in the real editor', fired, 'editor newlines ' + before + ' -> ' + nl());
  await sleep(500);
  await shotTo(page, OUT, '04-live');
  await page.click('#lv-x'); await sleep(700);
  // --- Type text tile ---
  await teach(null);
  await page.click('.qa-tile[data-id="text"]'); await sleep(500);
  check('Type text reveals one field', await page.eval(`!document.querySelector('#qa-text').hidden`));
  await page.focus('#qa-text-in'); await page.type('hello-from-quick'); await sleep(400);
  await shotTo(page, OUT, '05-type-text');
  check('typing enables Record and names the sign', await page.eval(`!document.querySelector('#tw-name-next').disabled && /hello-from-quick/i.test(document.querySelector('#tw-name').value)`), await page.eval(`document.querySelector('#tw-name').value`));
  await page.click('.qa-tile[data-id="alttab"]'); await sleep(400);
  check('another tile replaces the choice and hides the text field', await page.eval(`document.querySelector('#qa-text').hidden && document.querySelector('.qa-tile[data-id="alttab"]').getAttribute('aria-pressed') === 'true'`));
  await page.click('.qa-tile[data-id="app"]'); await sleep(900);
  check('Open app opens the picker with the laptop\'s real icons', await page.eval(`!document.querySelector('#sheet-actions').hidden && document.querySelectorAll('#sheet-actions .act-app img').length >= 3`));
  await shotTo(page, OUT, '06-open-app');
  await page.click('#act-x'); await sleep(500);
  // responsive
  for (const [w, h, name] of [[360, 640, '07-360'], [768, 1024, '08-768'], [1280, 800, '09-1280']]) {
    await page.viewport(w, h, { dpr: 1, mobile: w < 700 }); await sleep(600);
    await shotTo(page, OUT, name);
    check('no horizontal scroll at ' + w + 'px', await page.eval(`document.documentElement.scrollWidth <= ${w}`), '');
  }
  const errs = page.errors.filter(e => !/favicon|ServiceWorker|manifest/i.test(e));
  check('no page errors', errs.length === 0, errs.slice(0, 3).join(' | '));
} catch (e) {
  check('flow completed', false, String(e && e.message || e).slice(0, 300));
} finally {
  if (rec) { const r = await rec.stop('quick-flow.mp4'); check('screen recording (mp4)', r.ok, r.frames + ' frames'); }
  await page.close();
}
finish();
