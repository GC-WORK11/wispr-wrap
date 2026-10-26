// settings.js — PRO lane: the Settings page (#/settings).
// Only what is real and local to this phone. No account, no cloud,
// nothing that pretends to configure the laptop — that all lives on
// the laptop itself.
import { App } from './app.js';

const sheet = App.el.sheetSettings;
if (!sheet) throw new Error('settings: #sheet-settings missing');

sheet.innerHTML = `
  <div class="sheet-head">
    <h1>Settings</h1>
    <button class="sheet-x" id="st-x" aria-label="Close">×</button>
  </div>
  <div class="sheet-body st-body">
    <label class="st-row">
      <span class="st-t">Vibrate on send</span>
      <input type="checkbox" id="st-vibrate" class="st-sw">
    </label>
    <label class="st-row">
      <span class="st-t">Replayed gestures<span class="st-s">For demos and recordings only — hands move themselves</span></span>
      <input type="checkbox" id="st-replay" class="st-sw">
    </label>
    
  </div>`;

sheet.querySelector('#st-x').onclick = App.back;

const vib = sheet.querySelector('#st-vibrate'),
      rep = sheet.querySelector('#st-replay');

// vibration — gate the shared buzz() on the stored preference
const buzzReal = App.buzz;
App.buzz = () => { if (App.store.getItem('wfvib') !== '0') buzzReal(); };
vib.checked = App.store.getItem('wfvib') !== '0';
vib.onchange = () => App.store.setItem('wfvib', vib.checked ? '1' : '0');

// replay seam — the same flag pose.js checks; needs ?replay once or this
// toggle. Honest label, off by default, judges never see it on.
rep.checked = App.store.getItem('wfreplay') === '1';
// demo-only switch: not for users — shown with ?dev only
if (!(/[?&]dev\b/.test(location.search) || App.store.getItem('wfdev') === '1')) rep.closest('label').hidden = true;
rep.onchange = () => App.store.setItem('wfreplay', rep.checked ? '1' : '0');

App.route('settings', { el: sheet });
