// tools/pro/record-clips.mjs — record REAL app flows at 390x844 as muted loops
// for the landing site (mp4 + webm + poster each). Only the hand detections are
// substituted (kp42 clips through the app's own WFFakeTrack test seam); the UI,
// daemon endpointer/bank/act layer and X11 display are all real.
//
// Usage: SITE_ENV=/tmp/wf-pro-shot/env SITE_FAKE_CAM=/tmp/neutral-cam.y4m \
//        node tools/pro/record-clips.mjs <out-dir>
import { stack, newPage, loadClips, sleep } from './lib.mjs';
import { execFileSync, execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.argv[2] || '/tmp/clips';
fs.mkdirSync(OUT, { recursive: true });
const S = stack();
const page = await newPage(S, { cdpPort: 9465 });

/* ---- screencast plumbing ---- */
let frames = null;
page.on('Page.screencastFrame', async p => {
  if (!frames) return;
  frames.push(p.data);
  try { await page.send('Page.screencastFrameAck', { sessionId: p.sessionId }); } catch (e) {}
});
const startRec = async () => {
  frames = [];
  await page.send('Page.startScreencast', { format: 'jpeg', quality: 84, maxWidth: 390, maxHeight: 844, everyNthFrame: 1 });
};
const stopRec = async name => {
  await page.send('Page.stopScreencast').catch(() => {});
  await sleep(300);
  const got = frames || []; frames = null;
  if (!got.length) { console.log('EMPTY ' + name); return; }
  const dir = path.join(OUT, '.f-' + name); fs.mkdirSync(dir, { recursive: true });
  got.forEach((d, i) => fs.writeFileSync(path.join(dir, String(i).padStart(5, '0') + '.jpg'), Buffer.from(d, 'base64')));
  const enc = (args, out) => execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', '30', '-i', dir + '/%05d.jpg', ...args, out]);
  enc(['-vf', 'scale=390:844:flags=lanczos', '-an', '-c:v', 'libx264', '-preset', 'slow', '-crf', '26', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'], path.join(OUT, name + '.mp4'));
  enc(['-vf', 'scale=390:844:flags=lanczos', '-an', '-c:v', 'libvpx-vp9', '-crf', '35', '-b:v', '0', '-row-mt', '1'], path.join(OUT, name + '.webm'));
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', '30', '-i', dir + '/%05d.jpg', '-frames:v', '1', '-q:v', '4', path.join(OUT, name + '-poster.png')]);
  execSync(`rm -rf '${dir}'`);
  console.log(`${name}: ${got.length} frames ${(got.length / 30).toFixed(1)}s`);
};

const play = async (clip, pad = 900) => {
  await page.eval(`window.__play(${JSON.stringify(clip)})`);
  await sleep(300);
  await page.waitFor('window.__idle()', 30000, 'clip ' + clip);
  await sleep(pad);
};
const ev = js => page.eval(js);

await page.goto(`${S.base}/?k=${S.KEY}`);
await sleep(1800);
await loadClips(page, ['teach-OPEN-NOTES', 'perform-OPEN-NOTES']);

/* ---------- send: composer -> send -> toast ---------- */
await startRec();
await ev(`document.querySelector('#box').focus()`);
await page.type('Good morning', { delay: 55 });
await sleep(400);
await page.click('#go');
await sleep(2600);
await stopRec('send');

/* ---------- teach-pick: open wizard -> name it -> Record it ---------- */
await ev(`location.hash='#teach'`);
await sleep(1200);
await startRec();
await ev(`document.querySelector('#tw-name').focus()`);
await page.type('say morning', { delay: 55 });
await sleep(1100);
await page.click('#tw-name-next');
await sleep(1600);
await stopRec('teach-pick');

/* ---------- teach-take: three takes, take bar fills ---------- */
await startRec();
for (let t = 0; t < 3; t++) {
  await play('teach-OPEN-NOTES', 300);
  await sleep(1800);
}
await page.waitFor(`document.querySelector('.tw-step[data-step="bind"]').classList.contains('on')`, 20000, 'bind step');
await sleep(700);
await stopRec('teach-take');

/* ---------- teach-done: @ picker -> type text -> Finish -> live card ---------- */
await startRec();
await page.click('#tw-pick');
await sleep(1500);
await ev(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));if(r)r.querySelector('.act-input')?.focus()})()`);
await sleep(400);
await ev(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));const i=r&&r.querySelector('.act-input');if(i){i.value='Good morning';i.dispatchEvent(new Event('input',{bubbles:true}))}})()`);
await sleep(500);
await ev(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));r&&r.querySelector('.act-use')?.click()})()`);
await sleep(900);
await page.click('#tw-save');
await sleep(2400);
await stopRec('teach-done');

/* ---------- live: Go Live -> perform -> match chip + toast ---------- */
await ev(`location.hash='#live'`);
await sleep(1600);
await startRec();
await sleep(600);
await play('perform-OPEN-NOTES', 1800);
await sleep(1500);
await stopRec('live');

console.log('done -> ' + OUT);
await page.close();
process.exit(0);
