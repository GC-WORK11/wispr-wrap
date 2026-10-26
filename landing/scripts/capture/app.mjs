// app.mjs - landing-page captures of the real app: phone screenshots + recordings (Teach, Send, Home) and
// laptop grabs, against the isolated realrun stack (real daemon, real Chrome, real Xvfb/xfwm4/editor).
// Substituted: only the hand keypoints (synthetic clips from tools/demo/clips, coordinates only) and the
// fake camera device (neutral clip). Env: CAP_ENV (state file), CAP_CDP (Chrome port), CAP_OUT, CAP_WORK, SITE_FAKE_CAM.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { stack, newPage, loadClips, playAndWait, sleep, daemon, root } from '../../../tools/site/lib.mjs';
import { recorder } from './rec.mjs';

const OUT = process.env.CAP_OUT || path.join(root, 'landing/public/media/app');
const WORK = process.env.CAP_WORK || '/tmp/agy-cap';
const S = stack(process.env.CAP_ENV || '/tmp/wf-realrun-wispr-wrap-agy-landing/env');
const { api, sh, sink, sinkHas } = daemon(S);
fs.mkdirSync(OUT, { recursive: true }); fs.mkdirSync(WORK, { recursive: true });
const log = (...a) => console.log(...a);

// ---- stills ----
const ff = (args) => { const r = spawnSync('ffmpeg', ['-y', '-loglevel', 'error', ...args], { encoding: 'utf8' }); if (r.status) throw new Error(r.stderr); };
const webp = (png, name, vf = []) => ff(['-i', png, ...(vf.length ? ['-vf', vf.join(',')] : []), '-c:v', 'libwebp', '-quality', '85', '-compression_level', '6', path.join(OUT, name)]);
let page;
// phone shot: 390x844 @2x -> 780x1688 WebP; also report any visible 'Wispr' text or a visible placeholder
async function shot(name) {
  const png = path.join(WORK, name + '.png');
  await page.shot(png);
  webp(png, name + '.webp');
  const bad = await page.eval(`(() => { const out = [];
    const vis = (e) => { const r = e.getBoundingClientRect(); const c = getComputedStyle(e);
      return r.width > 2 && r.height > 2 && c.visibility !== 'hidden' && c.display !== 'none' && +c.opacity > 0.05 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
    for (const e of document.querySelectorAll('body *')) { if (!vis(e)) continue;
      for (const n of e.childNodes) if (n.nodeType === 3 && /wispr/i.test(n.nodeValue)) out.push('text:' + n.nodeValue.trim().slice(0, 40)); }
    const b = document.querySelector('#box'); if (b && vis(b) && !b.value && /wispr/i.test(b.placeholder)) out.push('placeholder');
    return out; })()`);
  log('shot', name, bad.length ? 'WARN visible: ' + bad.join(' | ') : 'ok');
}
// laptop grab: the real X display, cropped to the editor window's client area so no title bar
// (xfwm4 titles carry the stack name) is visible; pixels are untouched
function grab(name) {
  const png = path.join(WORK, name + '.png');
  ff(['-f', 'x11grab', '-draw_mouse', '0', '-video_size', '1280x800', '-i', S.D, '-frames:v', '1', png]);
  fs.copyFileSync(png, path.join(WORK, name + '-full.png'));
  const g = spawnSync('xwininfo', ['-id', S.TERM_WID], { env: { ...process.env, DISPLAY: S.D }, encoding: 'utf8' }).stdout;
  const v = (k) => +(g.match(new RegExp(k + ':\\s+(-?\\d+)')) || [])[1];
  const [x, y, w, h] = [v('Absolute upper-left X'), v('Absolute upper-left Y'), v('Width'), v('Height')];
  webp(png, name + '.webp', [`crop=${w}:${h}:${x}:${y}`]);
  log('grab', name, `${w}x${h}`);
}
const focusEditor = async () => { sh('xdotool', ['windowraise', S.TERM_WID]); sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(350); };
const clearEditor = async () => { await focusEditor(); sh('xdotool', ['key', 'ctrl+slash']); sh('xdotool', ['key', 'BackSpace']); await sleep(300); };
const toast = () => page.eval(`document.querySelector('#toast').textContent`);
const sent = (what) => page.waitFor(`/Sent/.test(document.querySelector('#toast').textContent)`, 15000, what);
const openPage = async () => {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
  await page.waitFor(`getComputedStyle(document.querySelector('#boot')).visibility === 'hidden' || !document.querySelector('#boot')`, 20000, 'boot done');
  await sleep(4500);   // lotus fully bloomed
};

// ---- inputs the phone sends (generated here, no people) ----
const SEND = '/tmp/agy-send'; fs.mkdirSync(SEND, { recursive: true });
fs.copyFileSync(path.join(root, 'web/icon-512.png'), SEND + '/photo.png');
fs.writeFileSync(SEND + '/notes.txt', 'Meeting notes\nSend the draft on Friday\n');

try {
  // fresh empty bank: every proof sign reuses the same synthetic motion
  for (const row of (((await api('GET', '/bank')).json || {}).signs || [])) await api('DELETE', '/bank?gloss=' + encodeURIComponent(row.gloss));
  page = await newPage(S, { cdpPort: Number(process.env.CAP_CDP || 9316) });
  await openPage();
  await loadClips(page, ['teach-TYPE-HELLO', 'palm-hold', 'perform-TYPE-HELLO', 'idle']);

  // ---------- home ----------
  await page.focus('#box'); await page.type('Draft the reply for Monday');
  await sleep(500); await shot('home');
  await page.eval(`(() => { const b = document.querySelector('#box'); b.value = ''; b.dispatchEvent(new Event('input', { bubbles: true })); })()`);

  // ---------- teach ----------
  const rec = recorder(page, path.join(WORK, '_rec-teach'));
  await rec.start(); await sleep(600);
  await page.eval(`document.querySelector('#tray-teach').click()`);   // the drawer carries the old product name; open Teach directly
  await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 10000, 'grid rendered');
  await sleep(900);
  await page.click('.qa-tile[data-id="type.text"]'); await sleep(600);
  await page.focus('#qa-text-in'); await page.type('hello', { delay: 90 }); await sleep(700);
  await shot('teach-1-pick');
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 8000, 'record');
  await sleep(900);
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length === 2`, 40000, 'take 2');
  await sleep(250); await shot('teach-2-take');
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 40000, 'done');
  await sleep(1100); await shot('teach-3-ready');
  await page.click('#tw-done-live');
  rec.skip(true);   // camera + model load is dead time
  await page.waitFor(`document.querySelector('#lv-cover').hidden`, 60000, 'camera ready');
  await sleep(500); rec.skip(false);
  await focusEditor();
  await playAndWait(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await sleep(500);
  const before = sink().length;
  await playAndWait(page, 'perform-TYPE-HELLO');
  const fired = await sinkHas(before, 'hello', 12000);
  log('teach sign fired:', fired, 'caption:', await page.eval(`document.querySelector('#lv-act-t').textContent`));
  await sleep(900); await shot('teach-4-live');
  await focusEditor(); grab('laptop-teach');
  await sleep(700);
  log('teach-flow', JSON.stringify(await rec.stop(path.join(OUT, 'teach-flow.mp4'))));
  await page.click('#lv-x'); await sleep(700);
  await clearEditor();

  // ---------- send ----------
  await openPage();
  const rec2 = recorder(page, path.join(WORK, '_rec-send'));
  await rec2.start(); await sleep(500);
  await focusEditor();
  await page.focus('#box'); await page.type('hello from the phone', { delay: 70 });
  await sleep(600); await shot('send-1-text');
  let b = sink().length;
  await page.click('#go'); await sent('text sent');
  log('text arrived:', await sinkHas(b, 'hello from the phone', 6000));
  await sleep(500); grab('laptop-send'); await sleep(900);
  for (const [tag, input, f] of [['photo', '#file', SEND + '/photo.png'], ['file', '#fileany', SEND + '/notes.txt']]) {
    await page.eval(`document.querySelector('#toast').textContent = ''`);
    await page.setFiles(input, [f]);
    await page.waitFor(`!document.querySelector('#attach').hidden`, 5000, tag + ' staged');
    await sleep(800); await shot(`send-${tag === 'photo' ? 2 : 3}-${tag}`);
    await focusEditor(); b = sink().length;
    await page.click('#go'); await sent(tag + ' sent');
    await sleep(900);
    log(tag, 'editor got:', JSON.stringify(sink().slice(b).replace(/[^\x20-\x7e\n]/g, '?').slice(0, 120)).replace(/\/tmp\/[^"]*\//g, '<path>/'));
    if (tag === 'file') { await focusEditor(); grab('laptop-send-file'); }
    await sleep(1200);
  }
  log('send-flow', JSON.stringify(await rec2.stop(path.join(OUT, 'send-flow.mp4'))));
  await page.click('#box'); await clearEditor();
  const errs = page.errors.filter((e) => !/favicon|ServiceWorker|manifest/i.test(e));
  log('page errors:', errs.length, errs.slice(0, 2).join(' | '));
} catch (e) {
  console.error('capture failed:', String(e && e.message || e).slice(0, 400)); process.exitCode = 1;
} finally {
  if (page) await page.close();
}
