// packs.js — ready-made signs rendered inside the Teach name step.
// TEACH stage owns this file. Real contract:
//   GET  /packs            -> {ok, packs:[{id,gloss,title,state,takes,action}]}
//   POST /packs/install {id} -> {ok,id,gloss,takes,state,source}
// states: ready -> "Add" (install, refresh, toast); needs_takes -> "Teach it"
// (hands back to Teach to record the 3 takes); built-in -> "Built in" (not
// selectable, copy says it pauses listening). The whole section hides if the
// request is not ok. Plain list rows — no cards, no chips.
import { App } from './app.js';

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let hooks = {};

async function load() {
  const r = await App.api.get('/packs').catch(() => null);
  if (!r || !r.ok || !r.json || !Array.isArray(r.json.packs)) return null;
  return r.json.packs;
}

function render(el, packs) {
  if (!packs || !packs.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = `<h2 class="tw-ask-t tw-packs-t">Ready-made signs</h2>
    <div class="tw-packs-list"></div>`;
  const list = el.querySelector('.tw-packs-list');
  for (const p of packs) {
    const row = document.createElement('div');
    row.className = 'tw-pk';
    row.dataset.id = p.id;
    let right;
    if (p.state === 'ready') right = `<button class="tw-pk-do" data-act="add">Add</button>`;
    else if (p.state === 'built-in') right = `<span class="tw-pk-off">Built in</span>`;
    else right = `<button class="tw-pk-do" data-act="teach">Teach it</button>`;
    row.innerHTML =
      `<div class="tw-pk-tx"><span class="tw-pk-n">${esc(p.title)}</span></div>` +
      `<div class="tw-pk-r">${right}</div>`;
    const btn = row.querySelector('.tw-pk-do');
    if (btn) btn.onclick = () => (btn.dataset.act === 'add' ? install(p, row) : teachIt(p));
    list.appendChild(row);
  }
}

async function install(p, row) {
  const btn = row.querySelector('.tw-pk-do');
  if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }
  const r = await App.api.post('/packs/install', { id: p.id }).catch(() => null);
  if (!r || !r.ok) {
    if (btn) { btn.disabled = false; btn.textContent = 'Add'; }
    App.toast('Could not add that one — try again', true);
    return;
  }
  App.toast('Added “' + p.title + '”');
  try { window.dispatchEvent(new CustomEvent('wf:sign-ready')); } catch (e) {}
  if (hooks.onDone) hooks.onDone();
  refresh();
}

function teachIt(p) {
  if (hooks.onStart) hooks.onStart(p);
}

let rootEl = null;
async function refresh() {
  if (!rootEl) return;
  const packs = await load();
  render(rootEl, packs);
}

export const Packs = {
  async mount(el, opts = {}) {
    rootEl = el; hooks = opts;
    const packs = await load();
    if (!packs) { el.hidden = true; return; }
    render(el, packs);
  },
  refresh,
};
