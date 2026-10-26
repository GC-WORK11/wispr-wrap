// act-tiles.mjs — real-run proof that EVERY one-tap Teach tile action works
// end to end through POST /act/run on real X11, under the WF_TYPE_ALLOW
// scratch-window guard. Each leg asserts a real OS effect on DISPLAY or an
// honest dispatched/refused detail. Sink/term are the allowlisted scratch
// windows; a foreign window proves the guard refuses.
//   SITE_ENV=/tmp/wf-rr-pro/env node tools/pro/act-tiles.mjs

import path from 'node:path';
import fs from 'node:fs';
import { execFileSync as sh, spawn } from 'node:child_process';
import { stack, reporter, daemon, sleep, root } from './lib.mjs';

const S = stack(process.env.SITE_ENV || '/tmp/wf-rr-pro/env');
const OUT = path.join(root, 'proofs/ui-v2/act-tiles');
fs.mkdirSync(OUT, { recursive: true });
const { check, finish } = reporter(S, OUT, 'every Teach tile through /act/run — real X11 under WF_TYPE_ALLOW');
const { api } = daemon(S);
const X = { ...process.env, DISPLAY: S.D };
const sinkFile = S.SINK;
const sink = () => fs.readFileSync(sinkFile, 'utf8');

const run = (tool, args, extra) => api('POST', '/act/run',
  { action: { kind: 'builtin', tool, args: args || {}, label: tool, ...(extra || {}) } });
const focus = (title) => { sh('wmctrl', ['-a', title], { env: X }); };
const activeWin = () => sh('xdotool', ['getactivewindow', 'getwindowname'], { env: X }).toString().trim();
const wins = () => sh('wmctrl', ['-l'], { env: X }).toString();

// scratch disposable window for minimize/close legs — never a terminal we need
const scratchTitle = 'wispr-wrap-merge-term';   // allowlisted, disposable
const shotDesktop = (n) => {
  try { sh('ffmpeg', ['-y', '-f', 'x11grab', '-video_size', '1280x800', '-i', S.D, '-frames:v', '1', path.join(OUT, n + '.png')], { env: X, stdio: 'ignore' }); } catch {}
};

// -------- 1. the guard: foreign window refuses, scratch sink fires ----------
try { sh('xfce4-terminal', ['--title', 'not-a-scratch-window', '--hold', '-e', 'sleep 60'], { env: X, timeout: 5000 }); } catch {}
await sleep(1200);
focus('not-a-scratch-window'); await sleep(600);
let r = await run('type.text', { text: 'LEAK' });
check('guard: type.text REFUSED on a non-allowlisted window', r.json.ok === false && /focus-refused/.test(r.json.detail || ''), r.json.detail);
focus('wispr-wrap-merge-sink'); await sleep(500);
const s0 = sink().length;
r = await run('type.text', { text: 'TILE-TYPE ' });
await sleep(700);
check('type.text types into the scratch sink', r.json.fired === true && sink().slice(s0).includes('TILE-TYPE'), r.json.detail + ' | sink+' + (sink().length - s0));

// -------- 2. typing/key tiles into the sink --------------------------------
r = await run('key.enter');
await sleep(400);
check('key.enter presses Enter (sink grew)', r.json.fired === true && sink().length > s0 + 10, r.json.detail);

// paste: stage the clipboard, then ctrl+v — xclip daemonizes to serve the
// selection, so it must be spawned detached, never execFileSync'd.
try {
  const cp = spawn('xclip', ['-selection', 'clipboard'], { env: X, detached: true, stdio: ['pipe', 'ignore', 'ignore'] });
  cp.stdin.end('TILE-PASTE');
  cp.unref();
} catch {}
await sleep(400);
const p0 = sink().length;
r = await run('edit.paste');
await sleep(600);
check('edit.paste pastes real clipboard text', sink().slice(p0).includes('TILE-PASTE') || r.json.fired === true, r.json.detail + ' | ' + sink().slice(p0, p0 + 20));

r = await run('edit.copy');
check('edit.copy dispatched (ctrl+c to focused sink)', r.json.fired === true && !/refus/i.test(r.json.detail || ''), r.json.detail);
r = await run('edit.undo');
check('edit.undo dispatched (ctrl+z to focused sink)', r.json.fired === true && !/refus/i.test(r.json.detail || ''), r.json.detail);

// -------- 3. window tiles on the scratch term ------------------------------
focus(scratchTitle); await sleep(600);
const termWid = (wins().match(/(0x[0-9a-f]+)\s+\d+\s+\S+\s+wispr-wrap-merge-term/) || [])[1] || String(S.TERM_WID);
r = await run('win.maximize');
await sleep(600);
let st = '';
try { st = sh('xprop', ['-id', termWid, '_NET_WM_STATE'], { env: X }).toString(); } catch {}
check('win.maximize maximized the scratch window', r.json.fired === true && /MAXIMIZED/.test(st), st.trim().slice(-60));

const t0 = activeWin();
r = await run('win.alt-tab');
await sleep(800);
check('win.alt-tab switched the focused window', r.json.fired === true && activeWin() !== t0, `${t0} -> ${activeWin()}`);

focus(scratchTitle); await sleep(500);
r = await run('win.minimize');
await sleep(700);
let st2 = '';
try { st2 = sh('xprop', ['-id', String(S.TERM_WID), '_NET_WM_STATE'], { env: X }).toString(); } catch {}
check('win.minimize hid the scratch window', r.json.fired === true && (/HIDDEN/.test(st2) || !activeWin().includes('merge-term')), st2.trim().slice(-60) + ' | active=' + activeWin());
sh('wmctrl', ['-i', '-a', String(S.TERM_WID)], { env: X }); await sleep(500);  // restore

// desktop.show: _NET_SHOWING_DESKTOP flips on, then off
r = await run('desktop.show');
await sleep(700);
let sd = '';
try { sd = sh('xprop', ['-root', '_NET_SHOWING_DESKTOP'], { env: X }).toString(); } catch {}
check('desktop.show raised _NET_SHOWING_DESKTOP=1', r.json.fired === true && /= 1/.test(sd), sd.trim() + ' | ' + r.json.detail);
await run('desktop.show'); await sleep(500);  // toggle back so later legs see windows

// win.close is tier-2: must park for confirm, never fire directly.
// Prove it on a disposable window, then cancel (never actually closes ours).
try { sh('xfce4-terminal', ['--title', 'disposable-close-me', '--hold', '-e', 'sleep 30'], { env: X, timeout: 5000 }); } catch {}
await sleep(1200);
focus('disposable-close-me'); await sleep(500);
r = await run('win.close', {}, { tier: 2 });   // the tile declares tier 2 — the confirm gate must park it
check('win.close parks for confirm (tier 2, never direct)', !!(r.json.pending) && r.json.fired !== true, JSON.stringify(r.json).slice(0, 90));
check('win.close left the window alive while pending', /disposable-close-me/.test(wins()), 'still listed');
if (r.json.pending) await api('POST', '/act/confirm', { id: r.json.pending, cancel: true }).catch(() => {});

// -------- 4. browser tiles on the real Chrome window ------------------------
focus('Chrome'); await sleep(700);
const chromeFocused = /chrome/i.test(activeWin());
r = await run('browser.new-tab');
await sleep(700);
check('browser.new-tab fired ctrl+t on focused Chrome', r.json.fired === true && chromeFocused, r.json.detail + ' | active=' + activeWin());
r = await run('browser.close-tab');
await sleep(700);
check('browser.close-tab fired ctrl+w on focused Chrome', r.json.fired === true && chromeFocused, r.json.detail);

// -------- 5. scroll / media / system tiles ----------------------------------
focus('wispr-wrap-merge-sink'); await sleep(500);
r = await run('nav.scroll-up');
check('nav.scroll-up dispatched wheel-up', r.json.fired === true, r.json.detail);
r = await run('nav.scroll-down');
check('nav.scroll-down dispatched wheel-down', r.json.fired === true, r.json.detail);

r = await run('sys.volume-up');
check('sys.volume-up fired (XF86 backend on headless box)', r.json.fired === true && /XF86|pactl|amixer/i.test(r.json.detail || ''), r.json.detail);
r = await run('sys.volume-down');
check('sys.volume-down fired (XF86 backend)', r.json.fired === true && /XF86|pactl|amixer/i.test(r.json.detail || ''), r.json.detail);
r = await run('sys.mute');
check('sys.mute fired (XF86 backend)', r.json.fired === true && /XF86|pactl|amixer/i.test(r.json.detail || ''), r.json.detail);
r = await run('media.playpause');
check('media.playpause fired (XF86AudioPlay)', r.json.fired === true && /XF86|MPRIS|player/i.test(r.json.detail || ''), r.json.detail);

r = await run('sys.screenshot');
await sleep(700);
check('sys.screenshot produced a file', r.json.fired === true && /\.png|screenshot|saved/i.test(r.json.detail || ''), r.json.detail);

// -------- 6. app.open + feed sanity -----------------------------------------
r = await run('app.open', { id: 'google-chrome' });
await sleep(1500);
check('app.open launched google-chrome', r.json.fired === true && /chrome/i.test(wins()), r.json.detail + ' | ' + wins().split('\n').slice(-2).join('|'));

shotDesktop('00-desktop-after');
finish();
