// panes.js — plain, working defaults for the two sidebar pages that would
// otherwise be blank: "Connected laptop" (#/laptop) and "Settings" (#/settings).
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
  lap.innerHTML = head('Connected laptop') + `<div class="sheet-body pn-body">` + (ok
    ? `<div class="pn-hero"><i class="pn-dot on"></i><div><div class="pn-name">${esc(name)}</div><div class="pn-sub">Connected</div></div></div>
       ${row('Typing into', esc(s.focus || 'Nothing focused yet'))}
       ${row('Can run actions', l.canAct === false ? 'No' : 'Yes')}
       ${row('Signs it knows', s.bankSize != null ? esc(s.bankSize) : '—')}
       <button class="btn btn-ghost pn-btn" id="pn-unpair">Disconnect this phone</button>`
    : `<div class="pn-hero"><i class="pn-dot"></i><div><div class="pn-name">Not connected</div><div class="pn-sub">${s.online ? 'Your laptop is there, but this phone needs its code.' : 'Your laptop is not answering.'}</div></div></div>
       <p class="hint pn-p">Run <code>wrap start</code> on your laptop, then enter the code it shows. Text, photos and files go to your laptop; signs and the @ picker turn on once it is connected.</p>
       <button class="btn btn-primary pn-btn" id="pn-code">Enter a code</button>`) + `</div>`;
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
App.route('laptop', {
  el: lap,
  open() { renderLaptop(); App.pollStatus(); lapT = setInterval(renderLaptop, 2500); },
  close() { clearInterval(lapT); },
});

/* ---------- Settings ---------- */
const set = App.el.sheetSettings;
const get = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } };
const put = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
function renderSettings() {
  const haptics = get('wf.haptics', '1') !== '0', mode = get('wf.livemode', 'control');
  set.innerHTML = head('Settings') + `<div class="sheet-body pn-body">
    <div class="pn-group">Feel</div>
    <button class="pn-row pn-tap" id="pn-hap" role="switch" aria-checked="${haptics}"><span class="pn-k">Vibrate on send</span><span class="pn-sw" aria-hidden="true"></span></button>
    <div class="pn-group">Go Live</div>
    <div class="pn-row"><span class="pn-k">Open in</span>
      <span class="pn-seg" role="tablist"><button role="tab" data-m="control" aria-selected="${mode !== 'text'}">Control</button><button role="tab" data-m="text" aria-selected="${mode === 'text'}">Text</button></span></div>
    <div class="pn-group">Privacy</div>
    <p class="hint pn-p">Camera video never leaves this phone. Only hand points are sent, and only to your own laptop. Nothing is stored in the cloud.</p>
    <div class="pn-group">This phone</div>
    <button class="btn btn-ghost pn-btn" id="pn-unpair2">Disconnect from this laptop</button>
    <p class="pn-foot">Wispr Wrap · open source</p>
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
App.route('settings', { el: set, open: renderSettings, close() {} });
