// quick.js — one-tap laptop actions for the Teach screen.
//
// The grid is driven by the laptop itself: GET /act returns tool rows {id,label,icon,tier} for the
// 20 one-tap actions (Devin PRO's contract). A tile shows only if the laptop reports its tool, takes its
// risk level from the row (win.close is tier 2: the laptop asks before it runs) and its picture from the
// laptop's own icon theme (/act/icons/<icon>-symbolic, monochrome, recoloured by CSS). Where the theme has
// no picture the tile keeps a drawn glyph. An older laptop without the rows still gets the xdotool versions.
//
//   Quick.mount(host, { onPick(action|null), onOpenApp(), onText(text|null) }) -> Promise<{tiles:number}>
//   Quick.setApp(label, iconUrl) / Quick.resetApp() / Quick.clear()
import { App } from './app.js';
import { Actions } from './actions.js';

const G = {
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
  wave: '<path d="M8.5 12V6a1.5 1.5 0 0 1 3 0v5m0-1.5a1.5 1.5 0 0 1 3 0V11m0-.5a1.5 1.5 0 0 1 3 0v1m0-.5a1.5 1.5 0 0 1 3 0v4.5c0 3-2.5 5.5-6 5.5s-5-1.5-6.5-4l-2-3.6c-.6-1 .8-2.1 1.7-1.3l1.3 1.1"/>',
  person: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-4 3-6.5 7-6.5s7 2.5 7 6.5"/>',
};
const glyph = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${G[name] || G.app}</svg>`;

/* The 20 tiles, in the spec order. id = the laptop's tool id (GET /act), label = 1-2 words,
   alt = extra icon names to try when the theme lacks the row's own, say = "Your laptop will <say>". */
const T = (id, label, g, say, o) => Object.assign({ id, label, g, say }, o || {});
export const TILES = [
  T('win.alt-tab', 'Switch app', 'switch', 'switch to the next app', { alt: ['focus-windows-symbolic'], fb: { keys: 'alt+Tab' } }),
  T('win.maximize', 'Maximize', 'maximize', 'maximize the window'),
  T('win.minimize', 'Minimize', 'minimize', 'minimize the window', { fb: { argv: ['xdotool', 'getactivewindow', 'windowminimize'] } }),
  T('win.close', 'Close window', 'close', 'close the window, after you say yes on the laptop', { tier: 2, fb: { keys: 'alt+F4' } }),
  T('sys.volume-up', 'Volume up', 'volup', 'turn the volume up'),
  T('sys.volume-down', 'Volume down', 'voldown', 'turn the volume down'),
  T('sys.mute', 'Mute', 'mute', 'mute the sound'),
  T('desktop.show', 'Show desktop', 'desktop', 'show the desktop', { fb: { keys: 'super+d' } }),
  T('browser.new-tab', 'New tab', 'newtab', 'open a new tab', { fb: { keys: 'ctrl+t' } }),
  T('browser.close-tab', 'Close tab', 'closetab', 'close the tab', { fb: { keys: 'ctrl+w' } }),
  T('media.playpause', 'Play / Pause', 'play', 'play or pause the music'),
  T('sys.screenshot', 'Screenshot', 'shot', 'take a screenshot'),
  T('nav.scroll-up', 'Scroll up', 'up', 'scroll up', { fb: { argv: ['xdotool', 'click', '--repeat', '5', '--delay', '15', '4'] } }),
  T('nav.scroll-down', 'Scroll down', 'down', 'scroll down', { fb: { argv: ['xdotool', 'click', '--repeat', '5', '--delay', '15', '5'] } }),
  T('edit.copy', 'Copy', 'copy', 'copy', { fb: { keys: 'ctrl+c' } }),
  T('edit.paste', 'Paste', 'paste', 'paste', { fb: { keys: 'ctrl+v' } }),
  T('edit.undo', 'Undo', 'undo', 'undo', { fb: { keys: 'ctrl+z' } }),
  T('key.enter', 'Enter', 'enter', 'press Enter', { noicon: true, fb: { keys: 'Return' } }),
  T('app.open', 'Open app', 'app', '', { special: 'app', alt: ['view-app-grid-symbolic'] }),
  T('type.text', 'Type text', 'text', '', { special: 'text', alt: ['input-keyboard-symbolic'] }),
];

/* Ready-made signs are plain tiles: tap, Record, done. The pack title (e.g. "Hello") is what the laptop
   already calls the pack, so GET /packs flips it to ready once it has three takes. Namaste is built into the
   engine (palms together) and has no tile. */
const PACK_TILES = {
  'hello': { label: 'Hello', g: 'wave', text: 'hello' },
  'thank-you': { label: 'Thank you', g: 'person', text: 'thank you' },
};
const packAction = (pk) => Object.assign(typeAction(pk.text), { label: pk.label, say: 'type \u201c' + pk.text + '\u201d' });

export const typeAction = (text) => ({
  kind: 'builtin', tool: 'type.text', type: 'type_text', args: { text },
  argv: ['xdotool', 'type', '--', text],
  label: 'Type \u201c' + text.slice(0, 28) + (text.length > 28 ? '\u2026' : '') + '\u201d',
  say: 'type \u201c' + text + '\u201d', tier: 1,
});

/* ---- the laptop's own icons (key-gated: fetch with the header, show from a blob URL) ---- */
const ICON = new Map();
function iconUrl(name) {
  if (ICON.has(name)) return ICON.get(name);
  const p = fetch('/act/icons/' + encodeURIComponent(name), { headers: { 'X-WF-Key': App.key } })
    .then(r => (r.ok ? r.blob() : null))
    .then(b => (b && /^image\//.test(b.type) ? URL.createObjectURL(b) : null))
    .catch(() => null);
  ICON.set(name, p);
  return p;
}
async function tileIcon(t, row) {
  if (t.noicon) return null;
  const names = t.special ? (t.alt || []).slice() : [];
  if (row && row.icon) names.push(row.icon + '-symbolic');
  (t.alt || []).forEach(n => { if (!names.includes(n)) names.push(n); });
  for (const n of names) { const u = await iconUrl(n); if (u) return u; }
  return null;
}

/* the action a tile binds: the laptop's tool id (contract) or, on an older laptop, the xdotool version */
function build(t, row) {
  const base = { label: t.label, say: t.say, tier: row && row.tier != null ? row.tier : (t.tier || 1) };
  if (row) return Object.assign({ kind: 'builtin', tool: t.id }, base);
  const f = t.fb || {};
  if (f.keys) return Object.assign({ kind: 'builtin', tool: 'key.press', type: 'key_press', args: { keys: f.keys }, argv: ['xdotool', 'key', f.keys] }, base);
  if (f.argv) return Object.assign({ kind: 'command', tool: 'command', type: 'run_argv', args: { argv: f.argv }, argv: f.argv }, base);
  return null;
}

let host = null, cb = {}, sel = null, rows = new Map();
const els = new Map();
function mark(id) {
  sel = id;
  for (const [k, el] of els) el.setAttribute('aria-pressed', k === id ? 'true' : 'false');
}

export const Quick = {
  TILES,
  async mount(h, callbacks) {
    host = h; cb = callbacks || {};
    host.textContent = ''; els.clear(); sel = null;
    let cat = null;
    try { cat = await Actions.catalog(true); } catch (e) { /* offline */ }
    const online = !!(cat && cat.mode && cat.mode !== 'offline');
    rows = new Map(((cat && cat.tools) || []).map(r => [r.id, r]));
    const modern = rows.has('win.alt-tab') || rows.has('key.enter');      // the laptop serves the grid rows
    let n = 0;
    for (const t of TILES) {
      const row = rows.get(t.id);
      if (!row && !(t.fb || t.special) ) continue;                         // the laptop can't do it: don't offer it
      if (!row && !t.special && modern) continue;                          // modern laptop without this tool
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'qa-tile'; b.dataset.id = t.id; b.setAttribute('aria-pressed', 'false');
      b.innerHTML = `<span class="qa-ic"></span><span class="qa-l">${t.label}</span>`;
      const ic = b.firstChild;
      ic.innerHTML = glyph(t.g);                                            // drawn glyph until the laptop's own icon lands
      tileIcon(t, row).then(u => {
        if (!u) return;
        ic.innerHTML = '<i class="qa-real"></i>';
        ic.firstChild.style.setProperty('--m', `url("${u}")`);
        b.classList.add('real');
      });
      b.onclick = () => this.tap(t);
      host.appendChild(b); els.set(t.id, b); n++;
    }
    if (online) this.addPacks();
    return { tiles: n, online };
  },
  async addPacks() {
    let list = [];
    try { const r = await App.api.get('/packs'); list = (r && r.ok && r.json && r.json.packs) || []; } catch (e) { /* older laptop */ }
    for (const p of list) {
      const pk = PACK_TILES[p.id];
      if (!pk || p.state === 'ready' || p.state === 'built-in' || els.has('pack.' + p.id) || !host) continue;
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'qa-tile'; b.dataset.id = 'pack.' + p.id; b.setAttribute('aria-pressed', 'false');
      b.innerHTML = `<span class="qa-ic">${glyph(pk.g)}</span><span class="qa-l">${pk.label}</span>`;
      b.onclick = () => { mark('pack.' + p.id); cb.onText && cb.onText(null); cb.onPick && cb.onPick(packAction(pk)); };
      host.appendChild(b); els.set('pack.' + p.id, b);
    }
  },
  tap(t) {
    if (sel === t.id && !t.special) { this.clear(); cb.onPick && cb.onPick(null); return; }
    if (t.special === 'app') { mark(t.id); cb.onText && cb.onText(null); cb.onOpenApp && cb.onOpenApp(); return; }
    if (t.special === 'text') { mark(t.id); cb.onText && cb.onText(''); cb.onPick && cb.onPick(null); return; }
    mark(t.id);
    cb.onText && cb.onText(null);
    cb.onPick && cb.onPick(build(t, rows.get(t.id)));
  },
  setApp(label, url) {
    const b = els.get('app.open'); if (!b) return;
    mark('app.open');
    b.querySelector('.qa-l').textContent = (label || 'Open app').replace(/^Open /i, '');
    if (url) b.querySelector('.qa-ic').innerHTML = `<img alt="" src="${url}">`;
  },
  resetApp() {
    const b = els.get('app.open'); if (!b) return;
    b.querySelector('.qa-l').textContent = 'Open app';
    const t = TILES.find(x => x.id === 'app.open');
    tileIcon(t, rows.get('app.open')).then(u => {
      const ic = b.querySelector('.qa-ic');
      if (u) { ic.innerHTML = '<i class="qa-real"></i>'; ic.firstChild.style.setProperty('--m', `url("${u}")`); }
      else ic.innerHTML = glyph('app');
    });
  },
  /* the chosen tile as a picture (for the Ready screen) */
  summary() {
    const b = sel && els.get(sel); if (!b) return null;
    return { ic: b.querySelector('.qa-ic').innerHTML, label: b.querySelector('.qa-l').textContent };
  },
  clear() { mark(null); },
  get selected() { return sel; },
};
