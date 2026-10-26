// library.js — the sign library, mounted in #sheet-signs (route 'signs').
//
//   GET    /bank            -> {ok, signs:[{gloss,label,n_exemplars,tier,
//                              taught,bound,argv,args,action}]}
//   PUT    /bank {gloss,label}                — rename (daemon stores label)
//   PUT    /bank {gloss,argv|action,tier}     — rebind via the @ picker
//   DELETE /bank?gloss=                       — remove sign + binding
//   + take -> Teach.openAdd(gloss) — one more exemplar through the wizard
import { App } from './app.js';
import { Actions } from './actions.js';
import { Teach } from './teach.js';

const sheet = App.el.sheetSigns;
if (!sheet) throw new Error('library: #sheet-signs missing');

sheet.innerHTML = `
  <div class="sheet-head">
    <h1>your signs</h1>
    <span class="sub" id="lib-count"></span>
    <button class="sheet-x" id="lib-x" aria-label="Close">×</button>
  </div>
  <div class="lib-banner" id="lib-banner" hidden>
    <span id="lib-banner-t"></span><button id="lib-banner-b" hidden>retry</button>
  </div>
  <div class="sheet-body">
    <section id="lib-list"></section>
    <div class="lib-empty" id="lib-empty" hidden>
      <span class="lib-empty-g" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M12 5v14M5 12h14"/>
        </svg>
      </span>
      no signs yet — <b>teach your first one</b>.<br>
      a sign is a gesture <i>you</i> choose, bound to an action on your laptop.<br>
      three takes, ~90 seconds, runs offline.
    </div>
  </div>
  <div class="sheet-foot">
    <button class="btn btn-primary btn-block" id="lib-teach">+ teach a sign</button>
  </div>`;
const $ = id => sheet.querySelector('#' + id);
const listEl = $('lib-list'), emptyEl = $('lib-empty'), countEl = $('lib-count'),
      banner = $('lib-banner'), bannerT = $('lib-banner-t'), bannerB = $('lib-banner-b'),
      teachBtn = $('lib-teach');

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let rows = [];

function actSummary(s) {
  const a = s.action;
  if (a && typeof a === 'object') return Actions.describe(a);
  if (typeof a === 'string' && a) return a;
  if (s.argv) return Array.isArray(s.argv) ? s.argv.join(' ') : s.argv;
  return 'no action bound';
}

function render() {
  listEl.textContent = '';
  emptyEl.hidden = rows.length > 0;
  countEl.textContent = rows.length ? rows.length + (rows.length === 1 ? ' sign' : ' signs') : '';
  for (const s of rows) {
    const card = document.createElement('div');
    card.className = 'lib-card';
    const n = s.n_exemplars != null ? s.n_exemplars
      : (s.exemplar_count != null ? s.exemplar_count
      : (s.exemplars ? s.exemplars.length : '?'));
    const tier = s.tier || 0;
    card.innerHTML =
      `<div class="lib-top"><span class="lib-gl">${esc(s.gloss)}</span>` +
      `<span class="lib-name">${esc(s.label && s.label !== s.gloss ? s.label : '')}</span>` +
      `<span class="chip">${n} exemplar${n === 1 ? '' : 's'}</span></div>` +
      `<div class="lib-act"><span class="lib-act-v">${esc(actSummary(s))}</span>` +
      (tier >= 2 ? '<span class="chip warn">confirm</span>' : '') +
      (s.taught === false ? '<span class="chip accent">bound</span>' : '') +
      `</div>`;
    const row = document.createElement('div'); row.className = 'lib-row';
    const mk = (txt, cls, fn) => {
      const b = document.createElement('button');
      b.className = 'btn-ghost lib-b' + (cls ? ' ' + cls : '');
      b.textContent = txt; b.onclick = fn; row.appendChild(b); return b;
    };
    if (s.taught !== false) mk('+ take', '', () => { App.nav('teach'); setTimeout(() => Teach.openAdd(s.gloss), 80); });
    mk('rebind @', '', () => rebind(card, s));
    mk('rename', '', () => startRename(card, s, row));
    const del = mk('delete', 'danger', async () => {
      if (del.dataset.armed) {
        del.textContent = '…'; del.disabled = true;
        const r = await App.api.del('/bank?gloss=' + encodeURIComponent(s.gloss)).catch(() => null);
        if (r && r.ok) { rows = rows.filter(x => x.gloss !== s.gloss); render(); App.toast('deleted ' + s.gloss); }
        else if (r && r.status === 404) bannerShow('no such sign — refreshed', true);
        else if (r && r.status === 403) App.toast('Not connected — scan the code again.', true);
        else { App.toast('delete failed', true); del.disabled = false; del.textContent = 'delete'; }
        delete del.dataset.armed;
      } else {
        del.dataset.armed = '1'; del.textContent = 'sure?';
        setTimeout(() => { if (del.isConnected) { delete del.dataset.armed; del.textContent = 'delete'; } }, 2600);
      }
    });
    card.appendChild(row);
    listEl.appendChild(card);
  }
}

function bannerShow(text, retry) {
  if (!text) { banner.hidden = true; return; }
  banner.hidden = false;
  bannerT.textContent = text;
  bannerB.hidden = !retry;
  bannerB.onclick = retry ? refresh : null;
}

async function rebind(card, s) {
  const a = await Actions.pick({ forGloss: s.gloss, title: 'rebind ' + s.gloss });
  if (!a) return;
  const body = { gloss: s.gloss, tier: a.tier == null ? s.tier || 0 : a.tier, action: a };
  if (Array.isArray(a.argv) && a.argv.length) body.argv = a.argv;
  const r = await App.api.put('/bank', body).catch(() => null);
  if (r && r.ok) { App.toast('rebound ' + s.gloss); refresh(); }
  else if (r && r.status === 403) App.toast('Not connected — scan the code again.', true);
  else App.toast(App.daemonErr(r, 'Couldn’t save that — try again.'), true);
}

function startRename(card, s, row) {
  row.style.display = 'none';
  const rr = document.createElement('div');
  rr.className = 'lib-rename';
  const inp = document.createElement('input');
  inp.className = 'input'; inp.type = 'text';
  inp.value = s.label || s.gloss; inp.maxLength = 48;
  const ok = document.createElement('button'); ok.className = 'btn-primary lib-b'; ok.textContent = 'save';
  const no = document.createElement('button'); no.className = 'btn-ghost lib-b'; no.textContent = 'cancel';
  rr.append(inp, ok, no); card.appendChild(rr); inp.focus(); inp.select();
  const cleanup = () => { rr.remove(); row.style.display = ''; };
  no.onclick = cleanup;
  ok.onclick = async () => {
    const label = inp.value.trim(); if (!label) return cleanup();
    ok.disabled = true; ok.textContent = '…';
    const r = await App.api.put('/bank', { gloss: s.gloss, label }).catch(() => null);
    if (r && r.ok) {
      /* the card object may be stale — a refresh() can replace rows while
         the PUT is in flight. Patch every row sharing this gloss. */
      rows.forEach(rw => { if (rw.gloss === s.gloss) rw.label = label; });
      s.label = label;
      render(); App.toast('renamed');
    }
    else if (r && r.status === 403) { App.toast('Not connected — scan the code again.', true); cleanup(); }
    else { App.toast('rename failed', true); ok.disabled = false; ok.textContent = 'save'; }
  };
  inp.onkeydown = e => { if (e.key === 'Enter') ok.click(); if (e.key === 'Escape') cleanup(); };
}

async function refresh() {
  const r = await App.api.get('/bank').catch(() => null);
  if (r && r.ok && r.json) {
    bannerShow(null);
    rows = Array.isArray(r.json.signs) ? r.json.signs : (Array.isArray(r.json) ? r.json : []);
    teachBtn.disabled = false;
  } else if (r && r.status === 404) {
    bannerShow('This laptop runs an older version — update it to manage signs.');
    teachBtn.disabled = true;
  } else if (r && r.status === 403) {
    bannerShow('Not connected — scan the code again.');
    teachBtn.disabled = true;
  } else {
    bannerShow('Can’t reach your laptop.', true);
    teachBtn.disabled = true;
  }
  render();
}

$('lib-x').onclick = () => App.back();
teachBtn.onclick = () => App.nav('teach');

App.route('signs', { el: sheet, open: refresh, close() {} });
