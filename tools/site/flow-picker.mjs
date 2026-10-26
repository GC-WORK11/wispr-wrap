// flow-picker.mjs — real-run proof for the @ picker v3 on the plain daemon :9010.
// usage: node --experimental-websocket tools/site/flow-picker.mjs
//
// Opens the real picker through import('/js/actions.js'), proves real app icons
// (naturalWidth>0 from /act/icons), the Suggested/Apps/Actions/Write-your-own
// structure, that picking Chrome resolves {tool:'app.open', args:{id:'google-chrome'}},
// the keyboard path, and the Write-your-own previews. Screenshots at 390x844@2x
// plus 360x640 / 768x1024 / 1280x800. Closes Chrome at the end.
import path from 'node:path';
import { stack, newPage, sleep, shotTo, reporter, root, visibleText, isLabel } from './lib.mjs';

const OUT = path.join(root, 'proofs/ui-v2/site-picker');
const S = stack();
const { check, finish } = reporter(S, OUT, '@ picker v3 — plain daemon');

const openPicker = async (page, query) => {
  await page.eval(`import('/js/actions.js').then(m => { window.__A = m.Actions; window.__pick = m.Actions.pick(${JSON.stringify({ forGloss: 'OPEN-BROWSER', query: query || '' })}); })`);
  await page.waitFor(`!document.querySelector('#sheet-actions').hidden && document.querySelectorAll('#act-list .act-app').length > 0`, 8000, 'picker open + apps');
  await sleep(700); // let icon blobs load
};

const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9017) });
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });

  /* ---- default view ---- */
  await openPicker(page);
  await sleep(500);
  await shotTo(page, OUT, '01-default');

  // real <img> icons with naturalWidth > 0
  const iconStats = await page.eval(`(() => {
    const imgs = [...document.querySelectorAll('#act-list .act-ic img')];
    return { total: imgs.length, real: imgs.filter(i => i.naturalWidth > 0).length };
  })()`);
  check('>= 5 app icons are real <img> with naturalWidth>0', iconStats.real >= 5,
    `${iconStats.real}/${iconStats.total} loaded`);

  // structure: Suggested + Apps + Actions + Write your own
  const sects = await page.eval(`[...document.querySelectorAll('#act-list .act-sect')].map(s => s.textContent.trim())`);
  check('no section headings: space separates, icons explain', sects.length >= 1 && sects.every(t => t === ''), sects.join('|'));
  check('apps are an icon grid, actions a flat list, and Write your own is there',
    await page.eval(`!!document.querySelector('#act-list .act-grid') && !!document.querySelector('#act-list .act-row') && !!document.querySelector('#act-list .act-own')`), '');
  const vt = await visibleText(page, '#sheet-actions');
  check('only labels on screen (no headings, helper lines or sentences)', vt.every(isLabel), vt.filter(t => !isLabel(t)).join(' | ') || vt.length + ' labels');

  // noise hidden (no Settings / Preferences / Bulk Rename / background helpers)
  const appNames = await page.eval(`[...document.querySelectorAll('#act-list .act-app-nm')].map(n => n.textContent)`);
  const noisy = appNames.filter(n => /preferences|tweaks|bulk rename|settings|picom|plank|x11vnc|okta/i.test(n));
  check('noise hidden in default view', noisy.length === 0, 'leaked: ' + noisy.join(', '));

  /* ---- search "chrome" ---- */
  await page.focus('#act-q');
  await page.eval(`(() => { const q=document.querySelector('#act-q'); q.value='chrome'; q.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(700);
  await shotTo(page, OUT, '02-search');

  // picking Chrome resolves to app.open google-chrome
  const chromeAction = await page.eval(`(() => {
    const cell = [...document.querySelectorAll('#act-list .act-app')]
      .find(c => /chrome/i.test((c.querySelector('.act-app-nm')||{}).textContent||''));
    if (!cell) return null; cell.click(); return window.__pick;
  })()`);
  check('picking Chrome resolves app.open google-chrome',
    chromeAction && chromeAction.tool === 'app.open' && chromeAction.args && chromeAction.args.id === 'google-chrome',
    JSON.stringify(chromeAction));

  /* ---- Actions section ---- */
  await openPicker(page, '');
  await page.eval(`(() => { const q=document.querySelector('#act-q'); q.value='screenshot'; q.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(500);
  await shotTo(page, OUT, '03-actions');
  const hasScreenshot = await page.eval(`[...document.querySelectorAll('#act-list .act-row .act-name')].some(n => /screenshot/i.test(n.textContent))`);
  check('Actions list shows the screenshot action', hasScreenshot, '');

  /* ---- apps-only (what Teach's Open app tile uses): just the laptop's apps ---- */
  await page.click('#act-x'); await sleep(500);
  await page.eval(`import('/js/actions.js').then(m => { window.__p2 = m.Actions.pick({ apps: true }); })`);
  await page.waitFor(`!document.querySelector('#sheet-actions').hidden && document.querySelectorAll('#act-list .act-app').length > 0`, 8000, 'apps-only open');
  await sleep(800);
  check('apps-only: icons only, no actions list, no Write your own',
    await page.eval(`document.querySelectorAll('#act-list .act-app').length >= 5 && !document.querySelector('#act-list .act-row') && !document.querySelector('#act-list .act-own')`), '');
  const vt2 = await visibleText(page, '#sheet-actions');
  check('apps-only: the only text is app names and Search', vt2.every(isLabel), vt2.join(' | ').slice(0, 160));
  await shotTo(page, OUT, '03b-apps-only');
  await page.click('#act-x'); await sleep(500);
  await openPicker(page, '');

  /* ---- Write your own: previews ---- */
  await page.eval(`(() => { const q=document.querySelector('#act-q'); q.value=''; q.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await sleep(400);
  const typeOwn = async (val) => {
    await page.eval(`(() => { const i=document.querySelector('.act-own-in'); i.value=${JSON.stringify(val)}; i.dispatchEvent(new Event('input',{bubbles:true})); })()`);
    await sleep(250);
    await page.eval(`document.querySelector('.act-own').scrollIntoView({ block: 'center' })`);
    await sleep(300);
    return page.eval(`(() => { const p=document.querySelector('.act-own-prev'); return { text:p.textContent, show:p.classList.contains('show'), err:p.classList.contains('err') }; })()`);
  };
  const pOpen = await typeOwn('open https://example.org');
  check('preview for "open https://example.org"', pOpen.show && /example\.org/.test(pOpen.text) && !pOpen.err, pOpen.text);
  await shotTo(page, OUT, '04-own-open');

  const pPress = await typeOwn('press ctrl+shift+t');
  check('preview for "press ctrl+shift+t"', pPress.show && /ctrl\+shift\+t/i.test(pPress.text) && !pPress.err, pPress.text);
  await shotTo(page, OUT, '05-own-press');

  // "Open this" one-tap = press Enter
  const openThis = await page.eval(`(() => {
    const b=document.querySelector('.act-open-this'); if(!b) return null; b.click(); return window.__pick;
  })()`);
  check('"Open this" resolves to key.press Return',
    openThis && openThis.tool === 'key.press' && openThis.args && openThis.args.keys === 'Return',
    JSON.stringify(openThis));
  await shotTo(page, OUT, '06-own-openthis');

  /* ---- error line ---- */
  await openPicker(page, '');
  const pErr = await typeOwn('frobnicate the thing');
  check('honest error for a bad verb', pErr.err && /open, type, press or run/i.test(pErr.text), pErr.text);

  /* ---- keyboard path ---- */
  await openPicker(page, '');
  await page.focus('#act-q');
  await page.key('ArrowDown', { code: 'ArrowDown', vk: 40 });
  await sleep(150);
  const kbSel = await page.eval(`!!document.querySelector('#act-list .act-sel')`);
  check('keyboard path: ArrowDown highlights a row', kbSel, '');
  await page.esc();
  await sleep(200);
  const closed = await page.eval(`document.querySelector('#sheet-actions').hidden`);
  check('Esc closes the picker', closed, '');

  /* ---- iconFor + appOf public API ---- */
  const iconApi = await page.eval(`(async () => {
    const A = window.__A; await A.catalog();
    const act = { kind:'app', tool:'app.open', args:{ id:'google-chrome' }, id:'google-chrome' };
    const app = A.appOf(act); const url = await A.iconFor(act);
    return { appName: app && app.name, isBlob: typeof url === 'string' && url.startsWith('blob:') };
  })()`);
  check('Actions.appOf resolves Chrome', iconApi.appName === 'Google Chrome', iconApi.appName || 'null');
  check('Actions.iconFor returns a blob url', iconApi.isBlob, '');

  /* ---- responsive screenshots ---- */
  for (const [w, h, mob, dpr, tag] of [[360, 640, true, 2, '07-360'], [768, 1024, false, 2, '08-768'], [1280, 800, false, 2, '09-1280']]) {
    await page.viewport(w, h, { mobile: mob, dpr });
    await openPicker(page, '');
    await sleep(600);
    await shotTo(page, OUT, tag);
    const hs = await page.eval(`document.documentElement.scrollWidth <= ${w} + 1`);
    check('no horizontal scroll at ' + w + 'px', hs, 'scrollWidth=' + await page.eval(`document.documentElement.scrollWidth`));
  }

  /* ---- no page errors ---- */
  const errs = page.errors.filter(e => !/favicon|ServiceWorker|manifest/i.test(e));
  check('no page errors', errs.length === 0, errs.slice(0, 3).join(' | '));
} catch (e) {
  check('flow completed', false, String(e && e.message || e));
} finally {
  await page.close();
}
finish();
