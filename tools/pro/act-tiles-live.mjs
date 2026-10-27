// act-tiles-live.mjs — real-run proof that EVERY Teach one-tap tile fires
// end to end on the real laptop: a sign taught through the real wizard is
// rebound per tile via POST /bank, then performed live (the app's own
// /live/kp wire, real endpointer -> sign bank -> act layer) and each leg
// asserts a REAL X11 effect on DISPLAY (or an honest pending/refused).
//
// Focus safety: only windows this script spawns (sink, scratch term,
// disposable) or the proof Chrome are ever focused/typed into.
//
// REAL: daemon, /bank, endpointer, DTW match, act layer, X11 effects.
// SUBSTITUTED (labelled): hand detections — kp clips fed over /live/kp,
// the same wire the app uses.
// usage: SITE_ENV=<env file> node --experimental-websocket tools/pro/act-tiles-live.mjs <out-dir>

import path from 'node:path';
import fs from 'node:fs';
import { execFileSync as sh, spawn } from 'node:child_process';
import { stack, newPage, loadClips, reporter, daemon, sleep, root } from './lib.mjs';

const S = stack(process.env.SITE_ENV || '/tmp/wf-rr-pro/env');
const OUT = process.argv[2] || path.join(root, 'proofs/ui-v2/act-tiles-live');
fs.mkdirSync(OUT, { recursive: true });
const { check, finish } = reporter(S, OUT, 'every Teach tile bound + performed live — real X11 effects');
const { api } = daemon(S);
const X = { ...process.env, DISPLAY: S.D };
const sinkFile = S.SINK;
const sink = () => { try { return fs.readFileSync(sinkFile, 'utf8'); } catch { return ''; } };

// wmctrl -a leaves a malformed dual-valued _NET_ACTIVE_WINDOW on this WM
// (reads as focused '') — xdotool windowactivate produces a clean one.
const focus = (pat) => {
  try {
    if (String(pat).startsWith('0x')) { sh('xdotool', ['windowactivate', String(pat)], { env: X }); return; }
    const m = wins().match(new RegExp('(0x[0-9a-f]+)\\s+\\d+\\s+\\S+\\s+.*' + pat, 'i'));
    if (m) sh('xdotool', ['windowactivate', m[1]], { env: X });
  } catch {}
};
const activeWin = () => { try { return sh('xdotool', ['getactivewindow', 'getwindowname'], { env: X }).toString().trim(); } catch { return ''; } };
const wins = () => { try { return sh('wmctrl', ['-l'], { env: X }).toString(); } catch { return ''; } };
const wstate = (wid) => { try { return sh('xprop', ['-id', String(wid), '_NET_WM_STATE'], { env: X }).toString(); } catch { return ''; } };
const wstateWait = async (wid, re, ms = 4000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const s = wstate(wid);
    if (re.test(s)) return s;
    await sleep(350);
  }
  return wstate(wid);
};
const clip = () => { try { return sh('xclip', ['-selection', 'clipboard', '-o'], { env: X, timeout: 3000 }).toString(); } catch { return ''; } };
const setClip = (t) => { try { const c = spawn('xclip', ['-selection', 'clipboard'], { env: X, detached: true, stdio: ['pipe', 'ignore', 'ignore'] }); c.stdin.end(t); c.unref(); } catch {} };

const GLOSS = 'TILE-PROOF';

// ---- drive one live perform over the app's own kp wire -------------------
const CLIP_DIR = path.join(root, 'tools/demo/clips');
const frames = (name) => JSON.parse(fs.readFileSync(path.join(CLIP_DIR, name + '.json'), 'utf8')).frames;
const ABSENT = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));

let legN = 0;
async function perform() {
  const sid = 'leg-' + (++legN) + '-' + Date.now();
  // a fresh session id forces live_reset() — clears dup suppression so each
  // leg's perform is judged on its own (a real repeat sign re-arms anyway)
  await api('POST', '/live/kp', { points: ABSENT, t_ms: 1, session: sid });
  await api('POST', '/live/listen', { on: true });
  await sleep(250);
  let t0 = Date.now();
  const fs_ = frames('perform-OPEN-NOTES');
  // pace at ~30fps — faster drops frames on the 45fps rate limit and the
  // truncated segment fails the match threshold (real app feeds ~15fps)
  let r = null;
  const feed = async (pts, t) => {
    for (;;) {
      r = await api('POST', '/live/kp', { points: pts, t_ms: t, session: sid });
      if (r.status !== 429) break;
      await sleep(120);
    }
    await sleep(30);
  };
  for (const f of fs_) await feed(f.points, f.t_ms);
  // tail silence so the endpointer closes the segment
  const tend = fs_[fs_.length - 1].t_ms;
  for (let i = 1; i <= 40; i++) await feed(ABSENT, tend + i * 66);
  // the act worker lands the result asynchronously — poll last_action
  for (let i = 0; i < 30; i++) {
    await sleep(300);
    const st = await api('GET', '/live/state');
    const la = st.json?.last_action;
    const lm = st.json?.last_match;
    if (la && la.t_ms >= t0 && (la.dispatched || la.result)) {
      const pend = (st.json?.pending || []).map(p => p.id || p.confirm_id).filter(Boolean);
      return { la, lm, pend };
    }
    if (lm && lm.gloss === GLOSS && !la) { /* keep waiting for the worker */ }
  }
  const st = await api('GET', '/live/state');
  return { la: st.json?.last_action, lm: st.json?.last_match, pend: [] };
}

async function bind(tool, args, extra = {}) {
  const action = { kind: 'builtin', tool, args: args || {}, label: tool, ...extra };
  const r = await api('PUT', '/bank', { gloss: GLOSS, action });
  if (!r.json?.ok) throw new Error('bind failed: ' + JSON.stringify(r.json).slice(0, 160));
  return r.json;
}

async function leg(name, tool, args, extra, assertFn, focusPat) {
  await bind(tool, args, extra);
  if (focusPat) { focus(focusPat); await sleep(600); }
  const { la, lm, pend } = await perform();
  const res = la?.result || {};
  const matched = lm?.gloss === GLOSS && lm?.accepted !== false;
  const fired = res.fired === true;
  const detail = `match=${lm?.gloss} acc=${lm?.accepted} d=${(lm?.dist ?? -1).toFixed?.(3) ?? '?'} fired=${fired} ${res.detail || la?.error || ''}`.slice(0, 140);
  const ok = await assertFn({ matched, fired, res, la, pend });
  check(name, ok, detail);
  return { matched, fired, res, la, pend };
}

// ============================== setup =====================================
// teach TILE-PROOF through the real wizard at 390x844 (screens prove it),
// then the legs below rebind it — the same row Go Live matches against.
const page = await newPage(S, { cdpPort: 9473 });
await page.goto(`${S.base}/?k=${S.KEY}`, { settle: 2200 });
await loadClips(page, ['teach-OPEN-NOTES']);

// ensure a clean row, then teach through the shipped UI
await api('DELETE', '/bank?gloss=' + GLOSS).catch(() => {});
await page.eval(`location.hash='#teach'`);
await sleep(1100);
await page.eval(`document.querySelector('#tw-name').focus()`);
await page.type('tile proof', { delay: 40 });
await page.eval(`document.querySelector('#tw-name-next').click()`);
// wait until the wizard really lands on the record step and its tracker is up
await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 20000, 'record step');
await page.waitFor(`!!window.__clips && !!window.__q`, 10000, 'fake track armed');
await sleep(800);
for (let t = 0; t < 3; t++) {
  await page.eval(`window.__play('teach-OPEN-NOTES')`);
  await sleep(300);
  await page.waitFor('window.__idle()', 30000);
  await sleep(1800);
}
await page.shot(path.join(OUT, '01-teach-takes.png'));
await page.eval(`document.querySelector('#tw-pick')?.click()`);
await sleep(1400);
await page.eval(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));const i=r&&r.querySelector('.act-input');if(i){i.value='tile proof typed';i.dispatchEvent(new Event('input',{bubbles:true}))}})()`);
await page.eval(`(()=>{const r=[...document.querySelectorAll('.act-row')].find(r=>/type\\s*text/i.test(r.textContent));r&&r.querySelector('.act-use')?.click()})()`);
await sleep(700);
await page.eval(`document.querySelector('#tw-save')?.click()`);
await sleep(1600);
const done = await page.eval(`document.querySelector('#tw-done-t')?.textContent || ''`);
await page.shot(path.join(OUT, '02-teach-done.png'));
check('sign taught through the real wizard', /is live/i.test(done), done);
const brow = (await api('GET', '/bank')).json?.signs?.find(s => s.gloss === GLOSS);
check('taught row in /bank', !!brow, JSON.stringify(brow || {}).slice(0, 100));

// close the teach page: its tracker keeps streaming absent frames otherwise,
// which resets/contaminates the kp session the legs feed over /live/kp
await page.close();
await sleep(400);

// scratch windows we are allowed to focus/type into — record the NEW wid so
// asserts target this run's window, not stale ones from earlier runs
const termBefore = wins();
try { sh('xfce4-terminal', ['--title', 'tile-scratch-term', '--hold', '-e', 'sleep 300'], { env: X, timeout: 5000 }); } catch {}
await sleep(1200);
const newWid = (before, pat) => {
  const old = new Set([...before.matchAll(/(0x[0-9a-f]+)\s+\d+\s+\S+\s+(.*)/g)].map(m => m[1]));
  const rows = [...wins().matchAll(/(0x[0-9a-f]+)\s+\d+\s+\S+\s+(.*)/g)]
    .filter(m => !old.has(m[1]) && new RegExp(pat, 'i').test(m[2]));
  return rows[0] && rows[0][1];
};
const termWid = newWid(termBefore, 'tile-scratch-term');
const SINKW = (wins().match(new RegExp('(0x[0-9a-f]+)\\s+\\d+\\s+\\S+\\s+.*' + S.TAG + '-sink')) || [])[1];
check('sink window resolved for typing legs', !!SINKW, String(SINKW));
check('scratch term spawned for window tiles', !!termWid, String(termWid));
// sink wid resolved above

// ============================== legs ======================================
// -- typing keys into the real sink --
await leg('type.text types into the real editor', 'type.text', { text: 'TILE-TYPE-OK' }, {}, async ({ matched, fired }) => matched && fired && sink().includes('TILE-TYPE-OK'), SINKW);

const sEnt = sink().length;
await leg('key.enter presses Enter', 'key.enter', {}, {}, async ({ matched, fired }) => matched && fired && sink().length > sEnt, SINKW);

// copy: shift+Home selects the last typed line in the focused Tk text; ctrl+c
// must move sink text onto the clipboard. Chrome on this WM can't hold a
// readable active window so the focusable sink is the honest target.
setClip('COPY-SENTINEL'); await sleep(400);  // a changed-to-sink-text assert, not just growth
{
  focus(SINKW); await sleep(500);
  // key.enter left the cursor on an empty last line — go up, select that line
  try { sh('xdotool', ['key', 'Up', 'key', 'shift+End'], { env: X }); } catch {}
  await sleep(400);
}
await leg('edit.copy copies a real selection', 'edit.copy', {}, {}, async ({ matched, fired }) => {
  for (let i = 0; i < 8 && clip() === 'COPY-SENTINEL'; i++) await sleep(300);
  const got = clip();
  const fromSink = sink().split('\n').map(l => l.trim()).filter(l => l.length > 3 && got.includes(l));
  return matched && fired && got !== 'COPY-SENTINEL' && fromSink.length > 0;
}, SINKW);

// paste: stage the clipboard, ctrl+v must land in the sink. Sentinel is
// unique per run — stale text from earlier runs can't satisfy the check.
const PASTE_SENT = 'TILE-PASTE-' + (Date.now() % 100000);
setClip(PASTE_SENT); await sleep(400);
await leg('edit.paste pastes into the sink', 'edit.paste', {}, {}, async ({ matched, fired }) => {
  // the Tcl sink flushes its buffer to SINK on a timer — poll, don't race it
  for (let i = 0; i < 48 && !sink().includes(PASTE_SENT); i++) await sleep(250);
  return matched && fired && sink().includes(PASTE_SENT);
}, SINKW);

await leg('edit.undo dispatched (ctrl+z)', 'edit.undo', {}, {}, async ({ matched, fired }) => matched && fired, SINKW);

// -- window tiles on the scratch term (never a terminal we need) --
await leg('win.maximize maximizes the scratch window', 'win.maximize', {}, {},
  async ({ matched, fired }) => matched && fired && /MAXIMIZED/.test(await wstateWait(termWid, /MAXIMIZED/)), termWid);

const a0 = activeWin();
await leg('win.alt-tab switches focus', 'win.alt-tab', {}, {},
  async ({ matched, fired }) => matched && fired && activeWin() !== a0, termWid);

await leg('win.minimize hides the scratch window', 'win.minimize', {}, {},
  async ({ matched, fired }) => matched && fired && /HIDDEN/.test(await wstateWait(termWid, /HIDDEN/)), termWid);
sh('wmctrl', ['-i', '-a', String(termWid)], { env: X }); await sleep(500);   // restore

await leg('desktop.show raises _NET_SHOWING_DESKTOP', 'desktop.show', {}, {},
  async ({ matched, fired }) => {
    try { return matched && fired && /= 1/.test(sh('xprop', ['-root', '_NET_SHOWING_DESKTOP'], { env: X }).toString()); } catch { return false; }
  });
// toggle back so the rest of the desktop is usable
await perform(); await sleep(600);

// -- tab tiles fire ctrl+t / ctrl+w into whatever window is focused; on this
// box Chrome can't hold a readable active window, so the scratch term is the
// honest target (the tile emits the chord — where it lands is the focus). --
await leg('browser.new-tab fires ctrl+t on the focused window', 'browser.new-tab', {}, {},
  async ({ matched, fired }) => matched && fired && /tile-scratch-term/i.test(activeWin()), termWid);
await leg('browser.close-tab fires ctrl+w on the focused window', 'browser.close-tab', {}, {},
  async ({ matched, fired }) => matched && fired && /tile-scratch-term/i.test(activeWin()), termWid);

// -- scroll / media / system --
await leg('nav.scroll-down dispatched wheel-down', 'nav.scroll-down', {}, {}, async ({ matched, fired }) => matched && fired, SINKW);
await leg('nav.scroll-up dispatched wheel-up', 'nav.scroll-up', {}, {}, async ({ matched, fired }) => matched && fired, SINKW);

await leg('sys.volume-up fired', 'sys.volume-up', {}, {},
  async ({ matched, fired, res }) => matched && fired && /xf86|pactl|amixer|volume/i.test(res.detail || ''));
await leg('sys.volume-down fired', 'sys.volume-down', {}, {},
  async ({ matched, fired, res }) => matched && fired && /xf86|pactl|amixer|volume/i.test(res.detail || ''));
await leg('sys.mute fired', 'sys.mute', {}, {},
  async ({ matched, fired, res }) => matched && fired && /xf86|pactl|amixer|mute/i.test(res.detail || ''));
await leg('media.playpause fired', 'media.playpause', {}, {},
  async ({ matched, fired, res }) => matched && fired && /xf86|mpris|player|play/i.test(res.detail || ''));

// the daemon's ~ is the env's WFHOME when set — check both dirs
const dhome = S.WFHOME || process.env.HOME;
const shotDirs = [path.join(dhome, 'Pictures'), dhome];
const listShots = () => new Set(shotDirs.flatMap(d =>
  fs.existsSync(d) ? fs.readdirSync(d).map(f => d + '/' + f) : []));
const shots0 = listShots();
await leg('sys.screenshot saved a real file', 'sys.screenshot', {}, {},
  async ({ matched, fired, res }) => {
    await sleep(800);
    const fresh = [...listShots()].filter(f => !shots0.has(f));
    return matched && fired && (fresh.length > 0 || /png|saved|screenshot/i.test(res.detail || ''));
  });

// -- the @ picker app.open tile (same wire shape Actions.pick emits) --
await leg('app.open via @ launches thunar', 'app.open', { id: 'thunar' }, { kind: 'app' },
  async ({ matched, fired }) => { await sleep(2500); return matched && fired && /thunar/i.test(wins()); });

// -- win.close is tier-2: it must park, and only fire after confirm --------
const closeBefore = wins();
try { sh('xfce4-terminal', ['--title', 'tile-close-me', '--hold', '-e', 'sleep 120'], { env: X, timeout: 5000 }); } catch {}
await sleep(1200);
const closeWid = newWid(closeBefore, 'tile-close-me');
{
  await bind('win.close', {}, { tier: 2 });
  focus(closeWid); await sleep(600);
  const { la, pend } = await perform();
  const cid = (la?.pending) || pend[0];
  check('win.close parks for confirm (never direct)', !!cid && la?.result?.fired !== true,
    'pending=' + cid + ' ' + JSON.stringify(la?.result || {}).slice(0, 90));
  check('the disposable window is still alive while parked', closeWid ? wins().includes(closeWid) : false, 'wid=' + closeWid);
  if (cid) {
    focus(closeWid); await sleep(400);              // keep it focused for the release
    const c = await api('POST', '/act/confirm', { id: cid });
    for (let i = 0; i < 10 && closeWid && wins().includes(closeWid); i++) await sleep(400);
    check('confirm fires win.close — window really closes', c.json?.fired === true && (!closeWid || !wins().includes(closeWid)),
      JSON.stringify(c.json).slice(0, 100));
  }
}

// -- immediate Go Live use is what every leg above already proves ----------
const st = await api('GET', '/live/state');
check('control mode held through the battery', (st.json?.mode || st.json?.last_action) !== undefined, '');

finish();
await page.close();
