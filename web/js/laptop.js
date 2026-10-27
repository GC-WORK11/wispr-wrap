// laptop.js — the "Connected laptop" page (#/laptop).
// One honest status surface: is the laptop there, what is it called,
// what is it doing — and the way back in (a code) when it isn't.
import { App } from './app.js';

const sheet = App.el.sheetLaptop;
if (!sheet) throw new Error('laptop: #sheet-laptop missing');

sheet.innerHTML = `
  <div class="sheet-head">
    <h1>Laptop</h1>
    <button class="sheet-x" id="lp-x" aria-label="Close">×</button>
  </div>
  <div class="sheet-body lp-body">
    <p class="lp-state" id="lp-state">Checking…</p>
    <p class="hint lp-sub" id="lp-sub"></p>
    <div class="lp-rows" id="lp-rows"></div>
    <p class="hint lp-help" id="lp-help" hidden>
      <code>wrap pair</code>
    </p>
    <div class="lp-foot">
      <button class="btn btn-primary" id="lp-connect" hidden>Enter a code</button>
      <button class="btn btn-ghost" id="lp-forget" hidden>Forget this laptop</button>
    </div>
  </div>`;

const stateEl = sheet.querySelector('#lp-state'),
      subEl = sheet.querySelector('#lp-sub'),
      rowsEl = sheet.querySelector('#lp-rows'),
      helpEl = sheet.querySelector('#lp-help'),
      connectBtn = sheet.querySelector('#lp-connect'),
      forgetBtn = sheet.querySelector('#lp-forget');

sheet.querySelector('#lp-x').onclick = App.back;
connectBtn.onclick = () => {
  const onb = document.getElementById('onb');
  if (onb) { onb.hidden = false; App.back(); }
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
  stateEl.textContent = ok ? ('Connected' + (name ? ' to ' + name : '')) : 'Not connected';
  stateEl.classList.toggle('on', ok);
  subEl.textContent = ok
    ? (s.focus ? 'Typing into ' + s.focus + '.' : 'Ready — what you send lands at the cursor.')
    : 'This phone is not linked to a laptop.';
  rowsEl.textContent = '';
  if (ok) {
    if (typeof s.bankSize === 'number' && s.bankSize > 0)
      rowsEl.appendChild(row('Signs it knows', String(s.bankSize)));
    if (s.confirms.length)
      rowsEl.appendChild(row('Waiting for a yes', String(s.confirms.length)));
  }
  helpEl.hidden = ok;
  connectBtn.hidden = ok;
  forgetBtn.hidden = !ok;
}

App.on('statusbar', render);
App.on('route', n => { if (n === 'laptop') render(); });
App.route('laptop', { el: sheet, open: render });
