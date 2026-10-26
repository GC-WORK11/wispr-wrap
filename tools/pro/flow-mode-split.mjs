// flow-mode-split.mjs — real-run proof for the mode split (PRO lane):
// a sign bound to a laptop action TYPES ITS LABEL in Text mode and FIRES
// the action in Control mode. Real daemon, real Chrome @390x844, real X11.
//   SITE_ENV=<env> node --experimental-websocket tools/pro/flow-mode-split.mjs
// Bank requirement: MY-SWITCH exists with action {kind:builtin,tool:win.alt-tab}


import path from 'node:path';
import { stack, newPage, loadClips, playAndWait, shotTo, reporter, daemon, sleep, root } from '../site/lib.mjs';
import { execFileSync as sh } from 'node:child_process';
import fs from 'node:fs';

const S = stack(process.env.SITE_ENV || '/tmp/wf-rr-pro/env');
const OUT = path.join(root, 'proofs/ui-v2/mode-split');
const { check, finish } = reporter(S, OUT, 'mode split — text types label, control fires action');
const api = daemon(S);
const sink = () => fs.readFileSync(S.SINK, 'utf8');
const activeWin = () => sh('xdotool', ['getactivewindow', 'getwindowname'], { env: { ...process.env, DISPLAY: S.D } }).toString().trim();

const page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9071) });
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
  await loadClips(page, ['palm-hold', 'perform-TYPE-HELLO', 'namaste', 'idle']);
  await page.click('#tray-live');
  await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
  await sleep(600);
  await shotTo(page, OUT, '01-live-ready');

  // ---------------- TEXT MODE: sign types its label -----------------
  await page.click('#lv-m-text'); await sleep(400);
  await shotTo(page, OUT, '02-text-mode');
  check('text mode selected in the UI', await page.eval(`document.querySelector('#lv-m-text').getAttribute('aria-selected') === 'true' || document.querySelector('#lv-view').className.includes('mode-text')`), '');
  sh('xdotool', ['windowfocus', S.TERM_WID], { env: { ...process.env, DISPLAY: S.D } }); await sleep(300);
  const beforeText = sink().length;
  await playAndWait(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await playAndWait(page, 'perform-TYPE-HELLO');
  await sleep(1200);
  await shotTo(page, OUT, '03-text-typed');
  check('text mode typed the LABEL (type hello site), not the action text',
        /type hello site/i.test(sink().slice(beforeText)) && !/hello-from-site/i.test(sink().slice(beforeText)),
        sink().slice(beforeText).slice(-60));

  // stop listening before the control-mode leg
  await playAndWait(page, 'namaste'); await sleep(500);

  // ---------------- CONTROL MODE: sign fires the action -------------
  await page.click('#lv-m-ctl'); await sleep(400);
  check('control mode selected in the UI', await page.eval(`document.querySelector('#lv-m-ctl').getAttribute('aria-selected') === 'true' || document.querySelector('#lv-view').className.includes('mode-control')`), '');
  sh('xdotool', ['windowfocus', S.TERM_WID], { env: { ...process.env, DISPLAY: S.D } }); await sleep(300);
  const beforeCtl = sink().length;
  await playAndWait(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await playAndWait(page, 'perform-TYPE-HELLO');
  await sleep(1500);
  await shotTo(page, OUT, '04-control-fired');
  check('control mode fired the bound ACTION (typed hello-from-site)', /hello-from-site/i.test(sink().slice(beforeCtl)), sink().slice(beforeCtl).slice(-60));
  check('and typed the action text, not the label', !/type hello site/i.test(sink().slice(beforeCtl)), 'no label text');
} finally {
  await page.close().catch(() => {});
}
finish();
