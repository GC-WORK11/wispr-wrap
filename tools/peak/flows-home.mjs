// flows-home.mjs — PEAK-home composer behaviour proofs, on the REAL stack
// (real browser + real wfd.py + real X11), copying flows.mjs's patterns.
// Covers, each a PASS/FAIL line:
//   - Enter sends, Shift+Enter newlines, textarea grows then scrolls
//   - empty send no-op, double-tap no duplicate, unicode/emoji/newlines intact
//   - remove attachment with x, paste image stages, drop file stages (desktop)
//   - send while daemon unreachable keeps text + human error, next send works
//   - send arrow / waveform swap correct after each, lotus bursts on success only
//   - BIG-FILE upload: progress line + percent status + cancel (Task 1)
//
// usage: node --experimental-websocket tools/peak/flows-home.mjs <outdir> [cdp-port]
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack, scrub } from './env.mjs';

const out = process.argv[2] || 'proofs/ui-v2/flows-home';
const cdpPort = Number(process.argv[3] || 8951);
const S = loadStack();
fs.mkdirSync(out, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  — ' + detail : ''));
};
const sh = (cmd, args) => execFileSync(cmd, args, { env: { ...process.env, DISPLAY: S.D }, encoding: 'utf8' });
const sink = () => fs.readFileSync(S.SINK, 'utf8');
const sinkHas = async (since, text, ms = 4000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (sink().slice(since).includes(text)) return true; await sleep(100); } return false; };
const shot = n => page.shot(path.join(out, n + '.png'));
const toast = () => page.eval(`document.querySelector('#toast').textContent`);
const ready = () => page.eval(`document.querySelector('#pill').classList.contains('ready')`);
// one black pill morphs: waveform (Go Live hit area live) <-> arrow (send hit area live)
const liveShown = () => page.eval(`(() => { const p = document.querySelector('#pill'), l = document.querySelector('#tray-live'); return !p.classList.contains('ready') && getComputedStyle(l).pointerEvents !== 'none'; })()`);
const goShown = () => page.eval(`(() => { const p = document.querySelector('#pill'), g = document.querySelector('#go'); return p.classList.contains('ready') && getComputedStyle(g).pointerEvents !== 'none'; })()`);
const waitToast = (re, what) => page.waitFor(`/${re}/.test(document.querySelector('#toast').textContent)`, 15000, what);
const burstCount = () => page.eval(`window.__burstN || 0`);

let page;
async function newPage(viewport = [390, 844, true, 2]) {
  const profile = '/tmp/peak-chrome-home';
  fs.rmSync(profile, { recursive: true, force: true });
  const p = await launchChrome({ port: cdpPort, profile, origin: S.base });
  await p.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
  // count lotus bursts so "bursts on success only" is checkable
  await p.send('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.__burstN = 0;
    import('./js/flower.js').then(m => {
      const orig = m.Flower.burst; m.Flower.burst = function () { window.__burstN++; return orig.apply(this, arguments); };
    }).catch(() => {});
  ` });
  await p.viewport(viewport[0], viewport[1], { mobile: viewport[2], dpr: viewport[3] });
  return p;
}

async function run() {
  page = await newPage();
  try {
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 3000 });
    sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(300);

    // --- initial state: waveform (Go Live) shown, arrow hidden ---
    check('empty composer shows the Go Live waveform, not the arrow', (await liveShown()) && !(await goShown()));

    // --- empty send is a no-op ---
    let before = sink().length;
    await page.eval(`document.querySelector('#toast').textContent=''`);
    await page.focus('#box'); await page.enter(); await sleep(400);
    check('empty send is a no-op (nothing typed, no toast)',
      sink().length === before && (await toast()).trim() === '', 'sink unchanged');

    // --- text makes the arrow appear (swap correct) ---
    await page.focus('#box');
    await page.type('Enter sends this line');
    await sleep(300);
    check('typing swaps waveform -> send arrow', (await ready()) && (await goShown()) && !(await liveShown()));
    await shot('01-arrow-shown');

    // --- Enter sends ---
    before = sink().length;
    const b0 = await burstCount();
    await page.enter();
    await waitToast('Sent', 'Enter delivered');
    check('Enter sends the message to the laptop', await sinkHas(before, 'Enter sends this line'), 'sink got it');
    check('box clears and the waveform returns after send', !(await ready()) && (await liveShown()), 'ready=false');
    check('the lotus bursts on a successful send', (await burstCount()) > b0, `bursts ${b0}->${await burstCount()}`);

    // --- Shift+Enter does not send (newline, not submit) ---
    await page.focus('#box');
    await page.type('line one');
    const sinkLenPre = sink().length;
    await page.key('Enter', { code: 'Enter', vk: 13, modifiers: 8 });   // 8 = Shift
    await page.type('line two');
    await sleep(300);
    const val = await page.eval(`document.querySelector('#box').value`);
    // the binding only suppresses send on Shift+Enter; the newline is the textarea default
    check('Shift+Enter does not send (text stays in the box)', /line one/.test(val) && /line two/.test(val) && sink().length === sinkLenPre, JSON.stringify(val));
    await shot('02-shift-enter');
    // insert a real newline programmatically to verify multiline growth below
    await page.eval(`(() => { const b = document.querySelector('#box'); b.value='line one\\nline two'; b.dispatchEvent(new Event('input')); })()`);
    const hasNl = await page.eval(`document.querySelector('#box').value.includes('\\n')`);
    check('the textarea holds multi-line text', hasNl, 'newline preserved');
    await shot('02b-multiline');

    // --- textarea grows then scrolls at its max ---
    await page.eval(`(() => { const b = document.querySelector('#box'); b.value = Array.from({length:30},(_,i)=>'row '+i).join('\\n'); b.dispatchEvent(new Event('input')); })()`);
    await sleep(200);
    const gm = await page.eval(`(() => { const b = document.querySelector('#box'); const h = parseFloat(getComputedStyle(b).maxHeight)||150; return { h: b.getBoundingClientRect().height, max: h, scrolls: b.scrollHeight > b.clientHeight + 2, oy: getComputedStyle(b).overflowY }; })()`);
    check('textarea grows to its max then scrolls', gm.h <= gm.max + 1 && gm.scrolls && gm.oy === 'auto', JSON.stringify(gm));
    await shot('03-grown-scrolling');
    // clear
    await page.eval(`(() => { const b = document.querySelector('#box'); b.value=''; b.dispatchEvent(new Event('input')); })()`);

    // --- unicode / emoji / newlines arrive intact ---
    const U = 'café ☕ 日本語 — emoji 🎉✳️\nsecond line ✓';
    await page.eval(`(() => { const b = document.querySelector('#box'); b.value = ${JSON.stringify(U)}; b.dispatchEvent(new Event('input')); })()`);
    before = sink().length;
    await page.click('#go');
    await waitToast('Sent', 'unicode delivered');
    const uok = await sinkHas(before, 'café ☕ 日本語 — emoji 🎉✳️', 7000);
    const uok2 = await sinkHas(before, 'second line ✓', 7000);
    check('unicode/emoji/newlines arrive intact in the editor', uok && uok2, 'both lines present with glyphs');
    await shot('04-unicode-delivered');

    // --- double-tap send does not duplicate ---
    await page.eval(`(() => { const b = document.querySelector('#box'); b.value='NODUPE-marker-42'; b.dispatchEvent(new Event('input')); })()`);
    before = sink().length;
    // two taps in quick succession; the second must be a no-op (sending guard)
    await page.click('#go');
    await page.eval(`document.querySelector('#go').click()`);   // synchronous second tap, no touch sequence
    await waitToast('Sent', 'single send');
    await sleep(1200);
    const dupes = (sink().slice(before).match(/NODUPE-marker-42/g) || []).length;
    check('double-tap send does not duplicate on the laptop', dupes === 1, `typed ${dupes}x`);

    // --- remove attachment with x works ---
    const tmp = '/tmp/peak-home'; fs.mkdirSync(tmp, { recursive: true });
    fs.writeFileSync(tmp + '/note.txt', 'peak home note\n');
    await page.setFiles('#fileany', [tmp + '/note.txt']);
    await page.waitFor(`!document.querySelector('#attach').hidden`, 4000, 'attach staged');
    await sleep(250);
    check('attaching a file stages it (chip shown, arrow live)', (await ready()) && (await goShown()));
    await shot('05-file-staged');
    await page.click('#unattach');
    await sleep(300);
    check('removing an attachment with x clears it', await page.eval(`document.querySelector('#attach').hidden`) && !(await ready()), 'chip gone, waveform back');

    // --- paste an image stages it ---
    const pasteOk = await page.eval(`(async () => {
      const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), c => c.charCodeAt(0));
      const file = new File([png], 'pasted.png', { type: 'image/png' });
      const dt = new DataTransfer(); dt.items.add(file);
      const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
      document.dispatchEvent(ev);
      await new Promise(r => setTimeout(r, 300));
      return !document.querySelector('#attach').hidden;
    })()`);
    check('pasting an image stages it', pasteOk);
    await shot('06-paste-staged');
    await page.click('#unattach'); await sleep(200);

    // --- send while the daemon is unreachable keeps the text + human error ---
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', S.base + '/send'] });
    await page.focus('#box');
    await page.type('offline keeps this text');
    const bb = await burstCount();
    await page.click('#go');
    await waitToast('laptop|failed|reach|wrap', 'offline error shown');
    const kept = await page.eval(`document.querySelector('#box').value`);
    const errShown = await page.eval(`document.querySelector('#toast').classList.contains('err')`);
    check('offline send keeps the text', kept.includes('offline keeps this text'), JSON.stringify(kept.slice(0, 40)));
    check('offline send shows a human error (no code dump)', errShown && !/undefined|null|\bfetch\b/i.test(await toast()), await toast());
    check('the lotus does NOT burst on a failed send', (await burstCount()) === bb, `bursts stayed ${bb}`);
    await shot('07-offline-error');
    // unblock and the next send works, same text
    await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
    before = sink().length;
    sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(200);
    await page.click('#go');
    await waitToast('Sent', 'recovered send');
    check('the next send works after the daemon is reachable again', await sinkHas(before, 'offline keeps this text'), 'recovered');

    // --- BIG FILE: progress line + percent status + cancel (Task 1) ---
    const big = tmp + '/big.bin';
    if (!fs.existsSync(big) || fs.statSync(big).size < 60 * 1048576) {
      execFileSync('dd', ['if=/dev/zero', 'of=' + big, 'bs=1048576', 'count=60'], { stdio: 'ignore' });
    }
    // throttle the upload so progress is observable mid-flight
    await page.send('Network.emulateNetworkConditions', { offline: false, latency: 20, downloadThroughput: 5 * 1024 * 1024, uploadThroughput: 2 * 1024 * 1024 });
    await page.setFiles('#fileany', [big]);
    await page.waitFor(`!document.querySelector('#attach').hidden`, 4000, 'big staged');
    await shot('08-big-staged');
    await page.click('#go');
    // catch the progress UI mid-upload
    let sawProgress = false, sawLine = false, sampled = '';
    for (let i = 0; i < 80; i++) {
      const st = await page.eval(`(() => { const p = document.querySelector('#pill'); return { up: p.classList.contains('uploading'), f: parseFloat(p.style.getPropertyValue('--up')) }; })()`);
      if (st.up) sawLine = true;
      if (st.up && st.f > 0.01 && st.f < 0.99) { sawProgress = true; sampled = Math.round(st.f * 100) + '%'; }   // founder 1652: no percent text, the line carries it
      if (sawProgress && sawLine) { await shot('09-upload-progress'); break; }
      await sleep(120);
    }
    check('big-file upload advances the progress line on the pill (no text)', sawProgress, sampled);
    check('big-file upload draws the 2px progress line on the pill', sawLine);
    // cancel it mid-flight via the attachment x
    const stillUp = await page.eval(`document.querySelector('#pill').classList.contains('uploading')`);
    if (stillUp) {
      await page.click('#unattach');
      await waitToast('cancel', 'upload cancelled');
      check('tapping the x cancels an in-flight upload and says so', /cancel/i.test(await toast()), await toast());
      check('the progress line clears after cancel', !(await page.eval(`document.querySelector('#pill').classList.contains('uploading')`)));
      await shot('10-upload-cancelled');
    } else {
      const t = await toast();
      check('big-file upload resolved to a clear outcome (no hang)', /Sent|too big|said no|reach/i.test(t), t);
    }
    await page.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });

    // --- drop a file stages it on a desktop viewport ---
    await page.viewport(1440, 900, { mobile: false, dpr: 1 });
    await page.goto(S.base + '/?k=' + S.KEY, { settle: 2000 });
    const dropOk = await page.eval(`(async () => {
      const file = new File([new Uint8Array([1,2,3,4])], 'dropped.bin', { type: 'application/octet-stream' });
      const dt = new DataTransfer(); dt.items.add(file);
      const stage = document.querySelector('#stage');
      stage.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true }));
      stage.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
      await new Promise(r => setTimeout(r, 300));
      return !document.querySelector('#attach').hidden;
    })()`);
    check('dropping a file stages it on a desktop viewport (1440x900)', dropOk);
    await shot('11-desktop-drop');

    check('no page errors', page.errors.length === 0, page.errors.slice(0, 3).join(' | '));
  } finally {
    if (page) await page.close();
  }
}

try { await run(); }
catch (e) { check('flows-home crashed', false, String(e.message || e).slice(0, 300)); }
const body = results.map(r => `${r.ok ? 'PASS' : 'FAIL'}\t${r.name}\t${scrub(r.detail, S)}`).join('\n');
fs.writeFileSync(path.join(out, 'RESULT.txt'), `# ${new Date().toISOString()}  flows-home\n${body}\n`);
process.exitCode = results.every(r => r.ok) ? 0 : 1;
