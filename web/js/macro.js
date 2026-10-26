// macro.js — macro builder + per-app context rules, in #sheet-macro.
//
//   Macro.edit(action?)         -> Promise<action|null>   macro chain editor
//   Macro.editContext(action?)  -> Promise<action|null>   context rules editor
//   Macro.describe(action)      -> human label
//
// Emits the APP_API_CONTRACT wire shapes macro.py / act_iface normalize:
//   macro:   {kind:'macro', tool:'macro', args:{steps:[
//              {step:'open_app',exec}|{step:'wait_ms',ms}|{step:'type',text}|
//              {step:'keys',keys}|{step:'argv',argv:[..]}|{tool:id,args:{..}}
//            ]}, label, tier}
//   context: {kind:'context', by_wm_class:{wc: action}, default: action, tier}
// Steps run SERIALLY, stop on first failed non-optional step (macro.py
// semantics); a step carrying argv makes the whole macro tier 2 — the row
// says 'laptop confirm' honestly.
import { App } from './app.js';
import { Actions } from './actions.js';

const sheet = App.el.sheetMacro;
if (!sheet) throw new Error('macro: #sheet-macro missing');

sheet.innerHTML = `
  <div class="sheet-card">
    <div class="sheet-head">
      <h1 id="mc-title">macro</h1>
      <button class="sheet-x" id="mc-x" aria-label="Cancel">×</button>
    </div>
    <div class="sheet-body" id="mc-body"></div>
    <div class="sheet-foot">
      <button class="btn btn-ghost btn-block" id="mc-cancel">cancel</button>
      <button class="btn btn-primary btn-block" id="mc-done" disabled>bind macro</button>
    </div>
  </div>`;
const $ = id => sheet.querySelector('#' + id);
const body = $('mc-body'), titleEl = $('mc-title'), done = $('mc-done');

let resolver = null, mode = 'macro', steps = [], rules = [], defAction = null;

function finish(a) {
  sheet.hidden = true;
  const r = resolver; resolver = null;
  if (r) r(a || null);
}
$('mc-x').onclick = () => finish(null);
$('mc-cancel').onclick = () => finish(null);
// scrim click (desktop modal) / Esc — same cancel path as × and cancel
sheet.addEventListener('click', e => { if (e.target === sheet) finish(null); });
sheet.addEventListener('keydown', e => { if (e.key === 'Escape') finish(null); });

/* ---------- step kinds (macro.py normalize_step wire shapes) ---------- */
const STEP_KINDS = [
  { k: 'open_app', label: 'open app', ph: 'firefox — or a .desktop id' },
  { k: 'wait_ms', label: 'wait', ph: 'milliseconds (max 60000)', num: true },
  { k: 'type', label: 'type text', ph: 'text at the cursor' },
  { k: 'keys', label: 'key press', ph: 'ctrl+alt+t' },
  { k: 'argv', label: 'terminal command', ph: 'notify-send "done"', tier2: true },
];

function stepRow(st, i) {
  const row = document.createElement('div');
  row.className = 'mc-step';
  const k = STEP_KINDS.find(x => x.k === st._k);
  row.innerHTML =
    `<span class="mc-n">${i + 1}</span>` +
    `<span class="mc-k">${k ? k.label : st._k}</span>` +
    (st._k === 'tool' ? `<span class="mc-t">${st.tool}</span>` : '');
  const inp = document.createElement('input');
  inp.className = 'input mc-in';
  inp.placeholder = k ? k.ph : 'args';
  inp.value = st._v || '';
  if (k && k.num) inp.inputMode = 'numeric';
  inp.oninput = () => { st._v = inp.value; syncDone(); };
  const del = document.createElement('button');
  del.className = 'mc-del'; del.textContent = '×';
  del.title = 'remove step';
  del.onclick = () => { steps.splice(steps.indexOf(st), 1); render(); };
  row.append(inp, del);
  return row;
}

function addStepRow() {
  const bar = document.createElement('div');
  bar.className = 'mc-add';
  for (const k of STEP_KINDS) {
    const b = document.createElement('button');
    b.className = 'mc-chip';
    b.textContent = '+ ' + k.label;
    b.onclick = () => { steps.push({ _k: k.k, _v: '' }); render(); };
    bar.appendChild(b);
  }
  // any catalog tool can be a step (macro.py {tool,args} passthrough)
  const t = document.createElement('button');
  t.className = 'mc-chip'; t.textContent = '+ any @ action';
  t.onclick = async () => {
    const cat = await Actions.catalog();
    const tools = (cat.tools || []).filter(x => x.id && !x.id.startsWith('_'));
    if (!tools.length) { App.toast('no tool catalog on this daemon', true); return; }
    const pick = await pickTool(tools);
    if (pick) { steps.push({ _k: 'tool', tool: pick.id, _v: '', _tool: pick }); render(); }
  };
  bar.appendChild(t);
  return bar;
}

/* a tiny inline tool picker for macro steps (the full @ sheet stays level-1) */
function pickTool(tools) {
  return new Promise(res => {
    const wrap = document.createElement('div');
    wrap.className = 'mc-toolpick';
    const back = document.createElement('button');
    back.className = 'mc-chip'; back.textContent = '‹ back';
    back.onclick = () => { wrap.remove(); res(null); };
    wrap.appendChild(back);
    for (const t of tools.slice(0, 40)) {
      const b = document.createElement('button');
      b.className = 'mc-tool';
      b.textContent = (t.label || t.id);
      b.onclick = () => { wrap.remove(); res(t); };
      wrap.appendChild(b);
    }
    body.appendChild(wrap);
  });
}

function render() {
  body.textContent = '';
  if (mode === 'macro') {
    const lead = document.createElement('p');
    lead.className = 'hint';
    lead.textContent = 'steps run in order; the chain stops on the first failure. a terminal command asks for a yes on the laptop the first time.';
    body.appendChild(lead);
    steps.forEach((st, i) => body.appendChild(stepRow(st, i)));
    body.appendChild(addStepRow());
    done.textContent = steps.length ? 'bind ' + steps.length + '-step macro' : 'bind macro';
    done.disabled = !steps.length || steps.some(s => s._k !== 'tool' && !(s._v || '').trim());
    done.classList.toggle('btn-danger', steps.some(s => s._k === 'argv'));
  } else {
    const lead = document.createElement('p');
    lead.className = 'hint';
    lead.textContent = 'the same sign does different things depending on which app is in front. pick the app; “default” fires when none match.';
    body.appendChild(lead);
    rules.forEach((r, i) => body.appendChild(ruleRow(r, i)));
    const add = document.createElement('button');
    add.className = 'mc-addone'; add.textContent = '+ rule';
    add.onclick = async () => {
      const cat = await Actions.catalog();
      const rr = { app: '', action: null };
      rules.push(rr); render();
    };
    body.appendChild(add);
    const drow = document.createElement('div');
    drow.className = 'mc-def';
    const dl = document.createElement('span');
    dl.className = 'mc-rt'; dl.textContent = 'default';
    const arw = document.createElement('span');
    arw.className = 'mc-arrow'; arw.setAttribute('aria-hidden', 'true'); arw.textContent = '→';
    const db = document.createElement('button');
    db.className = 'mc-chip mc-act'; db.textContent = defAction ? Actions.describe(defAction) : 'pick action…';
    db.onclick = async () => {
      const a = await Actions.pick({ title: 'default action (no rule matched)' });
      if (a) { defAction = a; render(); }
    };
    drow.append(dl, arw, db);
    body.appendChild(drow);
    done.textContent = 'bind context rule';
    done.disabled = !rules.length || rules.some(r => !r.app || !r.action);
    done.classList.remove('btn-danger');
  }
}

function ruleRow(r, i) {
  const row = document.createElement('div');
  row.className = 'mc-rule';
  row.innerHTML =
    '<div class="mc-rule-top"><span class="mc-rt">when</span></div>' +
    '<div class="mc-rule-act"><span class="mc-arrow" aria-hidden="true">→</span></div>';
  const sel = document.createElement('input');
  sel.className = 'input mc-in'; sel.placeholder = 'app name, e.g. firefox';
  sel.value = r.app;
  sel.oninput = () => { r.app = sel.value.trim(); syncDone(); };
  Actions.catalog().then(cat => {
    if (!(cat.apps || []).length) return;
    sel.setAttribute('list', 'mc-apps');
    if (!sheet.querySelector('#mc-apps')) {
      const dl = document.createElement('datalist'); dl.id = 'mc-apps';
      for (const a of cat.apps) {
        const o = document.createElement('option');
        o.value = a.wm_class || a.id; o.label = a.name;
        dl.appendChild(o);
      }
      body.appendChild(dl);
    }
  });
  const act = document.createElement('button');
  act.className = 'mc-chip mc-act';
  act.textContent = r.action ? Actions.describe(r.action) : 'pick action…';
  act.onclick = async () => {
    const a = await Actions.pick({ title: 'when ' + (r.app || 'app') + ' is focused…' });
    if (a) { r.action = a; render(); }
  };
  const del = document.createElement('button');
  del.className = 'mc-del'; del.textContent = '×';
  del.title = 'remove rule';
  del.onclick = () => { rules.splice(i, 1); render(); };
  row.querySelector('.mc-rule-top').append(sel, del);
  row.querySelector('.mc-rule-act').append(act);
  return row;
}

function syncDone() {
  if (mode === 'macro') done.disabled = !steps.length || steps.some(s => s._k !== 'tool' && !(s._v || '').trim());
  else done.disabled = !rules.length || rules.some(r => !r.app || !r.action);
}

done.onclick = () => {
  if (mode === 'macro') {
    const out = steps.map(st => {
      if (st._k === 'tool') return { tool: st.tool, args: st._v ? { arg: st._v } : {} };
      const v = st._v.trim();
      if (st._k === 'wait_ms') return { step: 'wait_ms', ms: +v || 0 };
      if (st._k === 'open_app') return { step: 'open_app', exec: v };
      if (st._k === 'type') return { step: 'type', text: v };
      if (st._k === 'keys') return { step: 'keys', keys: v };
      if (st._k === 'argv') return { step: 'argv', argv: v.split(/\s+/).filter(Boolean) };
      return null;
    }).filter(Boolean);
    const tier2 = steps.some(s => s._k === 'argv');
    finish({ kind: 'macro', tool: 'macro',
             args: { steps: out },
             label: steps.length + '-step macro', tier: tier2 ? 2 : 1 });
  } else {
    const by = {};
    for (const r of rules) if (r.app && r.action) by[r.app] = r.action;
    const a = { kind: 'context', by_wm_class: by, label: 'per-app rules', tier: 1 };
    if (defAction) a.default = defAction;
    finish(a);
  }
};

export const Macro = {
  edit(action) {
    mode = 'macro';
    steps = (action && (action.steps || (action.args || {}).steps) || [])
      .map(s => ({ _k: s.step || 'tool', _v: s.exec || s.text || s.keys ||
                  (s.ms != null ? String(s.ms) : '') ||
                  (Array.isArray(s.argv) ? s.argv.join(' ') : ''),
                  tool: s.tool }));
    if (!steps.length) steps = [{ _k: 'open_app', _v: '' }];
    titleEl.textContent = 'macro — chain steps';
    sheet.hidden = false;
    App.raise(sheet);
    render();
    return new Promise(res => { resolver = res; });
  },
  editContext(action) {
    mode = 'context';
    const by = (action && (action.by_wm_class || action.per_app)) || {};
    rules = Object.keys(by).map(k => ({ app: k, action: by[k] }));
    if (!rules.length) rules = [{ app: '', action: null }];
    defAction = (action && action.default) || null;
    titleEl.textContent = 'context — per-app rules';
    sheet.hidden = false;
    App.raise(sheet);
    render();
    return new Promise(res => { resolver = res; });
  },
  describe(a) {
    if (!a) return '';
    if (a.kind === 'context') return 'per-app rules';
    const n = (a.steps || (a.args || {}).steps || []).length;
    return n ? n + '-step macro' : (a.label || 'macro');
  },
};
