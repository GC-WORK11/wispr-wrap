// quick.js — one-tap laptop actions for the Teach screen (FOUNDER-SIMPLIFY-1433).
//
//   Quick.mount(host, { onPick(action|null), onOpenApp(), onText(text) })
//     renders a 3-column grid of tiles (icon + 1-2 words). Tap selects, tap again
//     clears. Two tiles open a second input: "Open app" (the @ picker with the
//     laptop's real icons) and "Type text" (a field under the grid).
//   Quick.setApp(action, label, iconUrl)  -> turns the Open app tile into the chosen app
//   Quick.clear()                          -> no tile selected
//
// Every tile is a real, runnable action:
//   key.press   {kind:'builtin', tool:'key.press', args:{keys}, argv:['xdotool','key',keys]}
//   catalog     {kind, tool:'win.maximize'|'sys.volume-up'|...}  (needs the tool in GET /act)
//   xdotool     {kind:'command', tool:'command', args:{argv}, argv}  (tier 1, direct spawn)
// Risky ones (Close window) carry tier 2, so the laptop asks before it runs. Nothing here is
// destructive without that confirm.
import { Actions } from './actions.js';

const I = {
  switch: '<rect x="3" y="8" width="13" height="10" rx="2"/><path d="M8 8V6a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-3"/>',
  maximize: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  minimize: '<path d="M5 18h14"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  desktop: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  newtab: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M12 10v6M9 13h6"/>',
  closetab: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M9.5 10.5l5 5M14.5 10.5l-5 5"/>',
  volup: '<path d="M4 10v4h4l5 4V6L8 10H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>',
  voldown: '<path d="M4 10v4h4l5 4V6L8 10H4z"/><path d="M16.5 9.5a3.5 3.5 0 0 1 0 5"/>',
  mute: '<path d="M4 10v4h4l5 4V6L8 10H4z"/><path d="M17 9.5l4 5M21 9.5l-4 5"/>',
  play: '<path d="M6 5l8 7-8 7V5z"/><path d="M18 5v14"/>',
  shot: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/>',
  up: '<path d="M12 19V6M6 11l6-6 6 6"/>',
  down: '<path d="M12 5v13M6 13l6 6 6-6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  paste: '<rect x="5" y="5" width="14" height="16" rx="2"/><path d="M9 5V4h6v1M9 11h6M9 15h4"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  enter: '<path d="M20 5v7a3 3 0 0 1-3 3H5M9 11l-4 4 4 4"/>',
  app: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
  text: '<path d="M5 7V5h14v2M12 5v14M9 19h6"/>',
};

const key = (id, label, icon, keys, say, extra) => ({
  id, label, icon, say,
  make: () => Object.assign({
    kind: 'builtin', tool: 'key.press', type: 'key_press', args: { keys },
    argv: ['xdotool', 'key', keys], label, say, tier: 1,
  }, extra || {}),
});
const cmd = (id, label, icon, argv, say, tier) => ({
  id, label, icon, say,
  make: () => ({ kind: 'command', tool: 'command', type: 'run_argv', args: { argv }, argv, label, say, tier: tier || 1 }),
});
// catalog tools run through the laptop's action layer (needs the tool in GET /act)
const tool = (id, label, icon, toolId, say) => ({
  id, label, icon, say, need: toolId,
  make: (row) => ({ kind: (row && row.kind) || 'builtin', tool: toolId, label, say, tier: row && row.tier != null ? row.tier : 1 }),
});

export const TILES = [
  key('alttab', 'Switch window', 'switch', 'alt+Tab', 'switch to the next window'),
  tool('maximize', 'Maximize', 'maximize', 'win.maximize', 'maximize the window'),
  cmd('minimize', 'Minimize', 'minimize', ['xdotool', 'getactivewindow', 'windowminimize'], 'minimize the window'),
  key('closewin', 'Close window', 'close', 'alt+F4', 'close the window, after you say yes on the laptop', { tier: 2 }),
  key('desktop', 'Show desktop', 'desktop', 'super+d', 'show the desktop'),
  key('newtab', 'New tab', 'newtab', 'ctrl+t', 'open a new tab'),
  key('closetab', 'Close tab', 'closetab', 'ctrl+w', 'close the tab'),
  tool('volup', 'Volume up', 'volup', 'sys.volume-up', 'turn the volume up'),
  tool('voldown', 'Volume down', 'voldown', 'sys.volume-down', 'turn the volume down'),
  tool('mute', 'Mute', 'mute', 'sys.mute', 'mute the sound'),
  tool('play', 'Play / Pause', 'play', 'media.playpause', 'play or pause the music'),
  tool('shot', 'Screenshot', 'shot', 'sys.screenshot', 'take a screenshot'),
  cmd('scrollup', 'Scroll up', 'up', ['xdotool', 'click', '--repeat', '5', '--delay', '15', '4'], 'scroll up'),
  cmd('scrolldown', 'Scroll down', 'down', ['xdotool', 'click', '--repeat', '5', '--delay', '15', '5'], 'scroll down'),
  key('copy', 'Copy', 'copy', 'ctrl+c', 'copy'),
  key('paste', 'Paste', 'paste', 'ctrl+v', 'paste'),
  key('undo', 'Undo', 'undo', 'ctrl+z', 'undo'),
  key('enter', 'Enter', 'enter', 'Return', 'press Enter'),
  { id: 'app', label: 'Open app', icon: 'app', special: 'app' },
  { id: 'text', label: 'Type text', icon: 'text', special: 'text' },
];

const svg = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[name] || I.app}</svg>`;

export const typeAction = (text) => ({
  kind: 'builtin', tool: 'type.text', type: 'type_text', args: { text },
  argv: ['xdotool', 'type', '--', text],
  label: 'Type \u201c' + text.slice(0, 28) + (text.length > 28 ? '\u2026' : '') + '\u201d',
  say: 'type \u201c' + text + '\u201d', tier: 1,
});

let host = null, cb = null, sel = null, catTools = null;
const els = new Map();

function mark(id) {
  sel = id;
  for (const [k, el] of els) el.setAttribute('aria-pressed', k === id ? 'true' : 'false');
}

export const Quick = {
  TILES,
  /* render the grid into `host`; callbacks: onPick(action|null), onOpenApp(), onText(text|null) */
  async mount(h, callbacks) {
    host = h; cb = callbacks || {};
    host.textContent = ''; els.clear(); sel = null;
    let cat = null;
    try { cat = await Actions.catalog(); } catch (e) { /* offline: key tiles still work */ }
    catTools = new Map(((cat && cat.tools) || []).map(t => [t.id, t]));
    const online = !!(cat && cat.mode && cat.mode !== 'offline');
    for (const t of TILES) {
      if (t.need && (!online || !catTools.has(t.need))) continue;   // the laptop can't do it: don't offer it
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'qa-tile'; b.dataset.id = t.id; b.setAttribute('aria-pressed', 'false');
      b.innerHTML = `<span class="qa-ic">${svg(t.icon)}</span><span class="qa-l">${t.label}</span>`;
      b.onclick = () => this.tap(t);
      host.appendChild(b); els.set(t.id, b);
    }
  },
  tap(t) {
    if (sel === t.id && !t.special) { this.clear(); cb.onPick && cb.onPick(null); return; }
    if (t.special === 'app') { mark('app'); cb.onText && cb.onText(null); cb.onOpenApp && cb.onOpenApp(); return; }
    if (t.special === 'text') { mark('text'); cb.onText && cb.onText(''); cb.onPick && cb.onPick(null); return; }
    mark(t.id);
    cb.onText && cb.onText(null);
    cb.onPick && cb.onPick(t.make(t.need ? catTools && catTools.get(t.need) : null));
  },
  /* the Open app tile becomes the chosen app (real icon when available) */
  setApp(label, iconUrl) {
    const b = els.get('app'); if (!b) return;
    mark('app');
    b.querySelector('.qa-l').textContent = (label || 'Open app').replace(/^Open /i, '');
    if (iconUrl) b.querySelector('.qa-ic').innerHTML = `<img alt="" src="${iconUrl}">`;
  },
  resetApp() {
    const b = els.get('app'); if (!b) return;
    b.querySelector('.qa-l').textContent = 'Open app';
    b.querySelector('.qa-ic').innerHTML = svg('app');
  },
  clear() { mark(null); },
  get selected() { return sel; },
};
