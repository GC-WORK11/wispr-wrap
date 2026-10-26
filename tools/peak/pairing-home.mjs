// pairing-home.mjs — first-run / pairing edge cases on the REAL stack (real
// wfd.py /pair + /pair-info + 403 handling). Web behaviour only; the server is
// read-only for PEAK-home.
// usage: node --experimental-websocket tools/peak/pairing-home.mjs <outdir> [cdp-port]
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack } from './env.mjs';

const out = process.argv[2] || 'proofs/ui-v2/pairing';
const cdpPort = Number(process.argv[3] || 8951);
const S = loadStack();
fs.mkdirSync(out, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: !!ok, detail }); console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  — ' + detail : '')); };

let page;
async function fresh({ blockPairInfo = true } = {}) {
  if (page) await page.close();
  const profile = '/tmp/peak-chrome-pair';
  fs.rmSync(profile, { recursive: true, force: true });
  page = await launchChrome({ port: cdpPort, profile, origin: S.base });
  const urls = ['http://127.0.0.1:8877/*'];
  if (blockPairInfo) urls.push(S.base + '/pair-info');   // simulate a PHONE (no loopback auto-pair)
  await page.send('Network.setBlockedURLs', { urls });
  await page.viewport(390, 844, { mobile: true, dpr: 2 });
}
const shot = n => page.shot(path.join(out, n + '.png'));
const onbErr = () => page.eval(`document.querySelector('#onb-err').textContent`);
const onbShown = () => page.eval(`!document.querySelector('#onb').hidden`);

try {
  // ============ phone: fresh browser shows the connect screen ============
  await fresh();
  await page.goto(S.base + '/', { settle: 500 });
  await page.eval(`localStorage.clear()`);
  await page.goto(S.base + '/', { settle: 1500 });
  check('a fresh phone lands on the Connect screen', await onbShown());
  await shot('01-connect-screen');

  // ============ wrong code ============
  await page.focus('#onb-code'); await page.type('WRONGXYZ'); await sleep(200);
  await page.click('#onb-go');
  await page.waitFor(`/isn.t right|Couldn.t pair|try per second/.test(document.querySelector('#onb-err').textContent)`, 8000, 'wrong-code error');
  const e1 = await onbErr();
  check("wrong code shows a clear 'that code isn't right' message", /isn.t right/i.test(e1) || /Couldn.t pair/i.test(e1), e1);
  check('wrong code does not store a key', !(await page.eval(`!!localStorage.getItem('wfk')`)));
  await shot('02-wrong-code');

  // ============ rapid retries -> 429 message ============
  // the daemon rate-limits to 1 try/sec/IP; two taps in the same second => 429
  await page.eval(`document.querySelector('#onb-code').value='ABCDEFGH'; document.querySelector('#onb-err').textContent='';`);
  await page.eval(`document.querySelector('#onb-go').click()`);
  await sleep(60);
  await page.eval(`document.querySelector('#onb-go').disabled=false; document.querySelector('#onb-go').click()`);
  const got429 = await page.waitFor(`/try per second|give it a moment/i.test(document.querySelector('#onb-err').textContent)`, 6000, '429 message').then(() => true).catch(() => false);
  check('rapid retries surface the one-try-per-second (429) message', got429, await onbErr());
  await shot('03-rate-limited');
  await sleep(1300);   // let the limiter cool down

  // ============ correct code connects and lands on the lotus ============
  await page.eval(`document.querySelector('#onb-code').value=''; document.querySelector('#onb-err').textContent='';`);
  await page.focus('#onb-code'); await page.type(S.PAIR_CODE); await sleep(200);
  await page.click('#onb-go');
  // the app stores the key then reloads to the home screen
  await page.waitFor(`!!localStorage.getItem('wfk')`, 8000, 'key stored after correct code');
  await sleep(1800);
  check('the correct code connects (key stored, connect screen gone)', (await page.eval(`!!localStorage.getItem('wfk')`)) && !(await onbShown()));
  check('after pairing the lotus home is shown', await page.eval(`['#f0','#f1','#f2'].some(s => (document.querySelector(s).textContent||'').trim().length>0)`));
  await shot('04-connected-lotus');

  // ============ stale key: 403 offers the code screen ONCE, never wipes the key ============
  await fresh();
  // seed a bogus-but-present key; /live/state will 403 it
  await page.goto(S.base + '/', { settle: 400 });
  await page.eval(`localStorage.setItem('wfk','STALEKEY-DOES-NOT-MATCH'); localStorage.setItem('wfname','Old laptop');`);
  await page.goto(S.base + '/?k=STALEKEY-DOES-NOT-MATCH', { settle: 2500 });
  // app.js: on 'unpaired' it offers the code screen once and keeps the stored key
  const offered = await page.waitFor(`!document.querySelector('#onb').hidden`, 8000, '403 offers code screen').then(() => true).catch(() => false);
  check('a stale key (403) offers the Connect screen', offered);
  check('a stale key is NOT wiped (reconnect without retyping stays possible)', await page.eval(`!!localStorage.getItem('wfk')`), 'wfk still present');
  check('the Connect screen for a stale key is dismissible (Not now)', await page.eval(`!document.querySelector('#onb-skip').hidden`));
  await shot('05-stale-key-offer');
  // dismiss, then confirm it is only offered once (no re-pop loop)
  await page.click('#onb-skip'); await sleep(400);
  check('dismissing the stale-key offer hides it', !(await onbShown()));
  await sleep(2500);   // several /live/state 403 cycles
  check('the stale-key offer is shown only once (no nagging re-pop)', !(await onbShown()));
  await shot('06-stale-key-dismissed');

  // ============ laptop-local browser auto-pairs via /pair-info, no typing ============
  await fresh({ blockPairInfo: false });   // allow loopback /pair-info (this harness IS localhost)
  await page.goto(S.base + '/', { settle: 500 });
  await page.eval(`localStorage.clear()`);
  await page.goto(S.base + '/', { settle: 2500 });
  const autoKey = await page.eval(`!!localStorage.getItem('wfk')`);
  check('a laptop-local browser auto-pairs via /pair-info with no typing', autoKey && !(await onbShown()), autoKey ? 'key obtained from /pair-info' : 'no key');
  await shot('07-auto-paired');

  // ============ 'Forget this laptop' returns to the connect screen ============
  // open Connected laptop, click Forget. On a phone (no loopback) this returns
  // to Connect; here (localhost) we assert the web contract: the key is cleared
  // and firstRun re-runs.
  await fresh();                          // phone: block /pair-info so forget cannot auto-repair
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2000 });
  await page.eval(`location.hash = '#/laptop'`); await sleep(1200);
  const hasForget = await page.eval(`(() => { const b = document.querySelector('#lp-forget'); return !!b && !b.hidden; })()`).catch(() => false);
  await shot('08-connected-laptop-page');
  if (hasForget) {
    await page.click('#lp-forget');
    await page.waitFor(`!document.querySelector('#onb').hidden || !localStorage.getItem('wfk')`, 8000, 'forget cleared the link');
    await sleep(600);
    check("'Forget this laptop' clears the stored laptop and returns to Connect", (await onbShown()) && !(await page.eval(`!!localStorage.getItem('wfk')`)), 'key cleared + connect shown');
    await shot('09-forgotten-connect');
  } else {
    check("'Forget this laptop' button is present on the connected page", false, 'lp-forget hidden/absent (laptop.js owner)');
  }

  check('no page errors', page.errors.length === 0, page.errors.slice(0, 3).join(' | '));
} finally {
  if (page) await page.close();
}

const body = results.map(r => `${r.ok ? 'PASS' : 'FAIL'}\t${r.name}\t${r.detail}`).join('\n');
fs.writeFileSync(path.join(out, 'RESULT.txt'), `# ${new Date().toISOString()}  pairing-home\n${body}\n`);
process.exitCode = results.every(r => r.ok) ? 0 : 1;
