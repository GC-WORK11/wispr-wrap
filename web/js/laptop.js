// laptop.js — PRO lane: the "Laptop" page (#/laptop).
// A picture, not a paragraph: the laptop glyph with a dot (green = connected),
// its name, and — when it isn't connected — the code field and Connect, right here.
import { App } from './app.js';

const sheet = App.el.sheetLaptop;
if (!sheet) throw new Error('laptop: #sheet-laptop missing');

const LAPTOP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2 19h20"/></svg>';

sheet.innerHTML = `
  <div class="sheet-head">
    <h1>Laptop</h1>
    <button class="sheet-x" id="lp-x" aria-label="Close">×</button>
  </div>
  <div class="sheet-body lp-body">
    <div class="lp-hero">
      <span class="lp-ico" id="lp-ico">${LAPTOP}<i class="lp-dot"></i></span>
      <div class="lp-name" id="lp-name"></div>
    </div>
    <p class="lp-state" id="lp-state" role="status">Checking…</p>
    <p class="hint lp-sub" id="lp-sub" hidden></p>
    <div class="lp-rows" id="lp-rows"></div>
    <p class="hint lp-help" id="lp-help" hidden></p>
    <form class="lp-foot" id="lp-form" novalidate>
      <input class="onb-code" id="lp-code" maxlength="8" placeholder="········" inputmode="text" autocomplete="off"
             autocapitalize="characters" autocorrect="off" spellcheck="false" aria-label="Laptop pairing code" hidden>
      <button class="btn btn-primary" id="lp-connect" type="submit" hidden>Connect</button>
      <div class="onb-err" id="lp-err" role="alert"></div>
      <button class="btn btn-ghost" id="lp-forget" type="button" hidden>Forget this laptop</button>
    </form>
  </div>`;

const stateEl = sheet.querySelector('#lp-state'),
      icoEl = sheet.querySelector('#lp-ico'),
      nameEl = sheet.querySelector('#lp-name'),
      subEl = sheet.querySelector('#lp-sub'),
      rowsEl = sheet.querySelector('#lp-rows'),
      helpEl = sheet.querySelector('#lp-help'),
      formEl = sheet.querySelector('#lp-form'),
      codeEl = sheet.querySelector('#lp-code'),
      errEl = sheet.querySelector('#lp-err'),
      connectBtn = sheet.querySelector('#lp-connect'),
      forgetBtn = sheet.querySelector('#lp-forget');

sheet.querySelector('#lp-x').onclick = App.back;

codeEl.addEventListener('input', () => {
  codeEl.value = codeEl.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
  errEl.textContent = '';
});
formEl.onsubmit = async e => {
  e.preventDefault();
  if (codeEl.hidden || connectBtn.disabled) return;
  if (codeEl.value.trim().length < 4) { errEl.textContent = 'Enter the code.'; return; }
  connectBtn.disabled = true; connectBtn.textContent = 'Connecting…'; errEl.textContent = '';
  const r = await App.pair(codeEl.value);
  if (r.ok) {
    connectBtn.textContent = 'Connected ✓';
    setTimeout(() => location.reload(), 350);
    return;
  }
  errEl.textContent = r.err;
  connectBtn.disabled = false; connectBtn.textContent = 'Connect';
};
forgetBtn.onclick = () => {
  App.store.removeItem('wfk');
  App.store.removeItem('wfname');
  location.replace(location.pathname);
};

function row(label, value) {
  const d = document.createElement('div');
  d.className = 'lp-row';
  const l = document.createElement('span');
  l.className = 'lp-k'; l.textContent = label;
  const v = document.createElement('span');
  v.className = 'lp-v'; v.textContent = value;
  d.append(l, v);
  return d;
}

function render() {
  const s = App.state;
  const ok = App.connected();
  const name = (s.laptop && s.laptop.name) || App.store.getItem('wfname') || '';
  stateEl.textContent = ok ? 'Connected' : 'Not connected';   // read aloud; the dot carries it on screen
  icoEl.classList.toggle('on', ok);
  nameEl.textContent = ok ? (name || 'Your laptop') : '';
  subEl.textContent = '';
  rowsEl.textContent = '';
  if (ok && s.confirms.length)
    rowsEl.appendChild(row('Waiting for a yes', String(s.confirms.length)));
  helpEl.hidden = true;
  codeEl.hidden = ok;
  connectBtn.hidden = ok;
  forgetBtn.hidden = !ok;
}

App.on('statusbar', render);
App.on('route', n => { if (n === 'laptop') render(); });
App.route('laptop', {
  el: sheet,
  open() {
    render();
    if (!App.connected()) setTimeout(() => { try { codeEl.focus({ preventScroll: true }); } catch (e) {} }, 280);
  },
});
