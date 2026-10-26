// actions.js — the @ picker: what does this sign DO on the laptop.
//
//   Actions.pick(opts?) -> Promise<actionObj | null>
//     opens #sheet-actions over the caller; resolves a bound-action object
//     (see ACTIONS below), null on cancel.
//     opts: {forGloss?:string, current?:actionObj, title?:string}
//   Actions.catalog(force?) -> Promise<catalog>   (cached GET /act)
//   Actions.describe(actionObj) -> short human label
//
// ACTION SHAPE (APP_API_CONTRACT v2 wire form — the bank stores it opaquely,
// the daemon's act layer normalizes kind+tool+args -> canonical):
//   app:      {kind:'app',     tool:'app.open', args:{id}, label, tier}
//   builtin:  {kind:'builtin', tool:'key.press', args:{keys}, label, tier}
//   command:  {kind:'command', tool:'command',  args:{argv:[..]}, label, tier}
//   macro:    {kind:'macro',   tool:'macro',    args:{steps:[..]}, label, tier}
//   context:  {kind:'context', by_wm_class:{wc:action}, default:action, label}
// For the pre-provider daemon (today's wfd bank_put understands only argv),
// argv-expressible actions ALSO carry `argv` and the caller PUTs both —
// the daemon stores argv, the action dict rides along for the new act layer.
//
// CATALOG — GET /act has two shapes:
//   provider catalog: {ok, apps:[{id,name,icon,argv,wm_class,...}],
//                      tools:[{id,kind,label,tier,arg,desc}], capabilities}
//   commands table:   {ok, actions:[{gloss,label,argv,tier,...}]}  (already
//                     bound rows — read-only, shown as reference)
// Missing entirely -> a built-in argv set that any Linux laptop can spawn.
import { App } from './app.js';
import { Macro } from './macro.js';

const sheet = App.el.sheetActions;
if (!sheet) throw new Error('actions: #sheet-actions missing');

sheet.innerHTML = `
  <div class="sheet-card">
    <div class="sheet-head">
      <h1 id="act-title">Bind an action</h1>
      <button class="sheet-x" id="act-x" aria-label="Cancel">×</button>
    </div>
    <div class="act-search"><input class="input" id="act-q" type="search"
      placeholder="Search apps, actions, commands…" autocomplete="off"></div>
    <div class="sheet-body" id="act-list"></div>
    <div class="act-note" id="act-note"></div>
  </div>`;
const $ = id => sheet.querySelector('#' + id);
const q = $('act-q'), listEl = $('act-list'), titleEl = $('act-title'), noteEl = $('act-note');

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------- fallback argv-expressible rows (pre-provider daemon) ---------- */
const FALLBACK_TOOLS = [
  { id: 'app.open', kind: 'app', label: 'open app', arg: 'app',
    desc: 'launch a program — pick from installed apps or type the command', tier: 1 },
  { id: 'open.url', kind: 'builtin', label: 'open url', arg: 'url',
    desc: 'open a page in the laptop browser', tier: 1 },
  { id: 'key.press', kind: 'builtin', label: 'key press', arg: 'keys',
    desc: 'a key combo — ctrl+alt+t, alt+Tab, media keys', tier: 1 },
  { id: 'type.text', kind: 'builtin', label: 'type text', arg: 'text',
    desc: 'type a phrase at the laptop cursor', tier: 1 },
  { id: 'command', kind: 'command', label: 'terminal command', arg: 'argv',
    desc: 'runs a command on your laptop — asks for a yes there first', tier: 2 },
];

const ARG_LABEL = { app: 'app name or command', url: 'https://…', keys: 'ctrl+alt+t', text: 'text to type', argv: 'program args…', index: 'number', app_pick: 'pick below' };
/* tool-row `arg` name -> the key the canonical handler reads
   (workspace/monitor take `n`, not `index`) */
const ARG_KEY = { app: 'app', url: 'url', keys: 'keys', text: 'text',
                  argv: 'argv', index: 'n' };
/* 1:1 contract ids -> canonical type (belt: the daemon maps `tool` anyway;
   `type` keeps the dict runnable on stub/legacy banks that look for it) */
const TOOL_TYPE = {
  'app.open': 'open_app', 'app.focus': 'focus_app', 'app.close': 'close_window',
  'open.url': 'open_url', 'type.text': 'type_text', 'key.press': 'key_press',
  'command': 'run_argv', 'macro': 'macro',
};

/* ---------- icons ----------
   /act/icons/<id> is key-gated and an <img src> cannot send X-WF-Key, so the
   icon is fetched with the header and shown from a blob URL (cached). */
const ICON_URL = new Map();
let iconBusy = 0; const iconQ = [];
function iconBlob(id) {
  if (ICON_URL.has(id)) return ICON_URL.get(id);
  const p = new Promise(res => {
    iconQ.push(() => fetch('/act/icons/' + encodeURIComponent(id), { headers: { 'X-WF-Key': App.key } })
      .then(r => r.ok ? r.blob() : null).then(b => res(b ? URL.createObjectURL(b) : null)).catch(() => res(null)));
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
  /* The daemon only serves /act/icons/<id> when it advertises the feature —
     otherwise every app cell would 404-log to the console. */
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
    CAT = { mode: 'basic', apps: [], tools: FALLBACK_TOOLS.slice(), bound: j.actions,
            capabilities: {} };
  } else {
    CAT = { mode: 'offline', apps: [], tools: FALLBACK_TOOLS.slice(), bound: [],
            capabilities: {} };
  }
  return CAT;
}
catalog.refresh = () => catalog(true);

/* ---------- action builders ---------- */
function appAction(app) {
  return { kind: 'app', tool: 'app.open', type: 'open_app',
           args: { id: app.id }, id: app.id,
           label: 'open ' + (app.name || app.id), tier: 1,
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
    return null;                      // required arg missing
  }
  // argv fallbacks for the pre-provider daemon — real spawnable commands
  if (tool.id === 'open.url' && a.args) a.argv = ['xdg-open', a.args.url];
  if (tool.id === 'key.press' && a.args) a.argv = ['xdotool', 'key', a.args.keys];
  if (tool.id === 'type.text' && a.args) a.argv = ['xdotool', 'type', '--', a.args.text];
  if (tool.id === 'command' && a.args) a.argv = a.args.argv;
  return a;
}

/* ---------- sentence: a natural lower-case verb phrase with the argument,
   for the teach "Your laptop will …" line (reads like a human wrote it) ---- */
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
  if (!a) return 'do nothing yet';
  const tool = a.tool || '';
  const arg = argOf(a).trim();
  const q = s => '“' + s + '”';
  switch (tool) {
    case 'type.text': return arg ? 'type ' + q(arg) : 'type text at your cursor';
    case 'open.url': return arg ? 'open ' + arg + ' in your browser' : 'open a page in your browser';
    case 'key.press': return arg ? 'press ' + arg : 'press a key';
    case 'app.open': return 'open ' + (arg || 'the app');
    case 'app.focus': return 'bring ' + (a.args && (a.args.app || a.app) || 'the app') + ' to the front';
    case 'app.close': return 'close the window';
    case 'win.tile-left': return 'tile the window to the left';
    case 'win.tile-right': return 'tile the window to the right';
    case 'win.maximize': return 'maximize the window';
    case 'win.workspace-next': return 'switch to the next workspace';
    case 'command': return arg ? 'run ' + q(arg) + ' — after you say yes on the laptop' : 'run a command';
    case 'macro': return 'run your steps in order';
    case 'sys.screenshot': return 'take a screenshot';
    case 'sys.lock': return 'lock the screen';
  }
  if (a.kind === 'context' || a.by_wm_class) return 'do different things depending on the open app';
  if (a.kind === 'macro' || (a.args && a.args.steps)) return 'run your steps in order';
  const d = describe(a);
  return d.charAt(0).toLowerCase() + d.slice(1);
}

/* ---------- describe (library rows reuse this) ---------- */
function describe(a) {
  if (!a) return 'not bound';
  if (typeof a === 'string') return a;
  if (a.kind === 'context' || a.by_wm_class || a.per_app) return 'per-app rules';
  if (a.kind === 'macro' || a.steps || (a.args && a.args.steps))
    return ((a.steps || (a.args || {}).steps || []).length || '?') + '-step macro';
  if (a.tool && a.tool !== 'command') return a.label || a.tool;
  if (a.argv) return (Array.isArray(a.argv) ? a.argv.join(' ') : String(a.argv));
  if (a.args && a.args.argv)
    return (Array.isArray(a.args.argv) ? a.args.argv.join(' ') : String(a.args.argv));
  if (a.label) return a.label;
  if (a.type) return a.type.replace(/_/g, ' ');
  return 'action';
}

/* ---------- pick sheet ---------- */
let resolver = null;

function close(result) {
  sheet.hidden = true;
  q.value = '';
  const r = resolver; resolver = null;
  if (r) r(result || null);
}

function pick(opts) {
  opts = opts || {};
  titleEl.textContent = opts.title ||
    (opts.forGloss ? 'What should ' + opts.forGloss + ' do?' : 'Bind an action');
  sheet.hidden = false;
  App.raise(sheet);
  build();
  catalog().then(() => build());
  setTimeout(() => q.focus(), 60);
  return new Promise(res => { resolver = res; });
}

function matches(txt, it) {
  if (!txt) return true;
  return (it.label + ' ' + (it.name || '') + ' ' + (it.desc || '') + ' ' +
          (it.tool || '') + ' ' + (it.id || '')).toLowerCase().includes(txt);
}

async function build() {
  const cat = CAT || { mode: 'offline', apps: [], tools: FALLBACK_TOOLS, bound: [] };
  const query = q.value.trim().toLowerCase();
  listEl.textContent = '';
  noteEl.textContent = cat.mode === 'offline'
    ? 'laptop offline — showing the built-in actions; they send as commands once connected.'
    : cat.mode === 'basic'
    ? 'this daemon binds raw commands — update wrap on the laptop for the full action catalog (icons, window moves, volume…).'
    : '';

  /* --- apps section --- */
  const apps = (cat.apps || []).filter(a =>
    matches(query, { label: a.name, id: a.id, desc: a.generic }));
  if (apps.length) {
    const h = document.createElement('div'); h.className = 'act-sect'; h.textContent = 'Apps';
    listEl.appendChild(h);
    const grid = document.createElement('div'); grid.className = 'act-apps';
    for (const a of apps.slice(0, 60)) {
      const it = document.createElement('button');
      it.className = 'act-app';
      const icon = document.createElement('span'); icon.className = 'act-ic';
      if (iconsEnabled(cat)) {
        icon.textContent = (a.name || '?')[0].toUpperCase();      // until the real icon lands
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
      grid.appendChild(it);
    }
    listEl.appendChild(grid);
  }

  /* --- tools section --- */
  const tools = (cat.tools || []).filter(t => matches(query, t));
  if (tools.length) {
    const h = document.createElement('div'); h.className = 'act-sect'; h.textContent = 'Actions';
    listEl.appendChild(h);
    for (const t of tools) {
      const row = document.createElement('div');
      row.className = 'act-row';
      const tier = t.tier == null ? 1 : t.tier;
      row.innerHTML =
        `<div class="act-row-top"><span class="act-dot${tier >= 2 ? ' warn' : ''}"></span>` +
        `<span class="act-name">${esc(t.label || t.id)}</span>` +
        `<span class="chip ${tier >= 2 ? 'warn' : 'accent'}">${tier >= 2 ? 'asks you first' : 'instant'}</span>` +
        (t.arg ? '' : '<span class="act-chev" aria-hidden="true">›</span>') + `</div>` +
        (t.desc ? `<div class="act-desc">${esc(t.desc)}</div>` : '');
      const argWrap = document.createElement('div'); argWrap.className = 'act-arg';
      let inp = null, use = null;
      if (t.arg && t.arg !== 'app') {
        inp = document.createElement('input');
        inp.className = 'input act-input'; inp.type = 'text';
        inp.placeholder = ARG_LABEL[t.arg] || t.arg;
        inp.autocomplete = 'off'; inp.autocapitalize = 'none';
        if (t.arg === 'index') inp.inputMode = 'numeric';
        use = document.createElement('button');
        use.className = 'btn-ghost act-use'; use.textContent = 'bind';
        argWrap.append(inp, use);
        row.appendChild(argWrap);
      }
      row.onclick = e => {
        if (e.target === inp || e.target === use) return;
        if (!t.arg) close(toolAction(t));
        else if (t.arg === 'app') { inp ? inp.focus() : null; }
      };
      const commit = () => {
        const a = toolAction(t, inp && inp.value);
        if (!a) { inp.classList.add('act-bad'); inp.focus(); return; }
        close(a);
      };
      if (use) use.onclick = commit;
      if (inp) inp.onkeydown = e => { if (e.key === 'Enter') commit(); };
      if (t.arg === 'app') {
        // an 'app' arg on a tool row (focus/close) needs a target — inline pick
        inp = document.createElement('input');
        inp.className = 'input act-input'; inp.type = 'text';
        inp.placeholder = 'app name (e.g. firefox)';
        inp.autocomplete = 'off'; inp.autocapitalize = 'none';
        const sel = document.createElement('select');
        sel.className = 'input act-select';
        sel.innerHTML = '<option value="">installed app…</option>' +
          (cat.apps || []).map(a => `<option value="${esc(a.wm_class || a.id)}">${esc(a.name)}</option>`).join('');
        use = document.createElement('button');
        use.className = 'btn-ghost act-use'; use.textContent = 'bind';
        argWrap.append(inp, sel, use);
        row.appendChild(argWrap);
        sel.onchange = () => { if (sel.value) inp.value = sel.value; };
        use.onclick = () => {
          const v = inp.value.trim();
          if (!v) { inp.classList.add('act-bad'); inp.focus(); return; }
          const a = { kind: t.kind || 'app', tool: t.id, label: t.label,
                      tier: t.tier == null ? 1 : t.tier };
          if (TOOL_TYPE[t.id]) a.type = TOOL_TYPE[t.id];
          a.args = { app: v };
          a.app = v;
          /* no provider catalog -> the typed name IS the command to spawn;
             only a dict would store an action the daemon can't run */
          if (cat.mode !== 'rich') a.argv = v.split(/\s+/).filter(Boolean);
          a.label = (t.label || t.id) + ' · ' + v;
          close(a);
        };
        inp.onkeydown = e => { if (e.key === 'Enter') use.click(); };
      }
      listEl.appendChild(row);
    }
  }

  /* --- advanced: macro + context + free argv --- */
  const adv = [
    { id: '_macro', label: 'macro — chain steps', desc: 'open app → wait → type → key press… in order, stops if a step fails', tier: 1 },
    { id: '_context', label: 'per-app rule', desc: 'the same sign does different things in different apps', tier: 1 },
    { id: '_argv', label: 'terminal command', desc: 'runs a command on your laptop — it asks for a yes there first', tier: 2 },
  ].filter(x => matches(query, x));
  if (adv.length) {
    const h = document.createElement('div'); h.className = 'act-sect'; h.textContent = 'Advanced';
    listEl.appendChild(h);
    for (const x of adv) {
      const row = document.createElement('div');
      row.className = 'act-row';
      row.innerHTML =
        `<div class="act-row-top"><span class="act-dot${x.tier >= 2 ? ' warn' : ''}"></span>` +
        `<span class="act-name">${esc(x.label)}</span>` +
        `<span class="chip ${x.tier >= 2 ? 'warn' : 'accent'}">${x.tier >= 2 ? 'asks you first' : 'instant'}</span>` +
        (x.id === '_argv' ? '' : '<span class="act-chev" aria-hidden="true">›</span>') + `</div>` +
        `<div class="act-desc">${esc(x.desc)}</div>`;
      if (x.id === '_argv') {
        const argWrap = document.createElement('div'); argWrap.className = 'act-arg';
        const inp = document.createElement('input');
        inp.className = 'input act-input'; inp.type = 'text';
        inp.placeholder = 'notify-send "signed!"';
        inp.autocomplete = 'off'; inp.autocapitalize = 'none';
        const use = document.createElement('button');
        use.className = 'btn-ghost act-use'; use.textContent = 'bind';
        argWrap.append(inp, use); row.appendChild(argWrap);
        use.onclick = () => {
          const argv = inp.value.trim().split(/\s+/).filter(Boolean);
          if (!argv.length) { inp.classList.add('act-bad'); inp.focus(); return; }
          close({ kind: 'command', tool: 'command', args: { argv },
                  argv, label: 'run `' + inp.value.trim().slice(0, 40) + '`', tier: 2 });
        };
        inp.onkeydown = e => { if (e.key === 'Enter') use.click(); };
      } else {
        row.onclick = async () => {
          const a = x.id === '_macro' ? await Macro.edit() : await Macro.editContext();
          if (a) close(a); else build();
        };
      }
      listEl.appendChild(row);
    }
  }

  /* --- already bound (reference, when the daemon reports them) --- */
  const bound = (cat.bound || []).filter(b => matches(query, { label: b.label, id: b.gloss }));
  if (bound.length && !query) {
    const h = document.createElement('div'); h.className = 'act-sect'; h.textContent = 'Already bound';
    listEl.appendChild(h);
    const wrap = document.createElement('div'); wrap.className = 'act-bound';
    for (const b of bound.slice(0, 12)) {
      const chip = document.createElement('span'); chip.className = 'chip accent';
      const argv = Array.isArray(b.argv) ? b.argv.join(' ') : b.argv;
      chip.textContent = (b.gloss || '') + ' → ' + (b.label || argv || '');
      wrap.appendChild(chip);
    }
    listEl.appendChild(wrap);
  }

  setTimeout(() => sel(selIdx), 0);
  if (!listEl.children.length) {
    const d = document.createElement('div'); d.className = 'act-empty';
    d.textContent = 'Nothing matches. Try “command” to run any program.';
    listEl.appendChild(d);
  }
}
q.addEventListener('input', () => { build(); sel(0, true); });
/* keyboard: ↑/↓ move through the list, Tab or Enter completes the highlighted
   row (an app, or an action that needs no argument; one with an argument
   focuses its field). Esc cancels. */
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
    if (inp && !(r.classList.contains('act-row') && r.querySelector('.act-chev'))) inp.focus(); else r.click();
  }
});
$('act-x').onclick = () => close(null);
// clicking the dimmed scrim (desktop modal) or hitting Esc cancels —
// same cancel path as the ×
sheet.addEventListener('click', e => { if (e.target === sheet) close(null); });
sheet.addEventListener('keydown', e => { if (e.key === 'Escape') close(null); });

export const Actions = { pick, catalog, describe, sentence,
                         _cat: () => CAT };      // selftest debug seam
