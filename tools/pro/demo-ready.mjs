// demo-ready.mjs — end-to-end proof of chalk/demo-ready on the realrun stack:
// pair -> @ window pick -> text/image/video sends -> Teach -> Go Live fires.
//   node --experimental-websocket tools/pro/demo-ready.mjs
// Stack: tools/realrun/setup.sh env (private Xvfb, SinkEdit editor + xfce4
// `cat` terminal — the only windows the run ever focuses).
// Substituted: hand detection only (WFFakeTrack replays recorded kp42 clips).
import fs from 'node:fs';
import path from 'node:path';
import { stack, newPage, loadClips, playAndWait, shotTo, reporter, daemon, sleep } from './lib.mjs';

const S = stack(process.env.SITE_ENV || '/tmp/dr-test/state/env');
const GLOSS = 'DR' + String(Date.now()).slice(-4);   // unique — wizard rejects dup names
const OUT = path.join(S.STATE, 'proofs', 'ui-v2', 'demo-ready');
const { check, finish } = reporter(S, OUT, 'demo-ready e2e: pair, @ pick, sends, teach, live');
const D = daemon(S);
const TERMSINK = S.TERMSINK;
const read = f => fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '';
const waitFor = async (fn, ms = 6000, poll = 150) => {
  const t0 = Date.now(); let v;
  while (Date.now() - t0 < ms) { if ((v = await fn())) return v; await sleep(poll); }
  return v;
};
const focus = async wid => { D.sh('xdotool', ['windowactivate', wid]); await sleep(350); };
const sinkWid = () => D.sh('xdotool', ['search', '--class', 'SinkEdit']).trim().split('\n').pop();
const termWid = () => D.sh('xdotool', ['search', '--name', '^' + S.TAG + '-term$']).trim().split('\n').pop();

// wipe stale DR* test signs — identical replay clips make DTW ambiguous
{
  const b = await D.api('GET', '/bank');
  const glosses = (b.json && (b.json.signs || b.json.rows || b.json.bank || [])) || [];
  for (const g of glosses) {
    const name = typeof g === 'string' ? g : (g.gloss || g.name || '');
    if (name) await D.api('DELETE', '/bank', { gloss: name });   // identical replay clips make DTW ambiguous
  }
}
/* send fixtures — the run creates them if the stack lacks them */
{
  const { execFileSync } = await import('node:child_process');
  const png = path.join(S.STATE, 'photo.png'), mp4 = path.join(S.STATE, 'clip.mp4');
  if (!fs.existsSync(png))
    execFileSync('ffmpeg', ['-y','-loglevel','error','-f','lavfi','-i','color=c=0xf12f92:s=640x400','-frames:v','1',png]);
  if (!fs.existsSync(mp4))
    execFileSync('ffmpeg', ['-y','-loglevel','error','-f','lavfi','-i','testsrc=duration=1:size=320x240:rate=10','-pix_fmt','yuv420p',mp4]);
}

const page = await newPage(S, { cdpPort: 9391 });
page.on('console', m => { if (m.type === 'error') console.log('  [page]', m.text.slice(0, 140)); });
await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
await loadClips(page, ['teach-TYPE-HELLO', 'perform-TYPE-HELLO', 'palm-hold', 'idle']);

/* ---- 1. paired composer ---- */
const paired = await waitFor(() => page.eval(`!!document.querySelector('#box')`));
check('pair via LAN URL opens the composer', paired);
await shotTo(page, OUT, '01-paired');

const type = async sel => page.eval(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.value='';e.focus();return true})()`);
const setVal = async (sel, v) => page.eval(`(()=>{const e=document.querySelector(${JSON.stringify(sel)});e.value=${JSON.stringify(v)};e.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
const click = async sel => page.eval(`document.querySelector(${JSON.stringify(sel)}).click()`);
const sendText = async t => { await setVal('#box', t); await click('#go'); };

/* ---- 2. @ window picker -> ${S.TAG}-term (xfce4 `cat` -> TERMSINK) ---- */
const t0_term = read(TERMSINK).length, t0_sink = read(S.SINK).length;
await setVal('#box', '@');
await waitFor(() => page.eval(`document.querySelectorAll('.win-item').length`));
const wins = await page.eval(`[...document.querySelectorAll('.win-item')].map(b=>b.textContent)`);
check('@ menu lists real open windows', wins.some(w => w.includes(S.TAG + '-term')), JSON.stringify(wins));
await shotTo(page, OUT, '02-at-menu');
await page.eval(`[...document.querySelectorAll('.win-item')].find(b=>b.textContent.includes('${S.TAG}-term')).click()`);
const chip = await page.eval(`document.querySelector('.tchip') && !document.querySelector('.tchip').hidden`);
check('@ chip shows the picked window', !!chip);
await sendText('AT-TERM-OK');
// `cat` sits behind the tty line buffer — flush it with Enter like a real
// user would, then the typed line reaches TERMSINK.
await sleep(600);
D.sh('xdotool', ['key', 'Return']);   // focused window = the @-picked term
const termGot = await waitFor(() => read(TERMSINK).slice(t0_term).includes('AT-TERM-OK'));
check('text sent via @ lands in the picked window', termGot);
check('@ send does not leak to the editor', !read(S.SINK).slice(t0_sink).includes('AT-TERM-OK'));
await shotTo(page, OUT, '03-at-sent');
// clear the target: back to focused-window sends
await page.eval(`document.querySelector('.tchip-x') && document.querySelector('.tchip-x').click()`);

/* ---- 3. text send into the focused scratch editor ---- */
await focus(sinkWid());
const UTXT = 'dr-proof-' + String(Date.now()).slice(-6);
await sendText(UTXT);
check('typed text lands in the focused editor', await waitFor(() => read(S.SINK).includes(UTXT)));
await shotTo(page, OUT, '04-text-sent');

/* ---- 4. image send -> clipboard paste -> path lands in the editor ---- */
const fileInput = async (sel, f) => page.setFiles(sel, [f]);
await focus(sinkWid());
const s1 = 0;
const hadImg = read(S.SINK);
await fileInput('#file', path.join(S.STATE, 'photo.png'));
await waitFor(() => page.eval(`!document.querySelector('#attach').hidden`), 8000);
await click('#go');
const imgLanded = await waitFor(() => { const t = read(S.SINK); return t !== hadImg && /wf-img|\.png|uploads/i.test(t); }, 10000);
check('image send pastes its staged path into the editor', imgLanded);
await shotTo(page, OUT, '05-image-sent');

/* ---- 5. video send -> /send-file -> path lands in the editor ---- */
await focus(sinkWid());
const s2 = 0;
const hadVid = read(S.SINK);
await fileInput('#fileany', path.join(S.STATE, 'clip.mp4'));
await waitFor(() => page.eval(`!document.querySelector('#attach').hidden`), 8000);
await click('#go');
const vidLanded = await waitFor(() => { const t = read(S.SINK); return t !== hadVid && /\.mp4|wf-file|uploads/i.test(t); }, 25000);
check('video send pastes its staged path into the editor', vidLanded);
const upDir = path.join(S.WFHOME || path.join(S.STATE, 'home'), 'wf-uploads');
const upFile = fs.existsSync(upDir) && fs.readdirSync(upDir).filter(f => f.endsWith('_clip.mp4')).sort().pop();
check('the uploaded file is a real file on the laptop', !!upFile, upFile || 'no wf-uploads file');
await shotTo(page, OUT, '06-video-sent');

/* ---- 6. Teach a new sign through the wizard ---- */
await click('#tray-teach');
await waitFor(() => page.eval(`!!document.querySelector('#tw-name')`));
await setVal('#tw-name', GLOSS);
await page.eval(`document.querySelector('#tw-name').dispatchEvent(new Event('input',{bubbles:true}))`);
await click('#tw-name-next');
for (let i = 0; i < 3; i++) {
  await waitFor(() => page.eval(`document.querySelector('#tw-rec') && !document.querySelector('#tw-rec').hidden`), 8000);
  await playAndWait(page, 'teach-TYPE-HELLO', 30000);
  await sleep(400);
}
await shotTo(page, OUT, '07-teach-takes');
await waitFor(() => page.eval(`!document.querySelector('#tw-rec-done').disabled`), 8000);
await click('#tw-rec-done');
await waitFor(() => page.eval(`!!document.querySelector('#tw-pick')`));
await click('#tw-pick');
await waitFor(() => page.eval(`!!document.querySelector('#act-q')`));
await setVal('#act-q', 'type text');
await waitFor(() => page.eval(`!!document.querySelector('.act-input')`));
const SENT = 'sign-' + String(Date.now()).slice(-6);
await page.eval(`(()=>{const i=document.querySelector('.act-input');i.value=${JSON.stringify(SENT)};i.dispatchEvent(new Event('input',{bubbles:true}))})()`);
await shotTo(page, OUT, '08-teach-pick');
await click('.act-use');
await waitFor(() => page.eval(`!document.querySelector('#tw-save').disabled`), 8000);
await click('#tw-save');
await waitFor(() => page.eval(`!!document.querySelector('#tw-done-live')`), 8000);
const bank = await D.api('GET', '/bank');
check('wizard finishes with the sign banked', bank.status === 200 && JSON.stringify(bank.json).includes(GLOSS));
await shotTo(page, OUT, '09-teach-done');

/* ---- 7. Go Live: perform the sign -> bound type.text fires ---- */
await click('#tw-done-live');
await waitFor(() => page.eval(`!document.querySelector('#sheet-live').hidden`), 8000);
await focus(sinkWid());   // opening the sheet focused the browser — sends go to the focused app
// control mode is the default tab; palm-hold arms listening, then the sign
await playAndWait(page, 'palm-hold', 15000);
await page.eval(`window.__play('idle')`);
await playAndWait(page, 'perform-TYPE-HELLO', 30000);
const fired = await waitFor(async () => {
  const st = await D.api('GET', '/live/state');
  const m = st.json && st.json.last_match;
  return m && m.accepted && String(m.gloss || '') === GLOSS ? st.json : null;
}, 8000);
check('Go Live match accepted (' + GLOSS + ')', !!fired, fired && JSON.stringify(fired.last_match));
const typed = await waitFor(() => read(S.SINK).includes(SENT), 8000);
check('bound type.text action types into the focused editor', !!typed);
const guess = await page.eval(`(document.querySelector('#lv-guess')||{}).textContent||''`);
check('live readout names the sign', new RegExp(GLOSS.slice(0,2) + '[ -]?' + GLOSS.slice(2), 'i').test(guess), guess);
await shotTo(page, OUT, '10-live-fired');

finish();
process.exit(process.exitCode || 0);
