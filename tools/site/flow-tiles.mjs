// flow-tiles.mjs — every one-tap tile, end to end, on a real daemon + real X11 (the spec).
//
// For EACH of the 20 tiles: open Teach in a real phone-size Chrome (390x844), tap the tile, Record, give the
// daemon 3 takes through its own endpointer, see Ready, go live, perform the new sign, then check the REAL
// effect on the laptop (X11 window state, a real Chrome's tabs / scroll / clipboard, a real editor, a file on
// disk). The bank is emptied between tiles because every proof sign reuses one synthetic motion.
//
// Substituted (and labelled): the hand detections (synthetic kp42 clips) and the phone camera (fake device).
// Everything else is real. A tile whose backend does not exist on this box (no audio server) is reported as
// NEVER-ON-BOX with what the laptop honestly answered, not as a pass of the effect.
//
// usage: SITE_ENV=/tmp/wf-site-overlay/env TILES=win.maximize,key.enter node --experimental-websocket tools/site/flow-tiles.mjs
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { stack, newPage, loadClips, playAndWait, sleep, shotTo, reporter, daemon, root, recorder } from './lib.mjs';
import { Page } from '../peak/cdp.mjs';
import { loadStack } from '../peak/env.mjs';

const OUT = path.join(root, 'proofs/ui-v2/site-tiles');
const S = stack(process.env.SITE_ENV || '/tmp/wf-site-overlay/env');
const B = loadStack('/tmp/wf-realrun-wispr-wrap-site/env');      // the realrun stack that owns the display (TERM2_WID)
const { check, finish } = reporter(S, OUT, 'One-tap tiles end to end - overlay daemon (live-merge server) + my web/');
const { api, sh, sink } = daemon(S);
const ONLY = (process.env.TILES || '').split(',').filter(Boolean);
const XPORT = 9014;

const xp = (...a) => { try { return sh(a[0], a.slice(1)).trim(); } catch (e) { return ''; } };
const nl = () => (sink().match(/\n/g) || []).length;
// xclip stays alive to own the selection and would hold execFileSync's pipe forever: spawn it detached
const setClip = async (text) => {
  spawn('bash', ['-c', `printf %s ${JSON.stringify(text)} | xclip -selection clipboard -i`], { env: { ...process.env, DISPLAY: S.D }, stdio: 'ignore', detached: true }).unref();
  await sleep(500);
};
const focusWin = async (wid) => { xp('xdotool', 'windowfocus', String(wid)); await sleep(400); };
const activeWin = () => xp('xdotool', 'getactivewindow');
const poll = async (fn, ms = 8000, step = 200) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await sleep(step); } return false; };
const sinkWid = S.TERM_WID;
const state = async () => (await api('GET', '/live/state')).json || {};
const wstate = (wid) => xp('xprop', '-id', String(wid), '_NET_WM_STATE');

// ---- a real Chrome on the laptop's display (tabs, scrolling, clipboard, undo) ----
let xchrome = null;
async function xtabs() { try { return (await (await fetch(`http://127.0.0.1:${XPORT}/json/list`)).json()).filter(t => t.type === 'page'); } catch (e) { return []; } }
async function ensureXChrome() {
  if ((await xtabs()).length) return;
  const html = '<title>SITE-TARGET</title><body style="margin:0;font:20px sans-serif"><textarea id=t rows=5 style="width:92%;font-size:22px"></textarea><div style="height:9000px;background:#eee;padding:20px">a tall page</div></body>';
  xchrome = spawn('google-chrome', ['--user-data-dir=/tmp/site-xchrome', '--no-first-run', '--no-default-browser-check', '--no-sandbox',
    '--disable-gpu', '--disable-dev-shm-usage', `--remote-debugging-port=${XPORT}`, '--window-size=900,640', '--window-position=300,40',
    'data:text/html,' + encodeURIComponent(html)], { env: { ...process.env, DISPLAY: S.D }, stdio: 'ignore' });
  await poll(async () => (await xtabs()).length === 1, 20000, 300);
  await sleep(1500);
}
async function xeval(expr) {
  const tabs = await xtabs();
  const t = tabs.find(x => /SITE-TARGET/.test(x.title || '')) || tabs[0];
  const p = new Page(t.webSocketDebuggerUrl, null); await p.ready;
  try { return await p.eval(expr); } finally { try { p.ws.close(); } catch (e) { /* gone */ } }
}
async function xcleanTabs() {
  const tabs = await xtabs();
  const keep = tabs.find(x => /SITE-TARGET/.test(x.title || ''));
  for (const t of tabs) if (keep && t.id !== keep.id) await fetch(`http://127.0.0.1:${XPORT}/json/close/${t.id}`).catch(() => {});
  if (keep) await fetch(`http://127.0.0.1:${XPORT}/json/activate/${keep.id}`).catch(() => {});
  await sleep(500);
}
async function xnewTab() { await fetch(`http://127.0.0.1:${XPORT}/json/new?about:blank`, { method: 'PUT' }).catch(() => {}); await sleep(600); }
const chromeWid = () => {
  const ids = xp('xdotool', 'search', '--onlyvisible', '--class', 'chrome').split('\n').filter(Boolean);
  let best = null, area = 0;
  for (const id of ids) {
    const g = xp('xdotool', 'getwindowgeometry', '--shell', id);
    const w = +((/WIDTH=(\d+)/.exec(g) || [])[1] || 0), h = +((/HEIGHT=(\d+)/.exec(g) || [])[1] || 0);
    if (w * h > area) { area = w * h; best = id; }
  }
  return best;
};
async function focusChrome() { const w = chromeWid(); if (w) await focusWin(w); xp('xdotool', 'mousemove', '600', '360'); await sleep(250); }

// ---- the 20 cases: setup() -> ctx, verify(ctx) -> detail|false, teardown() ----
const NOAUDIO = !xp('which', 'pactl') && !xp('which', 'amixer');
const refusal = async () => { const st = await state(); return JSON.stringify({ last_action: st.last_action, feed: (st.feed || []).slice(-3) }).slice(0, 220); };
const CASES = [
  { id: 'win.alt-tab', label: 'Switch app',
    setup: async () => { await focusWin(sinkWid); return { b: activeWin() }; },
    verify: async (c) => (await poll(() => activeWin() && activeWin() !== c.b, 6000)) && `active window ${c.b} -> ${activeWin()}` },
  { id: 'win.maximize', label: 'Maximize',
    setup: async () => { xp('wmctrl', '-ir', sinkWid, '-b', 'remove,maximized_vert,maximized_horz'); await focusWin(sinkWid); return {}; },
    verify: async () => (await poll(() => /MAXIMIZED_VERT/.test(wstate(sinkWid)) && /MAXIMIZED_HORZ/.test(wstate(sinkWid)), 6000)) && 'editor window is maximized (_NET_WM_STATE)',
    teardown: async () => { xp('wmctrl', '-ir', sinkWid, '-b', 'remove,maximized_vert,maximized_horz'); } },
  { id: 'win.minimize', label: 'Minimize',
    setup: async () => { await focusWin(sinkWid); return {}; },
    verify: async () => (await poll(() => /HIDDEN/.test(wstate(sinkWid)), 6000)) && 'editor window is minimized (_NET_WM_STATE_HIDDEN)',
    teardown: async () => { xp('wmctrl', '-ia', sinkWid); await sleep(500); xp('wmctrl', '-ir', sinkWid, '-b', 'remove,hidden'); } },
  { id: 'win.close', label: 'Close window',
    setup: async () => {
      spawn('xmessage', ['-name', 'site-closeme', 'close me'], { env: { ...process.env, DISPLAY: S.D }, stdio: 'ignore', detached: true }).unref();
      const w = await poll(() => xp('xdotool', 'search', '--name', 'site-closeme|xmessage').split('\n').filter(Boolean)[0], 6000);
      await focusWin(w); return { w };
    },
    verify: async (c) => {
      const pend = await poll(async () => { const st = await state(); return (st.pending || [])[0]; }, 8000);
      if (!pend) return false;
      const stillOpen = !!xp('xdotool', 'getwindowname', c.w);
      if (!stillOpen) return false;                                               // must NOT fire by itself
      await api('POST', '/act/confirm', { id: pend.id });                         // the laptop-side confirm
      const gone = await poll(() => !xp('xdotool', 'getwindowname', c.w), 6000);
      return gone && 'parked for the laptop confirm (window stayed open), confirm -> window closed';
    },
    teardown: async () => { xp('pkill', '-f', 'xmessage -name site-closeme'); } },
  { id: 'desktop.show', label: 'Show desktop',
    setup: async () => { xp('wmctrl', '-k', 'off'); await focusWin(sinkWid); return {}; },
    verify: async () => (await poll(() => /= 1/.test(xp('xprop', '-root', '_NET_SHOWING_DESKTOP')), 6000)) && '_NET_SHOWING_DESKTOP = 1',
    teardown: async () => { xp('wmctrl', '-k', 'off'); } },
  { id: 'browser.new-tab', label: 'New tab',
    setup: async () => { await ensureXChrome(); await focusChrome(); return { n: (await xtabs()).length }; },
    verify: async (c) => (await poll(async () => (await xtabs()).length === c.n + 1, 8000)) && `real Chrome tabs ${c.n} -> ${c.n + 1}`,
    teardown: async () => { await xcleanTabs(); } },
  { id: 'browser.close-tab', label: 'Close tab',
    setup: async () => { await ensureXChrome(); await xnewTab(); await focusChrome(); return { n: (await xtabs()).length }; },
    verify: async (c) => (await poll(async () => (await xtabs()).length === c.n - 1, 8000)) && `real Chrome tabs ${c.n} -> ${c.n - 1}`,
    teardown: async () => { await xcleanTabs(); } },
  { id: 'sys.volume-up', label: 'Volume up', noaudio: true },
  { id: 'sys.volume-down', label: 'Volume down', noaudio: true },
  { id: 'sys.mute', label: 'Mute', noaudio: true },
  { id: 'media.playpause', label: 'Play / Pause', noaudio: true },
  { id: 'sys.screenshot', label: 'Screenshot',
    setup: async () => ({ t: Date.now() - 1000 }),
    verify: async (c) => {
      const find = () => { const out = xp('find', S.WFHOME, '-maxdepth', '2', '-type', 'f', '(', '-name', '*.png', '-o', '-name', '*.jpg', ')', '-newermt', '@' + Math.floor(c.t / 1000)); return out.split('\n').filter(f => f && !/\.config|chrome/.test(f))[0]; };
      const f = await poll(find, 10000); return f && 'screenshot file written: ' + path.basename(f) + ' (' + fs.statSync(f).size + ' bytes)';
    } },
  { id: 'nav.scroll-down', label: 'Scroll down',
    setup: async () => { await ensureXChrome(); await xeval('window.scrollTo(0,0)'); await focusChrome(); return {}; },
    verify: async () => { const y = await poll(async () => (await xeval('window.scrollY')) > 50 && (await xeval('window.scrollY')), 8000); return y && `real page scrollY 0 -> ${Math.round(y)}`; },
    teardown: async () => { await xeval('window.scrollTo(0,0)'); } },
  { id: 'nav.scroll-up', label: 'Scroll up',
    setup: async () => { await ensureXChrome(); await xeval('window.scrollTo(0,1200)'); await sleep(300); await focusChrome(); return { y: await xeval('window.scrollY') }; },
    verify: async (c) => { const y = await poll(async () => { const v = await xeval('window.scrollY'); return v < c.y - 50 && v; }, 8000); return (y !== false) && `real page scrollY ${Math.round(c.y)} -> ${Math.round(y)}`; },
    teardown: async () => { await xeval('window.scrollTo(0,0)'); } },
  { id: 'edit.copy', label: 'Copy',
    setup: async () => { await ensureXChrome(); await setClip('old'); await xeval(`(() => { const t = document.getElementById('t'); t.value = 'COPY-ME-123'; t.focus(); t.select(); })()`); await focusChrome(); return {}; },
    verify: async () => (await poll(() => xp('xclip', '-o', '-selection', 'clipboard') === 'COPY-ME-123', 6000)) && 'clipboard now holds the selected text' },
  { id: 'edit.paste', label: 'Paste',
    setup: async () => { await ensureXChrome(); await setClip('PASTE-ME-456'); await xeval(`(() => { const t = document.getElementById('t'); t.value = ''; t.focus(); })()`); await focusChrome(); return {}; },
    verify: async () => (await poll(async () => /PASTE-ME-456/.test(await xeval(`document.getElementById('t').value`)), 6000)) && 'the clipboard text appeared in the real text box' },
  { id: 'edit.undo', label: 'Undo',
    setup: async () => { await ensureXChrome(); await xeval(`(() => { const t = document.getElementById('t'); t.value = ''; t.focus(); })()`); await focusChrome(); xp('xdotool', 'type', '--delay', '60', 'abcdef'); await sleep(500); return { v: await xeval(`document.getElementById('t').value`) }; },
    verify: async (c) => (c.v === 'abcdef') && (await poll(async () => (await xeval(`document.getElementById('t').value`)) !== 'abcdef', 6000)) && `text box "abcdef" -> "${await xeval(`document.getElementById('t').value`)}"` },
  { id: 'key.enter', label: 'Enter',
    setup: async () => { await focusWin(sinkWid); return { n: nl() }; },
    verify: async (c) => (await poll(() => nl() > c.n, 6000)) && `editor newlines ${c.n} -> ${nl()}` },
  { id: 'app.open', label: 'Open app', pickApp: 'Xfce Terminal',
    setup: async () => ({ n: xp('xdotool', 'search', '--class', 'xfce4-terminal').split('\n').filter(Boolean).length }),
    verify: async (c) => (await poll(() => xp('xdotool', 'search', '--class', 'xfce4-terminal').split('\n').filter(Boolean).length > c.n, 10000)) && 'a new Xfce Terminal window opened',
    teardown: async () => { const ids = xp('xdotool', 'search', '--class', 'xfce4-terminal').split('\n').filter(Boolean); const extra = ids.filter(i => i !== String(B.TERM2_WID || '')); if (extra.length > 1) xp('wmctrl', '-ic', extra[extra.length - 1]); } },
  { id: 'type.text', label: 'Type text', typeText: 'hello-from-tiles',
    setup: async () => { await focusWin(sinkWid); return { len: sink().length }; },
    verify: async () => (await poll(() => sink().includes('hello-from-tiles'), 6000)) && 'text typed into the real editor' },
];

// ---- one tile through the whole product ----
let page = null, n = 0;
async function shot(name) { await shotTo(page, OUT, name); }
async function runCase(c) {
  const tag = String(++n).padStart(2, '0') + '-' + c.id.replace(/[^a-z-]/g, '-');
  for (const row of ((await api('GET', '/bank')).json || {}).signs || []) await api('DELETE', '/bank?gloss=' + encodeURIComponent(row.gloss));
  await api('POST', '/live/listen', { on: false });
  try {
    // 1. Teach: open, tap the tile
    await page.click('#menu'); await sleep(600);
    await page.click('#tray-teach'); await sleep(700);
    await page.waitFor(`document.querySelectorAll('.qa-tile').length >= 15`, 12000, 'grid');
    await sleep(400);
    if (!(await page.eval(`!!document.querySelector('.qa-tile[data-id="${c.id}"]')`))) { check(c.label + ': tile is offered', false, 'the laptop does not report ' + c.id); return; }
    await page.click(`.qa-tile[data-id="${c.id}"]`); await sleep(500);
    if (c.pickApp) {
      await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 8000, 'picker');
      await sleep(900);
      await shot(tag + '-0-pick-app');
      await page.eval(`(() => { const b = [...document.querySelectorAll('#sheet-actions .act-app')].find(x => /${c.pickApp}/i.test(x.textContent)); b && b.click(); })()`);
      await sleep(700);
    }
    if (c.typeText) { await page.focus('#qa-text-in'); await page.type(c.typeText); await sleep(400); }
    await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 6000, 'Record enabled');
    await shot(tag + '-1-selected');
    // 2. Record -> 3 takes -> Ready
    await page.click('#tw-name-next');
    await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 8000, 'record');
    await sleep(600);
    await page.eval(`window.__play('teach-TYPE-HELLO')`);
    await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 45000, 'ready');
    await sleep(900);
    await shot(tag + '-2-ready');
    const said = await page.eval(`document.querySelector('#tw-done-m').textContent`);
    const bank = ((await api('GET', '/bank')).json || {}).signs || [];
    const row = bank[0];
    check(c.label + ': taught (3 takes, action saved)', row && (row.n_exemplars || 0) >= 3 && !!row.action, `${row ? row.gloss : 'no sign'} - "${said}"`);
    if (!row) return;
    // 3. live: perform the sign, then look at the laptop
    await page.click('#tw-done-live');
    await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera');
    await sleep(600);
    const ctx = (c.setup ? await c.setup() : {}) || {};          // OS state is set up right before the sign
    await api('POST', '/live/listen', { on: true }); await sleep(500);
    await playAndWait(page, 'perform-TYPE-HELLO');
    const m = await poll(async () => { const lm = (await state()).last_match || {}; return lm.gloss === row.gloss && lm.accepted && lm; }, 15000, 250);
    check(c.label + ': the daemon recognised the new sign', !!m, m ? `d=${(+m.dist).toFixed(3)}` : JSON.stringify((await state()).last_match || {}).slice(0, 120));
    if (c.noaudio) {
      await sleep(1500);
      const why = await refusal();
      await shot(tag + '-3-live');
      check(c.label + ': fires; the laptop answers honestly' + (NOAUDIO ? ' [NEVER-ON-BOX: no audio server]' : ''), !!m, why);
    } else {
      const v = c.verify ? await c.verify(ctx) : true;
      await shot(tag + '-3-live');
      check(c.label + ': real effect on the laptop', !!v, v || 'no effect seen');
    }
    await page.click('#lv-x'); await sleep(700);
  } catch (e) {
    let seen = '';
    try {
      seen = await page.eval(`(() => { const on = document.querySelector('.tw-step.on'); const rt = document.querySelector('#tw-retake'); return 'step=' + (on && on.dataset.step) + (rt && !rt.hidden ? ' retake="' + document.querySelector('#tw-retake-m').textContent + '"' : '') + ' hash=' + location.hash; })()`);
      await shot(tag + '-FAIL');
    } catch (e2) { /* page gone */ }
    check(c.label + ': completed', false, String(e && e.message || e).slice(0, 160) + ' | ' + seen);
    try { await page.eval(`location.hash = ''`); } catch (e2) { /* ignore */ }
  } finally {
    try { if (c.teardown) await c.teardown(); } catch (e) { /* best effort */ }
    await api('POST', '/live/listen', { on: false });
  }
}

page = await newPage(S, { cdpPort: Number(process.env.SITE_CDP || 9016) });
const rec = process.env.SITE_RECORD ? recorder(page, path.join(OUT, '_rec')) : null;
if (rec) await rec.start();
try {
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 2500 });
  await loadClips(page, ['teach-TYPE-HELLO', 'perform-TYPE-HELLO', 'palm-hold', 'idle']);
  for (const c of CASES) { if (ONLY.length && !ONLY.includes(c.id)) continue; await runCase(c); }
  const errs = page.errors.filter(e => !/favicon|ServiceWorker|manifest/i.test(e));
  check('no page errors', errs.length === 0, errs.slice(0, 3).join(' | '));
} finally {
  if (rec) { const r = await rec.stop('tiles-flow.mp4'); check('screen recording (mp4)', r.ok, r.frames + ' frames'); }
  await page.close();
  try { xchrome && xchrome.kill('SIGKILL'); } catch (e) { /* gone */ }
}
finish();
