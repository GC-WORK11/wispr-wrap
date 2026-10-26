// record.mjs — a short REAL screen recording (CDP screenshots at ~15 fps -> mp4) of the home screen:
// refresh -> loading screen blooms -> ASCII lotus replays its sprout -> type -> send -> pollen burst.
// usage: node --experimental-websocket tools/peak/record.mjs <out.mp4> [cdp-port]   (local proof, never committed)
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';
const out = process.argv[2] || 'proofs/ui-v2/home-bloom-send.mp4';
const S = loadStack();
fs.rmSync('/tmp/peak-chrome-rec', { recursive: true, force: true });
const page = await launchChrome({ port: Number(process.argv[3] || 8951), profile: '/tmp/peak-chrome-rec', origin: S.base });
await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
await page.viewport(390, 844, { mobile: true, dpr: 1 });
const dir = '/tmp/peak-rec-frames'; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
let n = 0, run = true; const t0 = Date.now();
const grab = (async () => { while (run) { try { const { data } = await page.send('Page.captureScreenshot', { format: 'jpeg', quality: 80, fromSurface: true }); fs.writeFileSync(`${dir}/${String(n++).padStart(4, '0')}.jpg`, Buffer.from(data, 'base64')); } catch (e) { await sleep(30); } } })();
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 0 });          // a fresh load = a refresh
  await sleep(4200);
  await page.focus('#box'); await page.type('Ship it'); await sleep(500);
  await page.click('#go'); await sleep(2600);
  await page.send('Page.reload', { ignoreCache: false }); await sleep(3800);            // and once more: a real refresh replays everything
} finally { run = false; await grab; }
const secs = (Date.now() - t0) / 1000, fps = Math.max(5, Math.round(n / secs));
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', String(fps), '-i', `${dir}/%04d.jpg`, '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '24', out]);
console.log(`recorded ${n} frames in ${secs.toFixed(1)}s (~${fps} fps) -> ${out}`, 'errors:', JSON.stringify(page.errors.slice(0, 2)));
await page.close();
fs.rmSync(dir, { recursive: true, force: true });
