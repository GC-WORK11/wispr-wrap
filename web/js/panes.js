// panes.js — plain, working defaults for the two sidebar pages that would
// otherwise be blank: "Laptop" (#/laptop) and "Settings" (#/settings).
// Devin PRO owns the richer versions (js/laptop.js, js/settings.js): they load
// AFTER this file and register the same routes, so theirs win. Real data only.
import { App } from './app.js';

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ICON = {
  ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
};
const head = title => `<div class="sheet-head"><h1>${title}</h1><button class="sheet-x" data-close aria-label="Close">×</button></div>`;
const row = (k, v) => `<div class="pn-row"><span class="pn-k">${k}</span><span class="pn-v">${v}</span></div>`;

/* ---------- Connected laptop ---------- */
const lap = App.el.sheetLaptop;
function renderLaptop() {
  const s = App.state, ok = App.connected(), l = s.laptop || {};
  const name = l.name || localStorage.getItem('wfname') || 'Your laptop';
  lap.innerHTML = head('Laptop') + `<div class="sheet-body pn-body">` + (ok
    ? `<div class="pn-hero"><i class="pn-dot on"></i><div class="pn-name">${esc(name)}</div></div>
       ${row('Typing into', esc(s.focus || '—'))}
       <button class="btn btn-ghost pn-btn" id="pn-unpair">Forget this laptop</button>`
    : `<div class="pn-hero"><i class="pn-dot"></i></div>
       <button class="btn btn-primary pn-btn" id="pn-code">Connect</button>`) + `</div>`;
  lap.querySelector('[data-close]').onclick = () => App.back();
  const un = lap.querySelector('#pn-unpair');
  if (un) un.onclick = () => unpair();
  const cd = lap.querySelector('#pn-code');
  if (cd) cd.onclick = () => App.connect();
}
function unpair() {
  App.wispr.unpair();
  try { localStorage.removeItem('wfk'); localStorage.removeItem('wfname'); } catch (e) {}
  location.replace(location.pathname);
}
let lapT;
const lapRoute = {
  el: lap,
  open() { renderLaptop(); App.pollStatus(); lapT = setInterval(renderLaptop, 2500); },
  close() { clearInterval(lapT); },
};

/* ---------- Settings ---------- */
const set = App.el.sheetSettings;
const get = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } };
const put = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
function renderSettings() {
  const haptics = get('wf.haptics', '1') !== '0', mode = get('wf.livemode', 'control');
  set.innerHTML = head('Settings') + `<div class="sheet-body pn-body">
    <button class="pn-row pn-tap" id="pn-hap" role="switch" aria-checked="${haptics}"><span class="pn-k">Vibrate on send</span><span class="pn-sw" aria-hidden="true"></span></button>
    <div class="pn-row"><span class="pn-k">Go Live</span>
      <span class="pn-seg" role="tablist"><button role="tab" data-m="control" aria-selected="${mode !== 'text'}">Control</button><button role="tab" data-m="text" aria-selected="${mode === 'text'}">Text</button></span></div>
    <button class="btn btn-ghost pn-btn" id="pn-unpair2">Forget this laptop</button>
  </div>`;
  set.querySelector('[data-close]').onclick = () => App.back();
  set.querySelector('#pn-hap').onclick = e => {
    const on = e.currentTarget.getAttribute('aria-checked') !== 'true';
    put('wf.haptics', on ? '1' : '0'); e.currentTarget.setAttribute('aria-checked', String(on)); if (on) App.buzz();
  };
  for (const b of set.querySelectorAll('.pn-seg button')) b.onclick = () => {
    put('wf.livemode', b.dataset.m);
    for (const x of set.querySelectorAll('.pn-seg button')) x.setAttribute('aria-selected', String(x === b));
  };
  set.querySelector('#pn-unpair2').onclick = unpair;
}
const setRoute = { el: set, open: renderSettings, close() {} };

// laptop.js / settings.js load after this file and own these routes. Register the plain defaults only when,
// once every module has run, nobody did. (Registering first left this file's timer repainting over the real
// Laptop page on a reload at #/laptop.)
const afterModules = f => document.readyState === 'loading' ? addEventListener('DOMContentLoaded', f, { once: true }) : setTimeout(f, 0);
afterModules(() => {
  if (!App.routes.laptop) App.route('laptop', lapRoute);
  if (!App.routes.settings) App.route('settings', setRoute);
});
