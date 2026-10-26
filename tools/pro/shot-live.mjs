// shot-live.mjs — Go Live proof shots at 390x844 on THIS build:
//  1) golive-secure-card: plain-http LAN origin -> camera card with ONE primary
//     action button, no helper paragraph (the secure-context cut).
//  2) golive-390: Go Live open and listening on localhost fake cam — no
//     'Demo input' tag element exists anymore.
// usage: SITE_ENV=/tmp/wf-pro-shot/env SITE_FAKE_CAM=<y4m> OUT=<dir> node --experimental-websocket tools/pro/shot-live.mjs
import { stack, newPage, loadClips, playAndWait, shotTo, sleep, daemon } from './lib.mjs';
import { launchChrome } from '../peak/cdp.mjs';
import fs from 'node:fs';
import path from 'node:path';

const S = stack();
const OUT = process.env.OUT || path.join(process.cwd(), 'proofs/ui-v2/golive-390');
fs.mkdirSync(OUT, { recursive: true });
const { api } = daemon(S);

const lan = 'http://' + (await api('GET', '/pair-info')).json.ip + ':' + S.PORT;

// 1) insecure LAN origin — secure-context failure shows the one-button card
const lanPage = await launchChrome({
  port: Number(process.env.SITE_CDP2 || 9819), profile: '/tmp/pro-shot-lan',
  origin: lan,
  extra: process.env.SITE_FAKE_CAM ? ['--use-file-for-fake-video-capture=' + process.env.SITE_FAKE_CAM] : [],
});
await lanPage.viewport(390, 844, { mobile: true, dpr: 2 });
await lanPage.goto(lan + '/?k=' + S.KEY, { settle: 2500 });
await lanPage.eval(`location.hash = '#live'`); await sleep(2500);
await lanPage.waitFor(`!document.querySelector('#lv-cover').hidden`, 20000, 'camera card up');
await sleep(700);
await shotTo(lanPage, OUT, 'golive-secure-card');
const cardTxt = await lanPage.eval(`document.querySelector('#lv-card-m').textContent`);
const nBtns = await lanPage.eval(`document.querySelectorAll('#lv-card-b button').length`);
console.log(`card msg=${JSON.stringify(cardTxt)} buttons=${nBtns}`);
await lanPage.close();

const page = await newPage(S);

// 2) localhost — fake cam opens, Go Live ready, no lv-tag in the DOM
await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
await loadClips(page, ['palm-hold', 'perform-TYPE-HELLO', 'idle']);
await page.eval(`location.hash = '#live'`); await sleep(2500);
await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
await sleep(900);
const hasTag = await page.eval(`!!document.querySelector('#lv-tag')`);
console.log('lv-tag element present:', hasTag);
await shotTo(page, OUT, 'golive-390');

console.log('shots in ' + OUT);
process.exit(0);
