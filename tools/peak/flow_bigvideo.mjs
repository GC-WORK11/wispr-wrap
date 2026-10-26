// flow_bigvideo — a ~60 MB video through the REAL phone UI (+ menu ->
// setFiles -> send), proving it lands in ~/wf-uploads byte-identical and
// its path is pasted into the focused SinkEdit. Separate file so it does
// not touch flows.mjs. Patterns copied from flows.mjs (newPage/shot/sh).
//
// usage: node --experimental-websocket tools/peak/flow_bigvideo.mjs <outdir> [cdp-port]
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || '/tmp/be-rr-proofs/bigvideo';
const cdpPort = Number(process.argv[3] || 8971);
const S = loadStack();
fs.mkdirSync(out, { recursive: true });
const results = [];
const check = (name, ok, detail = '') => { results.push({ ok: !!ok }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  — ' + detail : '')); };
const sh = (cmd, args) => execFileSync(cmd, args, { env: { ...process.env, DISPLAY: S.D }, encoding: 'utf8' });
const sha256 = f => crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');

async function newPage() {
  const profile = '/tmp/peak-chrome-bigvideo';
  fs.rmSync(profile, { recursive: true, force: true });
  const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
  await page.viewport(390, 844, { mobile: true, dpr: 2 });
  return page;
}

async function main() {
  // generate a ~60 MB video with ffmpeg testsrc2 (no people)
  const dirTmp = '/tmp/peak-bigvideo'; fs.mkdirSync(dirTmp, { recursive: true });
  const vid = dirTmp + '/big.mp4';
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=1280x720:d=40', '-c:v', 'libx264', '-b:v', '12M', '-pix_fmt', 'yuv420p', vid]);
  const mb = (fs.statSync(vid).size / 1048576).toFixed(1);
  const srcSha = sha256(vid);
  check('generated test video ~60 MB', fs.statSync(vid).size > 40 * 1048576, mb + ' MB, sha ' + srcSha.slice(0, 12));

  const page = await newPage();
  try {
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3000 });
    sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(300);
    // attach the video through the real file input (the + menu's video path)
    await page.setFiles('#file', [vid]);
    await page.waitFor(`!document.querySelector('#attach').hidden`, 5000, 'video staged');
    await sleep(400);
    await page.shot(path.join(out, '01-staged.png'));
    sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(200);
    const beforeList = fs.readdirSync(S.WFHOME + '/wf-uploads');
    await page.click('#go');
    // wait for the upload to finish and a success toast to show
    await page.waitFor(`/→/.test(document.querySelector('#toast').textContent)`, 60000, 'video delivered');
    await sleep(1500);
    await page.shot(path.join(out, '02-delivered.png'));
    // find the newly-written upload
    const after = fs.readdirSync(S.WFHOME + '/wf-uploads').filter(x => !beforeList.includes(x) && /big/i.test(x));
    check('video saved in ~/wf-uploads', after.length === 1, after.join(', '));
    const landed = path.join(S.WFHOME, 'wf-uploads', after[0]);
    check('uploaded bytes are byte-identical (sha256)', sha256(landed) === srcSha, sha256(landed).slice(0, 12) + ' == ' + srcSha.slice(0, 12));
    // the pasted path should be the landed file — check the real sink mirror
    const sink = fs.readFileSync(S.SINK, 'utf8');
    check('its path pasted into the focused SinkEdit', sink.includes(after[0]) || sink.includes(landed), 'sink tail: ' + sink.slice(-80).replace(/\n/g, ' '));
    check('no page errors', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  } finally { await page.close(); }
  const bad = results.filter(r => !r.ok).length;
  console.log(bad ? `\n${bad} FAIL` : '\nALL PASS');
  process.exit(bad ? 1 : 0);
}
main().catch(e => { console.error(e); process.exit(2); });
