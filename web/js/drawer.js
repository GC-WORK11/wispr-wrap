// drawer.js — sidebar gating.
// The drawer frame (#drawer, #menu, #scrim, row markup) and App.drawer
// live elsewhere; this module only decides what the rows MEAN right now:
// nothing laptop-powered is offered while no laptop is connected —
// the sidebar leads with one simple "Connect your laptop" row instead.
import { App } from './app.js';

const nav = App.drawer && App.drawer.nav;
if (nav) {
  // the connect call-to-action — inserted above the nav rows, only while
  // the laptop link is down. Tapping it opens the Laptop page, which
  // carries the code entry.
  const cta = document.createElement('button');
  cta.className = 'dr-item dr-cta';
  cta.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2 19h20"/></svg>' +
    '<span class="dr-t">Connect your laptop</span>';
  cta.onclick = () => { App.drawer.close(); App.nav('laptop'); };

  const teach = document.getElementById('tray-teach');
  const signs = document.getElementById('tray-signs');

  function gate() {
    const ok = App.connected();
    cta.hidden = ok;
    if (cta.parentNode !== nav.firstElementChild && !ok) nav.prepend(cta);
    else if (ok && cta.parentNode) cta.remove();
    // teach/signs stay tappable (they explain themselves) but dim honestly
    for (const el of [teach, signs]) if (el) el.classList.toggle('dis', !ok);
  }
  App.on('statusbar', gate);
  App.on('route', gate);
  gate();
}
