// drawer.js — the hamburger sidebar (FOUNDER-UI-SPEC-1026 §3).
// Slides in from the left over a dim scrim. Rows: Teach a sign, My signs,
// Connected laptop, Settings. Laptop-dependent features are gated on
// state.paired — unpaired shows a single "Connect your laptop" row.
// The drawer builds its own DOM inside #drawer-root (or body) and owns the
// top-left hamburger button, so no other file needs to know its internals.
import { App } from './app.js';

const root = document.getElementById('drawer-root') || document.body;
root.insertAdjacentHTML('beforeend', `
<button class="dr-btn" id="menu-btn" aria-label="Menu" aria-haspopup="dialog" aria-expanded="false">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
</button>
<div class="dr-scrim" id="dr-scrim" hidden></div>
<nav class="dr" id="dr" role="dialog" aria-modal="true" aria-label="Menu" hidden>
  <div class="dr-head">
    <svg class="dr-mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9"/></svg>
    <span class="dr-brand">wispr<span>wrap</span></span>
  </div>
  <div class="dr-body" id="dr-body"></div>
  <div class="dr-foot">private · video never leaves your phone</div>
</nav>`);

const btn = document.getElementById('menu-btn'),
      scrim = document.getElementById('dr-scrim'),
      dr = document.getElementById('dr'),
      body = document.getElementById('dr-body');

const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function row(label, sub, fn, opts) {
  opts = opts || {};
  const b = document.createElement('button');
  b.className = 'dr-row' + (opts.dim ? ' dim' : '');
  b.innerHTML = `<span class="dr-ico">${opts.icon || ''}</span>` +
    `<span class="dr-tx"><span class="dr-l">${esc(label)}</span>` +
    (sub ? `<span class="dr-s">${esc(sub)}</span>` : '') + '</span>' +
    `<svg class="dr-go" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>`;
  b.onclick = fn;
  return b;
}
const I = {
  teach: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  signs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
  laptop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="12" rx="2"/><path d="M2 20h20"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7 7 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.06-.4.1-.8.1-1.2Z"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>',
};

function connected() { return !!(App.state.online && App.state.paired); }

function render() {
  const s = App.state;
  body.textContent = '';
  if (!connected()) {
    // spec §3: laptop features exist ONLY when a laptop is connected.
    body.appendChild(row('Connect your laptop',
      s.online ? 'it needs the pairing code' : 'run: wrap start on the laptop',
      () => { close(); App.emit('unpaired'); }, { icon: I.link }));
    return;
  }
  const name = App.store.getItem('wfname') || 'Laptop';
  body.appendChild(row('Teach a sign', 'show it a few times, pick what it does',
    () => { close(); App.nav('teach'); }, { icon: I.teach }));
  body.appendChild(row('My signs',
    s.bankSize ? s.bankSize + ' taught' : '',
    () => { close(); App.nav('signs'); }, { icon: I.signs }));
  body.appendChild(row('Connected laptop', name + (s.focus ? ' · typing into ' + s.focus : ''),
    () => { close(); App.nav('signs'); }, { icon: I.laptop }));
  body.appendChild(row('Settings', '', openSettings, { icon: I.gear }));
}

/* ---------- settings (inline subview — simple rows, no new sheet files) --- */
function openSettings() {
  body.textContent = '';
  const h = document.createElement('div');
  h.className = 'dr-subhead';
  h.innerHTML = '<button class="dr-back" aria-label="Back">‹</button><span>Settings</span>';
  h.querySelector('.dr-back').onclick = render;
  body.appendChild(h);
  const name = App.store.getItem('wfname') || 'this laptop';
  body.appendChild(row('Reconnect laptop', 'pair again with a new code',
    () => { close(); App.emit('unpaired'); }, { icon: I.link }));
  const info = document.createElement('div');
  info.className = 'dr-info';
  info.innerHTML = `<div><span>laptop</span><b>${esc(name)}</b></div>` +
    `<div><span>signs</span><b>${App.state.bankSize || 0}</b></div>`;
  body.appendChild(info);
}

function open() {
  render();
  dr.hidden = false; scrim.hidden = false;
  requestAnimationFrame(() => { dr.classList.add('open'); scrim.classList.add('open'); });
  btn.setAttribute('aria-expanded', 'true');
}
function close() {
  dr.classList.remove('open'); scrim.classList.remove('open');
  btn.setAttribute('aria-expanded', 'false');
  setTimeout(() => { dr.hidden = true; scrim.hidden = true; }, 260);
}
function toggle() { dr.hidden ? open() : close(); }

btn.onclick = toggle;
scrim.onclick = close;
addEventListener('keydown', e => { if (e.key === 'Escape' && !dr.hidden) close(); });

/* re-render the rows whenever connection state changes while open */
App.on('statusbar', () => { if (!dr.hidden) render(); });

App.drawer = { open, close, toggle, render };
