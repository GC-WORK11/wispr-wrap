// flow-offline.mjs — laptop actions disappear when no laptop is connected (the UI spec §3), real run.
// Teach is open with the one-tap grid; the laptop's daemon is then stopped for real. Within a few seconds the
// grid is replaced by one line and one button ("Connect"), the Record button is gone, and the drawer no longer
// offers Teach a sign / My signs. usage: SITE_ENV=/tmp/wf-site-overlay/env node --experimental-websocket tools/site/flow-offline.mjs
import fs from 'node:fs';
import path from 'node:path';
import { stack, newPage, sleep, shotTo, reporter, root, visibleText } from './lib.mjs';
const OUT = path.join(root, 'proofs/ui-v2/site-offline');
const S = stack(process.env.SITE_ENV || '/tmp/wf-site-overlay/env');
const { check, finish } = reporter(S, OUT, 'Offline gating - overlay daemon stopped for real');
const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9016) });
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await page.click('#menu'); await sleep(600);
  check('connected: the drawer offers Teach a sign and My signs', await page.eval(`!document.querySelector('#tray-teach').hidden && !document.querySelector('#tray-signs').hidden`));
  await page.click('#tray-teach'); await sleep(700);
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 12000, 'grid');
  check('connected: the one-tap grid is there with a Record button', await page.eval(`!document.querySelector('#qa-grid').hidden && !document.querySelector('#tw-name-foot').hidden && document.querySelector('#qa-off').hidden`));
  await sleep(500); await shotTo(page, OUT, '01-connected');
  // stop the laptop's daemon for real
  const pidFile = path.join(path.dirname(process.env.SITE_ENV || '/tmp/wf-site-overlay/env'), 'pid');
  const pid = Number(fs.readFileSync(pidFile, 'utf8').trim());
  process.kill(pid, 'SIGTERM');
  await page.waitFor(`!document.querySelector('#qa-off').hidden`, 20000, 'offline state shown');
  await sleep(700);
  const st = await page.eval(`({ grid: !document.querySelector('#qa-grid').hidden, foot: !document.querySelector('#tw-name-foot').hidden,
     icon: !!document.querySelector('.qa-off-ic'), btn: document.querySelector('#qa-connect').textContent })`);
  check('laptop gone: the grid and the Record button are hidden', !st.grid && !st.foot, JSON.stringify(st));
  const vt = await visibleText(page, '#sheet-teach');
  check('laptop gone: a laptop icon and one Connect button, no sentence', st.icon && vt.length === 1 && vt[0] === 'Connect', vt.join(' | '));
  await shotTo(page, OUT, '02-no-laptop');
  await page.click('#tw-x'); await sleep(700);
  await page.click('#menu'); await sleep(700);
  check('laptop gone: the drawer hides Teach a sign and My signs, offers Connect', await page.eval(`document.querySelector('#tray-teach').hidden && document.querySelector('#tray-signs').hidden && !document.querySelector('#dr-connect').hidden`));
  await shotTo(page, OUT, '03-drawer-no-laptop');
  const errs = page.errors.filter(e => !/favicon|ServiceWorker|manifest|Failed to fetch|NetworkError|ERR_CONNECTION/i.test(e));
  check('no page errors', errs.length === 0, errs.slice(0, 3).join(' | '));
} catch (e) {
  check('flow completed', false, String(e && e.message || e).slice(0, 300));
} finally { await page.close(); }
finish();
