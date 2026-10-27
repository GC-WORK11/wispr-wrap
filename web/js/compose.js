// compose.js — the composer: text/image/video/file to the laptop cursor.
// Transports (unchanged from the original wispr-wrap):
//   POST /send        {text, mode:'type'}        -> {ok, into}
//   POST /send-image  blob (sniffed)             -> {ok, into, mime, kb}
//   POST /send-file   blob + X-File-Name         -> {ok, into, path, mb}
// Layout per the target design: round + , white pill, black waveform (Go Live)
// that turns into the send arrow when there is something to send. No mic.
// The lotus (flower.js) reacts: it charges while you type and bursts on send.
import { App } from './app.js';
import { Flower } from './flower.js';

const $ = id => document.getElementById(id);
const box = $('box'), go = $('go'), plus = $('plus'), file = $('file'),
      fileany = $('fileany'), attach = $('attach'), thumb = $('thumb'),
      vthumb = $('vthumb'), aname = $('aname'), unattach = $('unattach'),
      stage = $('stage'), pill = $('pill'), composer = $('composer');
if (!box) throw new Error('compose: #box missing');

let staged = null;
const fmtSize = b => b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';

const ICON = {
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  photo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><circle cx="9" cy="10.5" r="1.6"/><path d="m21 16-5-4.5L8 19"/></svg>',
};

/* ---------- growing, reviewable input ---------- */
function updateReady() { pill.classList.toggle('ready', !!box.value.trim() || !!staged); }
function grow() {
  box.style.height = 'auto';
  box.style.height = Math.min(box.scrollHeight, 150) + 'px';
  box.style.overflowY = box.scrollHeight > 150 ? 'auto' : 'hidden';
  updateReady();
  Flower.measure();
}
box.addEventListener('input', () => { grow(); Flower.poke(.22); });
addEventListener('resize', grow);

/* ---------- attach ---------- */
function stageFile(blob, name) {
  clearStaged();
  staged = { blob, url: URL.createObjectURL(blob), name: name || 'file',
             isVideo: (blob.type || '').startsWith('video/') };
  if (staged.isVideo) {
    vthumb.src = staged.url; vthumb.style.display = 'block'; thumb.style.display = 'none';
  } else if ((blob.type || '').startsWith('image/')) {
    thumb.src = staged.url; thumb.style.display = 'block'; vthumb.style.display = 'none';
  } else {
    thumb.style.display = 'none'; vthumb.style.display = 'none';
  }
  aname.textContent = staged.name + ' · ' + fmtSize(blob.size);
  attach.hidden = false;
  grow(); App.buzz(); Flower.poke(.5);
}
function clearStaged() {
  if (staged) URL.revokeObjectURL(staged.url);
  staged = null; attach.hidden = true;
  thumb.removeAttribute('src'); vthumb.removeAttribute('src'); vthumb.load && vthumb.load();
  grow();
}
unattach.onclick = () => { clearStaged(); box.focus(); };

/* + opens a small picker: photo/video vs any file (two real inputs exist) */
let menu = null;
function closeMenu() { if (menu) { menu.remove(); menu = null; } plus.setAttribute('aria-expanded', 'false'); }
function attachMenu() {
  if (menu) { closeMenu(); return; }
  menu = document.createElement('div');
  menu.className = 'attach-menu'; menu.setAttribute('role', 'menu');
  const mk = (icon, label, fn) => {
    const b = document.createElement('button');
    b.className = 'attach-item'; b.setAttribute('role', 'menuitem');
    b.innerHTML = icon + '<span></span>'; b.lastChild.textContent = label;
    b.onclick = () => { closeMenu(); fn(); };
    menu.appendChild(b);
  };
  mk(ICON.photo, 'Photo or video', () => file.click());
  mk(ICON.file, 'File', () => fileany.click());
  composer.appendChild(menu);
  plus.setAttribute('aria-expanded', 'true');
  setTimeout(() => document.addEventListener('pointerdown', function h(e) {
    if (menu && !menu.contains(e.target) && !plus.contains(e.target)) {
      closeMenu(); document.removeEventListener('pointerdown', h);
    } else if (!menu) document.removeEventListener('pointerdown', h);
  }), 0);
}
plus.onclick = attachMenu;
addEventListener('keydown', e => { if (e.key === 'Escape' && menu) closeMenu(); });
file.onchange = () => { const f = file.files[0]; if (f) stageFile(f, f.name); file.value = ''; };
fileany.onchange = () => { const f = fileany.files[0]; if (f) stageFile(f, f.name); fileany.value = ''; };

/* copy an image on the phone, paste in the composer -> it stages */
document.addEventListener('paste', e => {
  const items = (e.clipboardData || {}).items || [];
  for (const it of items) {
    if (it.type && it.type.startsWith('image/')) {
      stageFile(it.getAsFile(), 'pasted image'); e.preventDefault(); return;
    }
  }
});

/* drop a file anywhere on the home screen (laptop browsers) */
let dragDepth = 0;
const hasFiles = e => e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');
stage.addEventListener('dragenter', e => { if (hasFiles(e)) { dragDepth++; stage.classList.add('drop'); e.preventDefault(); } });
stage.addEventListener('dragover', e => { if (hasFiles(e)) e.preventDefault(); });
stage.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) stage.classList.remove('drop'); });
stage.addEventListener('drop', e => {
  if (!hasFiles(e)) return;
  e.preventDefault(); dragDepth = 0; stage.classList.remove('drop');
  const f = e.dataTransfer.files[0];
  if (f) stageFile(f, f.name);
});

/* ---------- @ target: pick an open laptop window; sends land there ---------- */
let target = null, winMenu = null;
const tchip = document.createElement('button');
tchip.className = 'tchip'; tchip.type = 'button'; tchip.hidden = true;
tchip.setAttribute('aria-label', 'Change target window');
pill.insertBefore(tchip, pill.querySelector('.row'));
function setTarget(w) {
  target = w;
  tchip.hidden = !w;
  if (w) tchip.innerHTML = '<span class="tchip-at">@</span><span class="tchip-n"></span><span class="tchip-x" aria-hidden="true">×</span>',
         tchip.querySelector('.tchip-n').textContent = w.title;
}
tchip.onclick = e => {
  if (e.target.classList.contains('tchip-x')) { setTarget(null); box.focus(); return; }
  openWinMenu('');
};
function closeWinMenu() { if (winMenu) { winMenu.remove(); winMenu = null; } }
async function openWinMenu(query) {
  closeMenu(); closeWinMenu();
  winMenu = document.createElement('div');
  winMenu.className = 'attach-menu win-menu'; winMenu.setAttribute('role', 'menu');
  composer.appendChild(winMenu);
  const m = winMenu;
  let wins = [];
  try {
    const r = await App.api.get('/windows');
    wins = (r.ok && r.json && r.json.windows) || [];
  } catch (e) { /* offline */ }
  if (m !== winMenu) return;
  const q = (query || '').toLowerCase();
  const rows = wins.filter(w => !q || (w.title + ' ' + w.app).toLowerCase().includes(q));
  if (!rows.length) {
    const n = document.createElement('div'); n.className = 'win-empty';
    n.textContent = wins.length ? 'No match' : 'No open windows';
    m.appendChild(n); return;
  }
  for (const w of rows.slice(0, 12)) {
    const b = document.createElement('button');
    b.className = 'attach-item win-item'; b.setAttribute('role', 'menuitem');
    b.innerHTML = '<span class="win-t"></span><span class="win-a"></span>';
    b.querySelector('.win-t').textContent = w.title;
    b.querySelector('.win-a').textContent = w.app;
    b.onclick = () => {
      setTarget(w); closeWinMenu();
      box.value = box.value.replace(/(^|\s)@[^\s@]*$/, '$1'); grow(); box.focus();
    };
    m.appendChild(b);
  }
}
box.addEventListener('input', () => {
  const m = box.value.match(/(^|\s)@([^\s@]*)$/);
  if (m) openWinMenu(m[2]); else closeWinMenu();
});
document.addEventListener('pointerdown', e => {
  if (winMenu && !winMenu.contains(e.target) && e.target !== box && !tchip.contains(e.target)) closeWinMenu();
});
addEventListener('keydown', e => { if (e.key === 'Escape' && winMenu) closeWinMenu(); });

/* ---------- image squeeze (same recipe as the original composer) ---------- */
const MAXD = 1280, JPEG_Q = 0.82, PASS_MAX = 900 * 1024;
async function squeeze(blob) {
  if (blob.type === 'image/gif' || blob.size <= PASS_MAX) return blob;
  try {
    const bmp = await createImageBitmap(blob);
    const s = Math.min(1, MAXD / Math.max(bmp.width, bmp.height));
    if (s >= 1 && blob.type !== 'image/png' && blob.size <= 1536 * 1024) {
      bmp.close && bmp.close(); return blob;
    }
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    const x = c.getContext('2d');
    if (blob.type === 'image/png') { x.fillStyle = '#fff'; x.fillRect(0, 0, c.width, c.height); }
    x.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close && bmp.close();
    return await new Promise(r => c.toBlob(b => r(b || blob), 'image/jpeg', JPEG_Q));
  } catch (e) { return blob; }
}

/* ---------- send ---------- */
let sending = false;
async function send() {
  if (sending) return;
  const text = box.value;
  if (!text.trim() && !staged) return;
  sending = true; go.disabled = true;
  let into = null, imgOk = false, txtOk = false;
  try {
    if (staged) {
      const st = staged;
      const isImg = (st.blob.type || '').startsWith('image/');
      App.toast(st.isVideo ? 'Sending video…' : isImg ? 'Sending image…' : 'Sending file…');
      try {
        let r;
        if (st.isVideo || !isImg) {
          r = await App.api.post('/send-file', st.blob,
            Object.assign({ 'Content-Type': st.blob.type || 'application/octet-stream',
              'X-File-Name': encodeURIComponent(st.name) },
              target ? { 'X-WF-Window': target.id } : {}));
        } else {
          const b = await squeeze(st.blob);
          r = await App.api.post('/send-image', b,
            Object.assign({ 'Content-Type': b.type || 'application/octet-stream' },
              target ? { 'X-WF-Window': target.id } : {}));
        }
        imgOk = r.ok; into = r.json && r.json.into;
        if (imgOk) { clearStaged(); await App.sleep(450); }
        else App.toast(App.daemonErr(r, 'Couldn’t send. Try again.'), true);
      } catch (e) { App.toast('Can’t reach your laptop.', true); }
    }
    if (text.trim()) {
      try {
        const r = await App.api.post('/send', target ? { text, mode: 'type', window: target.id } : { text, mode: 'type' });
        txtOk = r.ok;
        if (r.ok) { box.value = ''; grow(); into = into || (r.json && r.json.into); }
        else App.toast(App.daemonErr(r, 'Couldn’t send. Try again.'), true);
      } catch (e) { App.toast('Can’t reach your laptop.', true); }
    }
    App.emit('sent', { ok: imgOk || txtOk, into });
    if (imgOk || txtOk) {
      const kind = imgOk && txtOk ? 'Text and file' : imgOk ? 'File' : 'Text';
      App.state.lastSend = { t_ms: Date.now(), ok: true, into, kind };
      App.renderStatus();
      App.toast(kind + ' sent.');
      Flower.burst(); App.buzz();
    } else if (!imgOk && !txtOk && !document.querySelector('.toast.err.show')) {
      App.toast('Couldn’t send. Try again.', true);
    }
  } finally {
    sending = false; go.disabled = false; box.focus();
  }
}
go.onclick = send;
box.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    const first = winMenu && winMenu.querySelector('.win-item');
    if (first) { first.click(); return; }
    send();
  }
});

App.on('text:send', () => send());
/* Go Live's Text mode appends each recognised sign (a word) to the message */
App.on('text:append', w => {
  if (!w) return;
  box.value += (box.value && !/\s$/.test(box.value) ? ' ' : '') + w;
  grow(); Flower.poke(.3);
});

/* ---------- dictation bridge (Wispr Flow on Android) ---------- */
function armDictation() {
  if (!App.wispr.has()) return;
  box.focus();
  App.wispr.focusField();
}
addEventListener('load', () => {
  grow();
  if (!App.key) return;                // unpaired page doesn't steal focus
  /* a phone keyboard would cover the lotus: auto-focus only where there is a
     hardware keyboard, or in the Android shell that drives Wispr Flow */
  const touch = matchMedia('(pointer: coarse)').matches;
  if (!touch || App.wispr.has()) box.focus();
  setTimeout(armDictation, 1100);      // let the flower open first, then summon the keyboard
});

/* visual viewport: lift the composer above the soft keyboard (iOS does not
   resize the layout viewport) and let the lotus re-measure */
const vv = window.visualViewport;
function fitKb() {
  if (!vv) return;
  const kb = Math.max(0, innerHeight - (vv.height + vv.offsetTop));
  composer.style.transform = kb > 60 ? `translateY(${-kb}px)` : '';
  Flower.measure();
  clearTimeout(fitKb.t); fitKb.t = setTimeout(Flower.measure, 300);
}
if (vv) { vv.addEventListener('resize', fitKb); vv.addEventListener('scroll', fitKb); }
composer.addEventListener('transitionend', Flower.measure);

/* back on the home screen: re-focus where a keyboard is expected */
App.on('route', name => {
  if (!name && !matchMedia('(pointer: coarse)').matches) setTimeout(() => box.focus(), 350);
});
