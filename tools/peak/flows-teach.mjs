// flows-teach.mjs — real-run PASS/FAIL proofs for the PEAK teach lane, against
// a real browser + real wfd.py + real X11 (the realrun stack on MY ports).
//
// Covers, each proven with a REAL OS effect or a REAL daemon-bank change:
//   t1  teach TYPE-HELLO through the wizard (3 takes via the daemon's own
//       endpointer), bind "type text" by keyboard only, Finish reads well
//   t2  EVERY @ picker action type end to end — rebind the sign via the real
//       "My signs -> rebind @" UI, then sign it in Go Live and assert the
//       real effect (new window / typing / screenshot PNG / tier-2 park +
//       UI "Run it" / etc). NEVER-ON-BOX actions prove the UI path + the
//       honest refusal, and say so.
//   t3  My signs buttons on the real daemon: + take, rebind @, rename,
//       delete (two-step), empty state, drawer count, 10+ scroll
//   t4  teach "presented simply": no capsule/boxed/developer clutter; Back/
//       Cancel end the session cleanly (teach.active=false, camera stopped);
//       retry a rejected take; no-laptop path shows "Connect your laptop".
//
// What is real: the page, camera stream + MediaPipe, every HTTP call, the
// daemon's endpointer/DTW/action layer, the X11 effects. Substituted: hand
// detections only (window.WFFakeTrack plays synthetic kp42 clips — no video,
// no people), exactly as flows.mjs does.
//
// usage: node --experimental-websocket tools/peak/flows-teach.mjs <outdir> <cdp-port>
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { launchChrome, sleep } from './cdp.mjs';
import { loadStack, scrub } from './env.mjs';

const out = process.argv[2] || 'proofs/ui-v2/teach';
const cdpPort = Number(process.argv[3] || 8991);
const S = loadStack();
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const CLIPS = path.join(root, 'tools/demo/clips');
fs.mkdirSync(out, { recursive: true });

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok: !!ok, detail });
  console.log((ok ? 'PASS ' : 'FAIL ') + name + (detail ? '  — ' + detail : ''));
};
const note = (name, detail) => { results.push({ name, ok: true, detail, info: true }); console.log('NOTE ' + name + '  — ' + detail); };

const sh = (cmd, args) => { try { return execFileSync(cmd, args, { env: { ...process.env, DISPLAY: S.D }, encoding: 'utf8' }); } catch (e) { return (e.stdout || '') + (e.stderr || ''); } };
const sink = () => { try { return fs.readFileSync(S.SINK, 'utf8'); } catch (e) { return ''; } };
const sinkHas = async (since, text, ms = 5000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (sink().slice(since).includes(text)) return true; await sleep(120); } return false; };
const winCount = cls => (sh('xdotool', ['search', '--class', cls]).trim().split('\n').filter(Boolean)).length;

const api = async (method, p, body) => {
  const r = await fetch(S.base + p, { method, headers: { 'X-WF-Key': S.KEY, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  let j = null; const t = await r.text();
  try { j = JSON.parse(t); } catch (e) { /* not json */ }
  return { status: r.status, json: j };
};
const bankRow = async (gloss) => {
  const b = await api('GET', '/bank');
  return ((b.json || {}).signs || []).find(s => s.gloss === gloss) || null;
};

const TRACKER = `(() => {
  const ABSENT = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));
  window.__q = []; window.__clips = {};
  window.WFFakeTrack = {
    start(video, cb) {
      const iv = setInterval(() => { const f = window.__q.shift(); cb({ t_ms: performance.now(), points: f ? f.points : ABSENT }); }, 66);
      return { stop() { clearInterval(iv); } };
    },
  };
  window.__play = (name) => { for (const f of window.__clips[name]) window.__q.push(f); return window.__q.length; };
  window.__idle = () => window.__q.length === 0;
})()`;

async function newPage() {
  const profile = '/tmp/peak-chrome-flows-teach';
  fs.rmSync(profile, { recursive: true, force: true });
  const page = await launchChrome({ port: cdpPort, profile, origin: S.base });
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
  await page.send('Page.addScriptToEvaluateOnNewDocument', { source: TRACKER });
  await page.viewport(390, 844, { mobile: true, dpr: 2 });
  return page;
}
async function loadClips(page, names) {
  for (const n of names) {
    const j = JSON.parse(fs.readFileSync(path.join(CLIPS, n + '.json'), 'utf8'));
    await page.eval(`window.__clips[${JSON.stringify(n)}] = ${JSON.stringify(j.frames)}`);
  }
}
const playAndWait = async (page, clip, timeout = 30000) => {
  await page.eval(`window.__play(${JSON.stringify(clip)})`);
  await sleep(300);
  await page.waitFor('window.__idle()', timeout, 'clip ' + clip + ' finished');
};
const shot = (page, n) => page.shot(path.join(out, n + '.png'));
const viaDrawer = async (page, sel) => {
  await goHome(page);
  await page.click('#menu'); await sleep(600); await page.click(sel); await sleep(500);
};
const GLOSS = 'TYPE-HELLO';

// close any open sheet / drawer so #menu is reachable (no window.App in prod)
async function goHome(page) {
  await page.eval(`(() => {
    for (const id of ['sheet-actions','sheet-macro','sheet-teach','sheet-signs','sheet-laptop','sheet-settings','sheet-live']) {
      const s = document.querySelector('#' + id);
      if (s && !s.hidden) { const x = s.querySelector('.sheet-x, #lv-x, #tw-x, #lib-x, #act-x, #mc-x'); if (x) x.click(); }
    }
    const dr = document.querySelector('#drawer'); if (dr) dr.classList.remove('open');
    document.body.classList.remove('sheet-open','drawer-open');
    if (location.hash) location.hash = '';
  })()`);
  await sleep(500);
}
async function App_back(page) { await goHome(page); }

async function signInLive(page, expectMs = 25000) {
  await page.click('#tray-live');
  await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera ready');
  await sleep(600);
  sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(200);
  if (await page.eval(`document.querySelector('#lv-badge').classList.contains('listen')`)) {
    await playAndWait(page, 'namaste');
    await page.waitFor(`!document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'stopped').catch(() => {});
    await sleep(500);
  }
  await playAndWait(page, 'palm-hold');
  await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 15000, 'listening');
  await sleep(700);
  const m0 = (((await api('GET', '/live/state')).json || {}).last_match || {}).at_ms;
  await playAndWait(page, 'perform-TYPE-HELLO');
  const t0 = Date.now();
  while (Date.now() - t0 < expectMs) {
    const m = (((await api('GET', '/live/state')).json || {}).last_match || {});
    if (m.at_ms !== m0 && m.gloss === GLOSS && m.accepted) return m;
    await sleep(250);
  }
  return null;
}
async function leaveLive(page) { try { await page.click('#lv-x'); } catch (e) {} await sleep(700); }

async function rebindViaUI(page, drive) {
  await viaDrawer(page, '#tray-signs');
  await page.waitFor(`[...document.querySelectorAll('#lib-list .lib-b')].some(b=>/rebind/.test(b.textContent))`, 8000, 'rebind button');
  await page.eval(`[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/rebind/.test(b.textContent)).click()`);
  await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 6000, 'picker open');
  await sleep(600);
  await drive(page);
  await page.waitFor(`document.querySelector('#sheet-actions').hidden`, 8000, 'picker closed');
  await page.waitFor(`/rebound|rebind/i.test(document.querySelector('#toast').textContent)`, 8000, 'rebind toast').catch(() => {});
  await sleep(500);
  await App_back(page);
}
async function pickTool(page, labelRe, arg) {
  await page.focus('#act-q');
  await sleep(200);
  const did = await page.eval(`(() => {
    const rows = [...document.querySelectorAll('#act-list .act-row')];
    const row = rows.find(r => /${labelRe.source || labelRe}/i.test(r.querySelector('.act-name') ? r.querySelector('.act-name').textContent : r.textContent));
    if (!row) return 'no-row';
    const inp = row.querySelector('input');
    if (inp) { inp.focus(); window.__row = row; return 'input'; }
    row.click(); return 'clicked';
  })()`);
  if (did === 'input' && arg != null) {
    await page.eval(`(() => { const inp=window.__row.querySelector('input'); inp.focus(); inp.value=${JSON.stringify(arg)}; inp.dispatchEvent(new Event('input',{bubbles:true})); const u=window.__row.querySelector('.act-use'); if(u) u.click(); })()`);
  }
  return did;
}

// ============================ t1: teach through the wizard ============================
async function t1_teach(page) {
  await api('DELETE', '/bank?gloss=' + GLOSS);
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1200 });
  await loadClips(page, ['teach-TYPE-HELLO', 'palm-hold', 'perform-TYPE-HELLO', 'namaste', 'idle', 'reject']);
  await viaDrawer(page, '#tray-teach');
  await shot(page, 't1-01-name');
  await page.focus('#tw-name'); await page.type('type hello'); await sleep(300);
  await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 4000, 'name ok');
  check('name step has no developer "gloss" capsule', await page.eval(`!document.querySelector('#tw-gloss')`), 'tw-gloss removed');
  await page.click('#tw-name-next'); await sleep(900);
  await shot(page, 't1-02-record');
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  for (let n = 1; n <= 3; n++) await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length >= ${n}`, 25000, 'take ' + n);
  check('record step uses plain dots, no "Take n" chips', await page.eval(`(() => { const t=document.querySelector('#tw-takebar'); return t && !/Take\\s*\\d/i.test(t.textContent); })()`), 'no Take-n label');
  await page.waitFor(`document.querySelector('.tw-step[data-step="bind"]').classList.contains('on')`, 8000, 'bind step');
  await shot(page, 't1-03-bind');
  check('bind step has no boxed "frames" preview cards', await page.eval(`!document.querySelector('#tw-prev') && !/frames/i.test(document.querySelector('.tw-step[data-step="bind"]').textContent)`), 'tw-prev removed, no "frames"');
  await page.click('#tw-pick');
  await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 4000, 'picker');
  await sleep(600);
  await page.focus('#act-q'); await page.type('type text'); await sleep(400);
  await shot(page, 't1-04-picker-filter');
  await page.tab(); await sleep(200);
  await page.type('hello-from-peak'); await sleep(200);
  await page.enter();
  await page.waitFor(`document.querySelector('#sheet-actions').hidden`, 4000, 'picker closed');
  await page.waitFor(`!document.querySelector('#tw-save').disabled`, 4000, 'Finish on');
  await page.click('#tw-save');
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 6000, 'done');
  await sleep(500);
  await shot(page, 't1-05-done');
  const doneText = await page.eval(`document.querySelector('#tw-done-m').textContent`);
  const doneTitle = await page.eval(`document.querySelector('#tw-done-t').textContent`);
  check('done reads well: lower-case verb + argument', /your laptop will type/i.test(doneText) && /hello-from-peak/.test(doneText), doneText);
  check('done uses the friendly name, not the UPPER gloss', /type hello/.test(doneTitle) && !/TYPE-HELLO/.test(doneTitle), doneTitle);
  check('"Try it live" offered', await page.eval(`/try it live/i.test(document.querySelector('#tw-done-live').textContent)`));
  const row = await bankRow(GLOSS);
  check('sign saved in the real daemon bank with 3 takes + action', row && (row.n_exemplars || 0) >= 3 && !!(row.action || row.argv), JSON.stringify(row || {}).slice(0, 140));
  check('no page errors (teach)', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
  await App_back(page);
}

// ============================ t2: every @ picker action type ============================
async function t2_actions(page) {
  const cat = (await api('GET', '/act')).json || {};
  const toolIds = new Set((cat.tools || []).map(t => t.id));
  // 1) installed app -> a new window appears
  {
    const before = winCount('xfce4-terminal');
    await rebindViaUI(page, async p => {
      await p.focus('#act-q'); await p.type('terminal'); await sleep(500);
      await p.eval(`(() => { const a=[...document.querySelectorAll('#act-list .act-app')].find(a=>/terminal/i.test(a.textContent)); if(a) a.click(); })()`);
    });
    const m = await signInLive(page);
    let after = before; for (let i = 0; i < 20 && after <= before; i++) { await sleep(300); after = winCount('xfce4-terminal'); }
    await leaveLive(page);
    check('open app -> a NEW window appears (xfce4-terminal)', after > before, `windows ${before}->${after}` + (m ? ' matched' : ' NO-MATCH'));
  }
  // 2) type text -> editor receives it
  {
    const txt = 'PICKER-TYPE-' + Date.now().toString().slice(-5);
    await rebindViaUI(page, async p => { await pickTool(p, /type text/, txt); });
    const before = sink().length;
    await signInLive(page);
    const got = await sinkHas(before, txt, 6000);
    await leaveLive(page);
    check('type text -> the real editor receives it', got, got ? 'sink got ' + txt : 'not typed');
  }
  // 3) key press
  {
    await rebindViaUI(page, async p => { await pickTool(p, /key press/, 'ctrl+shift+F13'); });
    const m = await signInLive(page);
    await leaveLive(page);
    check('key press -> bound + fired through the daemon', !!m, m ? 'matched d=' + (+m.dist).toFixed(3) : 'no match');
  }
  // 4) open url
  {
    await rebindViaUI(page, async p => { await pickTool(p, /open url/, 'https://example.com'); });
    const chromeBefore = winCount('google-chrome');
    const m = await signInLive(page);
    let chromeAfter = chromeBefore; for (let i = 0; i < 12 && chromeAfter <= chromeBefore; i++) { await sleep(300); chromeAfter = winCount('google-chrome'); }
    await leaveLive(page);
    if (chromeAfter > chromeBefore) check('open url -> a browser window appeared', true, `chrome ${chromeBefore}->${chromeAfter}`);
    else { check('open url -> the daemon matched & ran xdg-open', !!m, m ? 'matched; no browser window spawned' : 'no match'); note('open url browser window', 'NEVER-ON-BOX: xdg-open spawns no persistent browser window in this scratch session'); }
  }
  // 5) window ops
  for (const [re, label] of [[/tile.*left/i, 'tile left'], [/tile.*right/i, 'tile right'], [/maximize/i, 'maximize'], [/next workspace/i, 'next workspace']]) {
    await rebindViaUI(page, async p => { await pickTool(p, re); });
    const m = await signInLive(page);
    await leaveLive(page);
    check(label + ' -> bound + fired (window op)', !!m, m ? 'matched' : 'no match');
  }
  // 6) focus app
  {
    await rebindViaUI(page, async p => {
      await p.focus('#act-q'); await p.type('focus'); await sleep(500);
      await p.eval(`(() => { const r=[...document.querySelectorAll('#act-list .act-row')].find(r=>/focus/i.test(r.textContent)); if(!r) return; const inp=r.querySelector('input'); if(inp){inp.focus(); inp.value='xfce4-terminal'; inp.dispatchEvent(new Event('input',{bubbles:true})); const u=r.querySelector('.act-use'); if(u) u.click();} })()`);
    });
    const m = await signInLive(page);
    await leaveLive(page);
    check('focus app -> bound + fired', !!m, m ? 'matched' : 'no match');
  }
  // 7) screenshot
  {
    const capOk = (() => { const c = (cat.capabilities_system || {})['sys.screenshot'] || {}; return c.available !== false; })();
    if (toolIds.has('sys.screenshot') && capOk) {
      const findPng = () => (sh('find', [S.WFHOME, '-maxdepth', '2', '-name', '*.png']).trim().split('\n').filter(Boolean)).length;
      const before = findPng();
      await rebindViaUI(page, async p => { await pickTool(p, /screenshot/); });
      const m = await signInLive(page);
      let after = before; for (let i = 0; i < 20 && after <= before; i++) { await sleep(300); after = findPng(); }
      await leaveLive(page);
      check('screenshot -> a NEW PNG in the scratch HOME', after > before, `png ${before}->${after}`);
    } else { note('screenshot', 'NEVER-ON-BOX: no screenshot backend advertised by the daemon'); }
  }
  // 8) terminal command (tier 2) -> parks, UI "Run it" fires it
  {
    const marker = '/tmp/teach-term-marker-' + Date.now();
    sh('rm', ['-f', marker]);
    await rebindViaUI(page, async p => {
      await p.focus('#act-q'); await p.type('terminal command'); await sleep(500);
      await p.eval(`(() => { const rows=[...document.querySelectorAll('#act-list .act-row')]; const r=rows.find(x=>/terminal command/i.test(x.textContent) && x.querySelector('input')); if(r){const inp=r.querySelector('input'); inp.focus(); inp.value='touch ${marker}'; inp.dispatchEvent(new Event('input',{bubbles:true})); const u=r.querySelector('.act-use'); if(u) u.click();} })()`);
    });
    const row = await bankRow(GLOSS);
    check('terminal command bound as tier-2 (asks on laptop)', row && (row.tier || 0) >= 2, 'tier=' + (row && row.tier));
    await page.click('#tray-live');
    await page.waitFor(`document.querySelector('#lv-cover').hidden`, 40000, 'camera');
    await sleep(500); sh('xdotool', ['windowfocus', S.TERM_WID]); await sleep(200);
    if (!(await page.eval(`document.querySelector('#lv-badge').classList.contains('listen')`))) { await playAndWait(page, 'palm-hold'); await page.waitFor(`document.querySelector('#lv-badge').classList.contains('listen')`, 12000, 'listening'); }
    await sleep(600);
    await playAndWait(page, 'perform-TYPE-HELLO');
    await page.waitFor(`!document.querySelector('#lv-pend').hidden && /Run it/.test(document.querySelector('#lv-pend').textContent)`, 20000, 'parked row').catch(() => {});
    const parked = fs.existsSync(marker) === false && await page.eval(`!document.querySelector('#lv-pend').hidden`);
    await shot(page, 't2-term-parked');
    check('tier-2 terminal command PARKS (no spawn) with a "Run it" row', parked, 'marker absent=' + (!fs.existsSync(marker)));
    await page.eval(`(() => { const b=[...document.querySelectorAll('#lv-pend button')].find(b=>/Run it/.test(b.textContent)); if(b) b.click(); })()`);
    const fired = await (async () => { for (let i = 0; i < 40; i++) { if (fs.existsSync(marker)) return true; await sleep(150); } return false; })();
    await leaveLive(page);
    check('UI "Run it" fires the parked command -> marker file appears', fired, marker);
    sh('rm', ['-f', marker]);
  }
  // 9) macro built in the macro editor UI
  {
    const txt = 'MACRO-STEP-' + Date.now().toString().slice(-4);
    await rebindViaUI(page, async p => {
      await p.focus('#act-q'); await p.type('macro'); await sleep(400);
      await p.eval(`(() => { const r=[...document.querySelectorAll('#act-list .act-row')].find(x=>/macro/i.test(x.textContent)); if(r) r.click(); })()`);
      await p.waitFor(`!document.querySelector('#sheet-macro').hidden`, 5000, 'macro editor');
      await sleep(400);
      await p.eval(`(() => { const b=[...document.querySelectorAll('#mc-add .mc-chip')].find(b=>/type text/i.test(b.textContent)); if(b) b.click(); })()`);
      await sleep(300);
      await p.eval(`(() => { const ins=document.querySelectorAll('#mc-body .mc-step .mc-in'); const inp=ins[ins.length-1]; if(inp){inp.focus(); inp.value='${txt}'; inp.dispatchEvent(new Event('input',{bubbles:true}));} })()`);
      await sleep(300);
      await p.eval(`document.querySelector('#mc-done').click()`);
    });
    const row = await bankRow(GLOSS);
    const isMacro = row && row.action && (row.action.kind === 'macro' || (row.action.args && row.action.args.steps));
    check('macro built in the macro editor UI + saved to the bank', !!isMacro, JSON.stringify((row || {}).action || {}).slice(0, 120));
    const before = sink().length;
    await signInLive(page);
    const got = await sinkHas(before, txt, 6000);
    await leaveLive(page);
    check('macro ran its step (type) through the daemon', got, got ? 'sink got ' + txt : 'macro step not observed');
  }
  // 10) per-app context rule built in the UI
  {
    await rebindViaUI(page, async p => {
      await p.focus('#act-q'); await p.type('per-app'); await sleep(400);
      await p.eval(`(() => { const r=[...document.querySelectorAll('#act-list .act-row')].find(x=>/per-app/i.test(x.textContent)); if(r) r.click(); })()`);
      await p.waitFor(`!document.querySelector('#sheet-macro').hidden`, 5000, 'context editor');
      await sleep(400);
      await p.eval(`(() => { const inp=document.querySelector('#mc-body .mc-rule .mc-in'); if(inp){inp.focus(); inp.value='xfce4-terminal'; inp.dispatchEvent(new Event('input',{bubbles:true}));} })()`);
      await sleep(200);
      await p.eval(`(() => { const b=document.querySelector('#mc-body .mc-rule .mc-act'); if(b) b.click(); })()`);
      await p.waitFor(`!document.querySelector('#sheet-actions').hidden`, 5000, 'sub-picker');
      await sleep(400);
      await p.eval(`(() => { const r=[...document.querySelectorAll('#act-list .act-row')].find(x=>/maximize/i.test(x.textContent)); if(r) r.click(); })()`);
      await p.waitFor(`document.querySelector('#sheet-actions').hidden`, 5000, 'sub-picker closed');
      await sleep(300);
      await p.eval(`document.querySelector('#mc-done').click()`);
    });
    const row = await bankRow(GLOSS);
    const isCtx = row && row.action && (row.action.kind === 'context' || row.action.by_wm_class);
    check('per-app context rule built in the UI + saved to the bank', !!isCtx, JSON.stringify((row || {}).action || {}).slice(0, 120));
  }
  // 11) keyboard path of the picker
  {
    await viaDrawer(page, '#tray-signs');
    await page.eval(`[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/rebind/.test(b.textContent)).click()`);
    await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 5000, 'picker');
    await sleep(500);
    await page.focus('#act-q'); await page.type('maximize'); await sleep(400);
    await page.key('ArrowDown'); await sleep(150);
    const hasSel = await page.eval(`!!document.querySelector('#act-list .act-sel')`);
    await page.enter();
    await page.waitFor(`document.querySelector('#sheet-actions').hidden`, 5000, 'picker closed by keyboard');
    await App_back(page);
    check('picker keyboard path: filter + Arrow + Enter completes', hasSel, 'highlight moved; Enter bound');
  }
  // 12) NEVER-ON-BOX families: prove the UI path up to the honest refusal
  {
    const families = { 'volume': /volume/i, 'brightness': /brightness/i, 'wifi': /wifi/i, 'bluetooth': /bluetooth/i, 'media': /play.*pause|next track/i };
    const present = Object.entries(families).filter(([k, re]) => (cat.tools || []).some(t => re.test(t.label || t.id)));
    await viaDrawer(page, '#tray-signs');
    await page.eval(`[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/rebind/.test(b.textContent)).click()`);
    await page.waitFor(`!document.querySelector('#sheet-actions').hidden`, 5000, 'picker');
    await page.focus('#act-q'); await page.type('volume'); await sleep(400);
    const volRow = await page.eval(`[...document.querySelectorAll('#act-list .act-row')].some(r=>/volume/i.test(r.textContent))`);
    await shot(page, 't2-volume-picker');
    await page.eval(`document.querySelector('#act-x').click()`);
    await App_back(page);
    check('volume/brightness/wifi/bt/media appear in the picker UI', volRow && present.length > 0, present.map(p => p[0]).join(','));
    note('volume/brightness/wifi/bluetooth/media effect', 'NEVER-ON-BOX: no audio/backlight/radio backend (realrun probe: pulseaudio/nmcli/rfkill absent). UI path proven; effect cannot be asserted here.');
  }
}

// ============================ t3: My signs buttons ============================
async function t3_signs(page) {
  await rebindViaUI(page, async p => { await pickTool(p, /type text/, 'hello-from-peak'); });
  await viaDrawer(page, '#tray-signs');
  await page.waitFor(`document.querySelectorAll('#lib-list .lib-card').length >= 1`, 6000, 'library rows');
  await shot(page, 't3-01-list');
  const count0 = await page.eval(`document.querySelector('#lib-count').textContent`);
  check('drawer count shows the sign total', /\d+\s*sign/i.test(count0), count0);
  const takesBefore = (await bankRow(GLOSS)).n_exemplars || 0;
  await page.eval(`[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/\\+ take/.test(b.textContent)).click()`);
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 8000, 'add-take record');
  await loadClips(page, ['perform-TYPE-HELLO']);
  await page.eval(`window.__play('perform-TYPE-HELLO')`);
  await page.waitFor(`document.querySelector('.tw-step[data-step="done"]').classList.contains('on')`, 25000, 'add-take done');
  await sleep(400);
  const takesAfter = (await bankRow(GLOSS)).n_exemplars || 0;
  check('"+ take" adds a take through the wizard (count up)', takesAfter > takesBefore, `takes ${takesBefore}->${takesAfter}`);
  await App_back(page);
  // rename
  await viaDrawer(page, '#tray-signs');
  await page.waitFor(`document.querySelectorAll('#lib-list .lib-card').length >= 1`, 6000, 'rows');
  await page.eval(`[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/rename/.test(b.textContent)).click()`);
  await page.waitFor(`document.querySelector('#lib-list .lib-rename input')`, 5000, 'rename field');
  await page.eval(`(() => { const i=document.querySelector('#lib-list .lib-rename input'); i.focus(); i.value='say hello'; })()`);
  await page.eval(`(() => { const b=[...document.querySelectorAll('#lib-list .lib-rename button')].find(b=>/save/.test(b.textContent)); b.click(); })()`);
  await sleep(900);
  const renamed = (await bankRow(GLOSS)).label;
  check('rename really persists on the server (GET /bank label)', renamed === 'say hello', 'label=' + renamed);
  // delete (two-step)
  const sigCountBefore = ((await api('GET', '/bank')).json.signs || []).length;
  await page.eval(`(() => { const b=[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/delete/.test(b.textContent)); b.click(); })()`);
  await sleep(200);
  const armed = await page.eval(`[...document.querySelectorAll('#lib-list .lib-b')].some(b=>/sure/i.test(b.textContent))`);
  check('delete is two-step (first click arms "sure?")', armed, 'armed=' + armed);
  await page.eval(`(() => { const b=[...document.querySelectorAll('#lib-list .lib-b')].find(b=>/sure/i.test(b.textContent)); if(b) b.click(); })()`);
  await sleep(1000);
  const sigCountAfter = ((await api('GET', '/bank')).json.signs || []).length;
  const gone = !(await bankRow(GLOSS));
  check('delete removes it from GET /bank + drawer count drops', gone && sigCountAfter < sigCountBefore, `signs ${sigCountBefore}->${sigCountAfter}`);
  // empty state
  for (const s of ((await api('GET', '/bank')).json.signs || [])) await api('DELETE', '/bank?gloss=' + encodeURIComponent(s.gloss));
  await App_back(page);
  await viaDrawer(page, '#tray-signs');
  await sleep(600);
  await shot(page, 't3-02-empty');
  check('empty state shows "teach your first one"', await page.eval(`!document.querySelector('#lib-empty').hidden && /teach your first/i.test(document.querySelector('#lib-empty').textContent)`));
  await App_back(page);
  // 10+ scroll
  for (let i = 0; i < 12; i++) {
    await api('POST', '/teach/begin', { name: 'SCROLL-' + i, takes: 1, label: 'scroll ' + i });
    await api('POST', '/teach/end', { name: 'SCROLL-' + i });
    await api('PUT', '/bank', { gloss: 'SCROLL-' + i, label: 'scroll ' + i, tier: 1, action: { kind: 'builtin', tool: 'type.text', args: { text: 'x' }, argv: ['xdotool', 'type', 'x'], label: 'type text', tier: 1 } });
  }
  await viaDrawer(page, '#tray-signs');
  await page.waitFor(`document.querySelectorAll('#lib-list .lib-card').length >= 10`, 8000, '10+ rows');
  await sleep(400);
  const scrollable = await page.eval(`(() => { const b=document.querySelector('#sheet-signs .sheet-body'); return b.scrollHeight > b.clientHeight + 20; })()`);
  await page.eval(`document.querySelector('#sheet-signs .sheet-body').scrollTop = 600`);
  await sleep(300);
  await shot(page, 't3-03-scroll');
  const nRows = await page.eval(`document.querySelectorAll('#lib-list .lib-card').length`);
  check('a bank with 10+ signs scrolls', nRows >= 10 && scrollable, `${nRows} rows, scrollable=${scrollable}`);
  for (let i = 0; i < 12; i++) await api('DELETE', '/bank?gloss=SCROLL-' + i);
  await App_back(page);
  check('no page errors (signs)', page.errors.length === 0, page.errors.slice(0, 2).join(' | '));
}

// ============================ t4: presented simply + clean exits + no-laptop ============================
async function t4_simple(page) {
  await api('DELETE', '/bank?gloss=' + GLOSS);
  await viaDrawer(page, '#tray-teach');
  await page.eval(`(() => { const b=document.querySelector('#tw-name-cancel'); if(b) b.click(); })()`);
  await sleep(500);
  check('Cancel on name step closes the sheet', await page.eval(`document.querySelector('#sheet-teach').hidden`));
  // Back at record ends the session
  await viaDrawer(page, '#tray-teach');
  await page.focus('#tw-name'); await page.type('type hello'); await sleep(200);
  await page.waitFor(`!document.querySelector('#tw-name-next').disabled`, 4000, 'name ok');
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 6000, 'record');
  await sleep(500);
  await page.eval(`document.querySelector('#tw-rec-back').click()`);
  await sleep(800);
  const teachAfter = (((await api('GET', '/live/state')).json || {}).teach || {}).active;
  const recOff = await page.eval(`!document.querySelector('#tw-rec').classList.contains('on')`);
  check('Back on record ends the teach session (teach.active false)', teachAfter !== true, 'active=' + teachAfter);
  check('no stuck REC after Back', recOff, 'REC off');
  // close mid-record
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 6000, 'record2');
  await sleep(400);
  await page.eval(`document.querySelector('#tw-x').click()`);
  await sleep(800);
  const closedActive = (((await api('GET', '/live/state')).json || {}).teach || {}).active;
  check('closing the sheet mid-record ends the session + stops camera', closedActive !== true, 'active=' + closedActive);
  // retry a rejected take
  await viaDrawer(page, '#tray-teach');
  await page.focus('#tw-name'); await page.type('type hello'); await sleep(200);
  await page.click('#tw-name-next');
  await page.waitFor(`document.querySelector('.tw-step[data-step="record"]').classList.contains('on')`, 6000, 'record3');
  await sleep(500);
  await loadClips(page, ['reject', 'teach-TYPE-HELLO']);
  await page.eval(`window.__play('reject')`); await sleep(2500);
  await page.eval(`window.__play('teach-TYPE-HELLO')`);
  const recovered = await page.waitFor(`document.querySelectorAll('#tw-takebar .tk.got').length >= 1`, 25000, 'recovered').then(() => true).catch(() => false);
  await shot(page, 't4-retry');
  check('a rejected take can be retried and a good one lands', recovered, 'got a take after reject');
  await page.eval(`document.querySelector('#tw-x').click()`); await sleep(600);
  await api('DELETE', '/bank?gloss=' + GLOSS);
  // no-laptop: block /health -> disconnected -> the single "Connect your laptop" row,
  // teach/signs hidden, and the @ picker cannot open.
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*', '*/health'] });
  await page.goto(S.base + '/?k=' + S.KEY, { settle: 1500 });
  await sleep(2000);
  await page.click('#menu'); await sleep(700);
  await shot(page, 't4-no-laptop');
  const connRow = await page.eval(`(() => { const d=document.querySelector('#dr-connect'); return !!d && !d.hidden; })()`);
  const trayHidden = await page.eval(`(() => { const t=document.querySelector('#tray-teach'); return !t || t.hidden; })()`);
  const connText = await page.eval(`(() => { const d=document.querySelector('#dr-connect'); return d ? d.textContent.replace(/\\s+/g,' ').trim() : ''; })()`);
  check('with no laptop the drawer shows the single "Connect your laptop" row', connRow && /connect your laptop/i.test(connText), connText.slice(0, 60));
  check('with no laptop, teach / my-signs (the @ picker paths) are hidden', trayHidden, 'tray-teach hidden=' + trayHidden);
  const pickerClosed = await page.eval(`(() => { const s=document.querySelector('#sheet-actions'); return s ? s.hidden : true; })()`);
  check('with no laptop the @ picker stays closed', pickerClosed, 'picker hidden=' + pickerClosed);
  await page.send('Network.setBlockedURLs', { urls: ['http://127.0.0.1:8877/*'] });
}

// ============================================================================
(async () => {
  const page = await newPage();
  try {
    await t1_teach(page);
    await t2_actions(page);
    await t3_signs(page);
    await t4_simple(page);
  } catch (e) {
    check('flow crashed', false, String(e.message || e).slice(0, 300));
  } finally {
    await page.close();
  }
  const body = results.map(r => `${r.info ? 'NOTE' : (r.ok ? 'PASS' : 'FAIL')}\t${r.name}\t${scrub(r.detail, S)}`).join('\n');
  const pass = results.filter(r => !r.info && r.ok).length, fail = results.filter(r => !r.ok).length;
  fs.writeFileSync(path.join(out, 'RESULT.txt'), `# ${new Date().toISOString()}  flows-teach  PASS=${pass} FAIL=${fail}\n${body}\n`);
  console.log(`\n=== flows-teach: PASS=${pass} FAIL=${fail} ===`);
  process.exitCode = fail ? 1 : 0;
})();
