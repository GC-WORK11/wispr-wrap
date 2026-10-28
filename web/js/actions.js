// actions.js — the @ picker v3: pick what a sign DOES on the laptop.
//
//   Actions.pick(opts?) -> Promise<actionObj | null>
//     opens #sheet-actions over the caller; resolves a bound-action object
//     (see shapes below), null on cancel.
//     opts: {forGloss?, current?, title?, query?}   (query seeds the search)
//   Actions.catalog(force?) -> Promise<catalog>     (cached GET /act)
//   Actions.describe(actionObj) -> short human label
//   Actions.sentence(actionObj) -> "open Google Chrome" style verb phrase
//   Actions.iconFor(action) -> Promise<blobUrl|null>  (real app icon, cached)
//   Actions.appOf(action) -> appRecord|null            (catalog row for an app action)
//
// ACTION SHAPES (wire form the daemon's act layer normalises):
//   app:      {kind:'app',     tool:'app.open', args:{id}, label, tier}
//   builtin:  {kind:'builtin', tool:'key.press'|'open.url'|'type.text',
//                              args:{...}, label, tier}
//   command:  {kind:'command', tool:'command',  args:{argv:[..]}, label, tier}
//   macro/context from macro.js.
// argv-expressible actions also carry a top-level `argv` for the pre-provider
// daemon; the richer dict rides along for the new act layer.
import { App } from './app.js';
import { Macro } from './macro.js';

const sheet = App.el.sheetActions;
if (!sheet) throw new Error('actions: #sheet-actions missing');

sheet.innerHTML = `
  <div class="sheet-card">
    <div class="sheet-head">
      <h1 id="act-title" class="sr-only">Pick an app or action</h1>
      <button class="sheet-x" id="act-x" aria-label="Cancel"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
    </div>
    <div class="act-search">
      <span class="act-at" aria-hidden="true">@</span>
      <input class="input" id="act-q" type="search"
        placeholder="Search" aria-label="Search" autocomplete="off" autocapitalize="none">
    </div>
    <div class="sheet-body" id="act-list"></div>
    <div class="act-note" id="act-note"></div>
  </div>`;
const $ = id => sheet.querySelector('#' + id);
const q = $('act-q'), listEl = $('act-list'), titleEl = $('act-title'), noteEl = $('act-note');

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------- inline SVG glyphs for the Actions list (thin, ink, no fill) ----- */
function glyph(name) {
  const P = {
    globe: 'M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c2.5 2.3 2.5 15.7 0 18M12 3c-2.5 2.3-2.5 15.7 0 18',
    keys: 'M7 9h10a2 2 0 012 2v6a2 2 0 01-2 2H7a2 2 0 01-2-2v-6a2 2 0 012-2zM8 12h.01M12 12h.01M16 12h.01M8 16h8',
    text: 'M5 7h14M5 12h14M5 17h9',
    terminal: 'M5 5h14a1 1 0 011 1v12a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1zM7 9l3 3-3 3M13 15h4',
    window: 'M4 6h16v12H4zM4 9h16',
    tile: 'M4 5h7v14H4zM13 5h7v14h-7z',
    maximize: 'M5 5h14v14H5z',
    workspace: 'M4 5h16v14H4zM9 5v14',
    monitor: 'M4 5h16v11H4zM9 20h6M12 16v4',
    camera: 'M4 8h4l1.5-2h5L16 8h4v11H4zM12 16a3 3 0 100-6 3 3 0 000 6z',
    volume: 'M5 9v6h4l5 4V5L9 9H5zM17 9a4 4 0 010 6',
    play: 'M5 9v6h4l5 4V5L9 9H5z',
    lock: 'M7 11V8a5 5 0 0110 0v3M6 11h12v9H6z',
    clipboard: 'M9 5h6v2H9zM7 6H5v14h14V6h-2',
    dnd: 'M12 3a9 9 0 100 18 9 9 0 000-18zM6 6l12 12',
    bolt: 'M13 3L5 13h5l-1 8 8-10h-5l1-8z',
  };
  const d = P[name] || P.bolt;
  return `<svg class="act-g" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="${d}"/></svg>`;
}
/* tool id -> glyph name */
function glyphFor(id) {
  const m = {
    'open.url': 'globe', 'key.press': 'keys', 'type.text': 'text',
    'command': 'terminal', 'app.focus': 'window', 'app.close': 'window',
    'win.tile-left': 'tile', 'win.tile-right': 'tile', 'win.maximize': 'maximize',
    'win.move-monitor': 'monitor', 'win.workspace-next': 'workspace', 'win.to-workspace': 'workspace',
    'sys.screenshot': 'camera', 'sys.volume-up': 'volume', 'sys.volume-down': 'volume',
    'sys.mute': 'volume', 'media.playpause': 'play', 'media.next': 'play', 'media.prev': 'play',
    'sys.brightness-up': 'bolt', 'sys.brightness-down': 'bolt', 'sys.lock': 'lock',
    'sys.dnd': 'dnd', 'net.wifi': 'bolt', 'net.bluetooth': 'bolt', 'clipboard.history': 'clipboard',
  };
  return m[id] || 'bolt';
}

/* ---------- fallback tools (pre-provider daemon) ---------- */
const FALLBACK_TOOLS = [
  { id: 'open.url', kind: 'builtin', label: 'Open a page', arg: 'url', tier: 1 },
  { id: 'key.press', kind: 'builtin', label: 'Press keys', arg: 'keys', tier: 1 },
  { id: 'type.text', kind: 'builtin', label: 'Type text', arg: 'text', tier: 1 },
  { id: 'command', kind: 'command', label: 'Run a command', arg: 'argv', tier: 2 },
];

const ARG_KEY = { app: 'app', url: 'url', keys: 'keys', text: 'text', argv: 'argv', index: 'n' };
const TOOL_TYPE = {
  'app.open': 'open_app', 'app.focus': 'focus_app', 'app.close': 'close_window',
  'open.url': 'open_url', 'type.text': 'type_text', 'key.press': 'key_press',
  'command': 'run_argv', 'macro': 'macro',
};

/* ---------- real app icons ----------
   /act/icons/<id> is key-gated; an <img src> can't send X-WF-Key, so fetch
   with the header once and reuse a blob URL. */
const ICON_URL = new Map();
let iconBusy = 0; const iconQ = [];
function iconBlob(id) {
  if (!id) return Promise.resolve(null);
  if (ICON_URL.has(id)) return ICON_URL.get(id);
  const p = new Promise(res => {
    iconQ.push(() => fetch('/act/icons/' + encodeURIComponent(id), { headers: { 'X-WF-Key': App.key } })
      .then(r => r.ok ? r.blob() : null)
      .then(b => res(b && b.size ? URL.createObjectURL(b) : null)).catch(() => res(null)));
    pumpIcons();
  });
  ICON_URL.set(id, p);
  return p;
}
function pumpIcons() {
  while (iconBusy < 6 && iconQ.length) {
    iconBusy++;
    Promise.resolve(iconQ.shift()()).finally(() => { iconBusy--; pumpIcons(); });
  }
}

/* ---------- catalog ---------- */
let CAT = null;
function iconsEnabled(cat) {
  const f = (cat && cat.capabilities && cat.capabilities.features) || {};
  return !!f.icons;
}
async function catalog(force) {
  if (CAT && !force) return CAT;
  const r = await App.api.get('/act').catch(() => null);
  const j = r && r.ok && r.json ? r.json : null;
  if (j && (Array.isArray(j.apps) || Array.isArray(j.tools))) {
    CAT = { mode: 'rich', apps: j.apps || [], tools: j.tools || [],
            capabilities: j.capabilities || {}, bound: j.actions || [] };
  } else if (j && Array.isArray(j.actions)) {
    CAT = { mode: 'basic', apps: [], tools: FALLBACK_TOOLS.slice(), bound: j.actions, capabilities: {} };
  } else {
    CAT = { mode: 'offline', apps: [], tools: FALLBACK_TOOLS.slice(), bound: [], capabilities: {} };
  }
  return CAT;
}
catalog.refresh = () => catalog(true);

/* ---------- Suggested / noise classification ---------- */
const SUGGEST_RULES = [
  { key: 'browser', cat: /WebBrowser/i, gen: /browser/i, id: /chrom|firefox|brave|edge|safari|opera/i },
  { key: 'terminal', cat: /TerminalEmulator/i, gen: /terminal/i, id: /terminal|konsole|alacritty|kitty/i },
  { key: 'files', cat: /FileManager/i, gen: /file manager/i, id: /thunar$|nautilus|dolphin|files|nemo/i },
  { key: 'editor', cat: /(TextEditor|WordProcessor|IDE)/i, gen: /(editor|word processor)/i, id: /code|gedit|kate|writer|sublime|vim/i },
  { key: 'music', cat: /(Audio|Player)/i, gen: /(music|audio player)/i, id: /spotify|rhythmbox|music|clementine/i },
];
function catList(a) { return (a.categories || []).join(' '); }
function suggestRank(a) {
  for (let i = 0; i < SUGGEST_RULES.length; i++) {
    const r = SUGGEST_RULES[i];
    if ((r.cat && r.cat.test(catList(a))) || (r.gen && r.gen.test(a.generic || '')) || (r.id && r.id.test(a.id || '')))
      return i;
  }
  return -1;
}
const NOISE_ID = /-settings$|-preferences$/i;
const NOISE_CAT = /(^|\s)(Settings|DesktopSettings|X-XFCE-SettingsDialog)(\s|$)/i;
const NOISE_NAME = /(preferences|tweaks|bulk rename|settings)/i;
const NOISE_HELPER = /^(picom|plank|x11vnc|okta|compton|xscreensaver|redshift)/i;
function isNoise(a) {
  return NOISE_ID.test(a.id || '') || NOISE_CAT.test(catList(a)) ||
         NOISE_NAME.test(a.name || '') || NOISE_HELPER.test(a.id || '');
}

/* ---------- action builders ---------- */
function appAction(app) {
  return { kind: 'app', tool: 'app.open', type: 'open_app',
           args: { id: app.id }, id: app.id,
           label: 'Open ' + (app.name || app.id), tier: 1,
           argv: app.argv && app.argv.length ? app.argv : undefined };
}
function toolAction(tool, argVal) {
  const a = { kind: tool.kind || 'builtin', tool: tool.id,
              label: tool.label || tool.id, tier: tool.tier == null ? 1 : tool.tier };
  if (TOOL_TYPE[tool.id]) a.type = TOOL_TYPE[tool.id];
  if (tool.arg && argVal != null && argVal !== '') {
    a.args = { [ARG_KEY[tool.arg] || tool.arg]: tool.arg === 'argv'
      ? String(argVal).trim().split(/\s+/).filter(Boolean)
      : tool.arg === 'index' ? +argVal : argVal };
  } else if (tool.arg) {
    return null;
  }
  if (tool.id === 'open.url' && a.args) a.argv = ['xdg-open', a.args.url];
  if (tool.id === 'key.press' && a.args) a.argv = ['xdotool', 'key', a.args.keys];
  if (tool.id === 'type.text' && a.args) a.argv = ['xdotool', 'type', '--', a.args.text];
  if (tool.id === 'command' && a.args) a.argv = a.args.argv;
  return a;
}

/* ---------- appOf + iconFor (public) ---------- */
function appOf(a) {
  if (!a) return null;
  const cat = CAT;
  const id = (a.args && a.args.id) || a.id;
  if (!cat || !cat.apps || !id) return null;
  if (a.tool && a.tool !== 'app.open' && a.kind !== 'app') return null;
  return cat.apps.find(x => x.id === id) || null;
}
async function iconFor(a) {
  await catalog().catch(() => {});
  const app = appOf(a);
  if (app && iconsEnabled(CAT)) return iconBlob(app.id);
  // an app action pointing at an id we didn't catalog — still try the daemon
  const id = (a && a.args && a.args.id) || (a && a.id);
  if (id && (a.tool === 'app.open' || a.kind === 'app')) return iconBlob(id);
  return null;
}

/* ---------- sentence ---------- */
function argOf(a) {
  if (!a) return '';
  const g = a.args || {};
  if (g.text != null) return String(g.text);
  if (g.url != null) return String(g.url);
  if (g.keys != null) return String(g.keys);
  if (g.id != null) return String(a.label || g.id).replace(/^open\s+/i, '');
  if (Array.isArray(g.argv)) return g.argv.join(' ');
  if (Array.isArray(a.argv)) return a.argv.join(' ');
  return '';
}
function sentence(a) {
  if (!a) return '';
  const tool = a.tool || '';
  const arg = argOf(a).trim();
  const quo = s => '\u201c' + s + '\u201d';
  switch (tool) {
    case 'type.text': return arg ? 'type ' + quo(arg) : 'type text';
    case 'open.url': return arg ? 'open ' + arg : 'open a page';
    case 'key.press': return arg ? 'press ' + arg : 'press a key';
    case 'app.open': return 'open ' + (arg || 'the app');
    case 'app.focus': return 'show ' + (a.args && (a.args.app || a.app) || 'the app');
    case 'app.close': return 'close the window';
    case 'win.tile-left': return 'tile left';
    case 'win.tile-right': return 'tile right';
    case 'win.maximize': return 'maximize the window';
    case 'win.workspace-next': return 'next workspace';
    case 'command': return arg ? 'run ' + quo(arg) : 'run a command';
    case 'macro': return 'run steps';
    case 'sys.screenshot': return 'take a screenshot';
    case 'sys.lock': return 'lock the screen';
  }
  if (a.kind === 'context' || a.by_wm_class) return 'switch by app';
  if (a.kind === 'macro' || (a.args && a.args.steps)) return 'run steps';
  const d = describe(a);
  return d.charAt(0).toLowerCase() + d.slice(1);
}

/* ---------- describe ---------- */
function describe(a) {
  if (!a) return '';
  if (typeof a === 'string') return a;
  if (a.kind === 'context' || a.by_wm_class || a.per_app) return 'By app';
  if (a.kind === 'macro' || a.steps || (a.args && a.args.steps))
    return ((a.steps || (a.args || {}).steps || []).length || '') + ' steps'.trim();
  if (a.tool && a.tool !== 'command') return a.label || a.tool;
  if (a.argv) return (Array.isArray(a.argv) ? a.argv.join(' ') : String(a.argv));
  if (a.args && a.args.argv)
    return (Array.isArray(a.args.argv) ? a.args.argv.join(' ') : String(a.args.argv));
  if (a.label) return a.label;
  if (a.type) return a.type.replace(/_/g, ' ');
  return '';
}

/* ---------- pick sheet ---------- */
let resolver = null;

function close(result) {
  sheet.hidden = true;
  const r = resolver; resolver = null;
  if (r) r(result || null);
}

let APPS_ONLY = false;
function pick(opts) {
  opts = opts || {};
  APPS_ONLY = !!opts.apps;
  sheet.classList.toggle('apps-only', APPS_ONLY);
  titleEl.textContent = opts.title ||
    (opts.forGloss ? 'What should this sign do?' : 'Pick an app or action');
  q.value = opts.query ? String(opts.query) : '';
  sheet.hidden = false;
  App.raise(sheet);
  selIdx = 0;
  build();
  catalog().then(() => build());
  setTimeout(() => q.focus(), 60);
  return new Promise(res => { resolver = res; });
}

function matchApp(txt, a) {
  if (!txt) return true;
  return (a.name + ' ' + (a.id || '') + ' ' + (a.generic || '') + ' ' + catList(a)).toLowerCase().includes(txt);
}
function matchTool(txt, t) {
  if (!txt) return true;
  return ((t.label || '') + ' ' + (t.desc || '') + ' ' + (t.id || '')).toLowerCase().includes(txt);
}

const LOCK = '<svg class="act-lock" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Asks first"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
/* sections are separated by space, not by headings */
function sectHead() {
  const h = document.createElement('div'); h.className = 'act-sect'; h.setAttribute('role', 'presentation');
  listEl.appendChild(h);
}

function appCell(a) {
  const it = document.createElement('button');
  it.className = 'act-app';
  const icon = document.createElement('span'); icon.className = 'act-ic';
  if (iconsEnabled(CAT)) {
    icon.textContent = (a.name || '?')[0].toUpperCase();
    iconBlob(a.id).then(u => {
      if (!u) return;
      const img = document.createElement('img');
      img.alt = ''; img.src = u;
      img.onload = () => { icon.textContent = ''; icon.appendChild(img); icon.classList.add('real'); };
    });
  } else {
    icon.textContent = (a.name || '?')[0].toUpperCase();
  }
  const nm = document.createElement('span'); nm.className = 'act-app-nm';
  nm.textContent = a.name || a.id;
  it.append(icon, nm);
  it.onclick = () => close(appAction(a));
  return it;
}

function toolRow(t) {
  const row = document.createElement('div');
  row.className = 'act-row';
  const tier = t.tier == null ? 1 : t.tier;
  const top = document.createElement('div'); top.className = 'act-row-top';
  top.innerHTML = glyph(glyphFor(t.id)) +
    `<span class="act-name">${esc(t.label || t.id)}</span>`;
  row.appendChild(top);
  if (tier >= 2) top.insertAdjacentHTML('beforeend', LOCK);
  if (t.arg) {
    const argWrap = document.createElement('div'); argWrap.className = 'act-arg';
    const inp = document.createElement('input');
    inp.className = 'input act-input'; inp.type = 'text';
    inp.setAttribute('aria-label', t.label || t.id);
    inp.autocomplete = 'off'; inp.autocapitalize = 'none';
    const use = document.createElement('button');
    use.className = 'act-use'; use.textContent = 'Use this';
    argWrap.append(inp, use); row.appendChild(argWrap);
    const commit = () => {
      const a = toolAction(t, inp.value);
      if (!a) { inp.classList.add('act-bad'); inp.focus(); return; }
      close(a);
    };
    use.onclick = commit;
    inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } };
    row.onclick = e => { if (e.target !== inp && e.target !== use) inp.focus(); };
  } else {
    row.onclick = () => close(toolAction(t));
  }
  return row;
}

/* ---------- Write your own ---------- */
const VERBS = ['open', 'type', 'press', 'run'];
function parseOwn(raw) {
  const s = String(raw || '').trim();
  if (!s) return { ok: false, empty: true, preview: '', err: '' };
  const m = s.match(/^(\w+)\s*([\s\S]*)$/);
  const verb = (m ? m[1] : '').toLowerCase();
  const rest = m ? m[2].trim() : '';
  if (!VERBS.includes(verb))
    return { ok: false, preview: '', err: 'open, type, press or run' };
  if (!rest)
    return { ok: false, preview: '', err: verb + ' what?' };
  if (verb === 'open') {
    const isUrl = /^https?:\/\//i.test(rest) || /\.[a-z]{2,}(\/|$)/i.test(rest);
    const isPath = rest.startsWith('/') || rest.startsWith('~');
    if (isUrl) {
      const url = /^https?:\/\//i.test(rest) ? rest : 'https://' + rest;
      return { ok: true, preview: 'Open ' + url + ' in your browser',
        action: toolAction({ id: 'open.url', kind: 'builtin', label: 'Open ' + url, arg: 'url', tier: 1 }, url) };
    }
    if (isPath)
      return { ok: true, preview: 'Open ' + rest + ' on your laptop',
        action: { kind: 'command', tool: 'command', args: { argv: ['xdg-open', rest] }, argv: ['xdg-open', rest], label: 'Open ' + rest, tier: 1 } };
    if (/^(this|that|it|this one|the selected one|selected)$/i.test(rest))
      return { ok: true, preview: 'Open whatever is selected on your laptop (presses Enter)',
        action: toolAction({ id: 'key.press', kind: 'builtin', label: 'Open this', arg: 'keys', tier: 1 }, 'Return') };
    const low = rest.toLowerCase();
    const hit = ((CAT && CAT.apps) || []).find(a => String(a.id).toLowerCase() === low || String(a.name || '').toLowerCase() === low)
      || ((CAT && CAT.apps) || []).find(a => String(a.name || '').toLowerCase().includes(low) || String(a.id).toLowerCase().includes(low));
    if (hit) return { ok: true, preview: 'Open ' + (hit.name || hit.id), action: appAction(hit) };
    return { ok: true, preview: 'Open ' + rest + ' on your laptop',
      action: { kind: 'app', tool: 'app.open', type: 'open_app', args: { id: rest }, id: rest, label: 'Open ' + rest, tier: 1 } };
  }
  if (verb === 'type')
    return { ok: true, preview: 'Type \u201c' + rest + '\u201d at your cursor',
      action: toolAction({ id: 'type.text', kind: 'builtin', label: 'Type text', arg: 'text', tier: 1 }, rest) };
  if (verb === 'press')
    return { ok: true, preview: 'Press ' + rest,
      action: toolAction({ id: 'key.press', kind: 'builtin', label: 'Press keys', arg: 'keys', tier: 1 }, rest) };
  if (verb === 'run') {
    const argv = rest.split(/\s+/).filter(Boolean);
    return { ok: true, risky: true, preview: 'Run ' + rest + ' — asks you first on your laptop',
      action: { kind: 'command', tool: 'command', args: { argv }, argv, label: 'Run ' + rest.slice(0, 40), tier: 2 } };
  }
  return { ok: false, preview: '', err: 'open, type, press or run' };
}

function ownSection() {
  sectHead('Write your own');
  const wrap = document.createElement('div'); wrap.className = 'act-own';
  const inp = document.createElement('input');
  inp.className = 'input act-own-in'; inp.type = 'text';
  inp.setAttribute('aria-label', 'Write your own');
  inp.autocomplete = 'off'; inp.autocapitalize = 'none'; inp.spellcheck = false;
  const prev = document.createElement('div'); prev.className = 'act-own-prev';
  const bar = document.createElement('div'); bar.className = 'act-own-bar';
  const openThis = document.createElement('button');
  openThis.className = 'act-open-this'; openThis.type = 'button';
  openThis.textContent = 'Open this';
  const use = document.createElement('button');
  use.className = 'act-use act-own-use'; use.textContent = 'Use this'; use.disabled = true;
  bar.append(openThis, use);
  wrap.append(inp, prev, bar);
  listEl.appendChild(wrap);

  let cur = null;
  const render = () => {
    const r = parseOwn(inp.value);
    cur = r.ok ? r.action : null;
    use.disabled = !r.ok;
    inp.classList.toggle('act-bad', !!(r.err && !r.empty));
    if (r.empty) { prev.textContent = ''; prev.className = 'act-own-prev'; }
    else if (r.ok) { prev.textContent = r.preview; prev.className = 'act-own-prev show' + (r.risky ? ' ask' : ''); }
    else { prev.textContent = r.err; prev.className = 'act-own-prev err'; }
  };
  inp.addEventListener('input', render);
  inp.addEventListener('keydown', e => { if (e.key === 'Enter' && cur) { e.preventDefault(); close(cur); } });
  use.onclick = () => { if (cur) close(cur); };
  // "Open this" = press Enter on the laptop (opens whatever is selected there)
  openThis.onclick = () => close(toolAction(
    { id: 'key.press', kind: 'builtin', label: 'Open this', arg: 'keys', tier: 1 }, 'Return'));
  render();
}

async function build() {
  const cat = CAT || { mode: 'offline', apps: [], tools: FALLBACK_TOOLS, bound: [], capabilities: {} };
  const query = q.value.trim().toLowerCase();
  listEl.textContent = '';
  noteEl.textContent = '';

  /* --- a typed command works right here ("open youtube.com", "press ctrl+shift+t") --- */
  const typed = (query && !APPS_ONLY) ? parseOwn(q.value.trim()) : null;
  if (typed && typed.ok) {
    const row = document.createElement('div'); row.className = 'act-row act-cmd';
    row.innerHTML = `<div class="act-row-top">${glyph('bolt')}<span class="act-name">${esc(typed.preview)}</span>${typed.risky ? LOCK : ''}</div>`;
    row.onclick = () => close(typed.action);
    listEl.appendChild(row);
  } else if (!query && !APPS_ONLY) {
    const row = document.createElement('div'); row.className = 'act-row act-own-link';
    row.innerHTML = `<div class="act-row-top">${glyph('text')}<span class="act-name">Write your own</span></div>`;
    row.onclick = () => {
      const o = listEl.querySelector('.act-own-in'); if (!o) return;
      o.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(() => o.focus({ preventScroll: true }), 280);
    };
    listEl.appendChild(row);
  }

  const allApps = cat.apps || [];
  const visibleApps = allApps.filter(a => query ? true : !isNoise(a));
  const searched = allApps.filter(a => matchApp(query, a));

  /* --- Suggested (daily apps present on this laptop) --- */
  const suggested = [];
  const seenSug = new Set();
  const pool = (query ? searched : visibleApps);
  const ranked = pool.map(a => ({ a, r: suggestRank(a) })).filter(x => x.r >= 0)
    .sort((x, y) => x.r - y.r);
  for (const { a, r } of ranked) {
    if (seenSug.has(r)) continue;    // one per lane (browser/terminal/files/editor/music)
    seenSug.add(r); suggested.push(a);
  }
  if (suggested.length) {
    sectHead('Suggested');
    const grid = document.createElement('div'); grid.className = 'act-grid';
    for (const a of suggested) grid.appendChild(appCell(a));
    listEl.appendChild(grid);
  }

  /* --- Apps (icon grid, noise hidden unless searched) --- */
  const sugSet = new Set(suggested);
  const apps = (query ? searched : visibleApps).filter(a => !sugSet.has(a));
  if (apps.length) {
    sectHead(query ? 'Apps' : (suggested.length ? 'More apps' : 'Apps'));
    const grid = document.createElement('div'); grid.className = 'act-grid';
    for (const a of apps.slice(0, 60)) grid.appendChild(appCell(a));
    listEl.appendChild(grid);
  }

  if (APPS_ONLY) { setTimeout(() => sel(selIdx), 0); return; }   // Teach: just the laptop's apps

  /* --- Actions (flat list) --- */
  const tools = (cat.tools || []).filter(t => t.id !== 'app.open' && matchTool(query, t));
  if (tools.length) {
    sectHead('Actions');
    for (const t of tools) listEl.appendChild(toolRow(t));
    // advanced: macro + per-app rule (same list, quieter)
    const adv = [
      { id: '_macro', label: 'Steps', tier: 1, g: 'bolt' },
      { id: '_context', label: 'By app', tier: 1, g: 'window' },
    ].filter(x => matchTool(query, x));
    for (const x of adv) {
      const row = document.createElement('div'); row.className = 'act-row';
      row.innerHTML = `<div class="act-row-top">${glyph(x.g)}<span class="act-name">${esc(x.label)}</span></div>`;
      row.onclick = async () => {
        const a = x.id === '_macro' ? await Macro.edit() : await Macro.editContext();
        if (a) close(a); else build();
      };
      listEl.appendChild(row);
    }
  }

  /* --- Write your own --- */
  ownSection();

  setTimeout(() => sel(selIdx), 0);
  if (!listEl.querySelector('.act-app, .act-row') && !listEl.querySelector('.act-own') && query) {
    const d = document.createElement('div'); d.className = 'act-empty';
    d.textContent = '';
    listEl.appendChild(d);
  }
}

q.addEventListener('input', () => { build(); sel(0, true); });

/* keyboard: ↑/↓ move, Tab or Enter completes the highlighted row, Esc cancels */
let selIdx = 0;
const rows = () => [...listEl.querySelectorAll('.act-app, .act-row')];
function sel(i, reset) {
  const r = rows(); if (!r.length) return;
  selIdx = reset ? 0 : (i + r.length) % r.length;
  r.forEach((el, k) => el.classList.toggle('act-sel', k === selIdx));
  r[selIdx].scrollIntoView({ block: 'nearest' });
}
q.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') { e.preventDefault(); sel(selIdx + 1); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); sel(selIdx - 1); }
  else if ((e.key === 'Tab' && !e.shiftKey) || e.key === 'Enter') {
    const r = rows()[selIdx]; if (!r) return;
    e.preventDefault();
    const inp = r.querySelector('input');
    if (inp) inp.focus(); else r.click();
  }
});
$('act-x').onclick = () => close(null);
sheet.addEventListener('click', e => { if (e.target === sheet) close(null); });
sheet.addEventListener('keydown', e => { if (e.key === 'Escape') close(null); });

export const Actions = { pick, catalog, describe, sentence, iconFor, appOf,
                         _cat: () => CAT };
