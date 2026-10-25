// compose.js — the composer: text/image/video/file to the laptop cursor.
// Ported from the original single-file composer; transports unchanged:
//   POST /send        {text, mode:'type'}        -> {ok, into}
//   POST /send-image  blob (sniffed)             -> {ok, into, mime, kb}
//   POST /send-file   blob + X-File-Name         -> {ok, into, path, mb}
// Demo-review fix folded in: the input is a reviewable textarea that grows
// with content (capped, then scrolls) — not one cramped line.
import { App } from './app.js';

const $ = id => document.getElementById(id);
const box = $('box'), go = $('go'), plus = $('plus'), file = $('file'),
      fileany = $('fileany'), attach = $('attach'), thumb = $('thumb'),
      vthumb = $('vthumb'), aname = $('aname'), unattach = $('unattach');
if (!box) throw new Error('compose: #box missing');

let staged = null;

const fmtSize = b => b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.round(b / 1024) + ' KB';

/* ---------- growing, reviewable input ---------- */
function grow() {
  box.style.height = 'auto';
  box.style.height = Math.min(box.scrollHeight, 132) + 'px';
  box.style.overflowY = box.scrollHeight > 132 ? 'auto' : 'hidden';
}
box.addEventListener('input', grow);
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
  App.buzz();
}
function clearStaged() {
  if (staged) URL.revokeObjectURL(staged.url);
  staged = null; attach.hidden = true;
  thumb.removeAttribute('src'); vthumb.removeAttribute('src'); vthumb.load && vthumb.load();
}
unattach.onclick = () => { clearStaged(); box.focus(); };

/* + opens a small picker: photo/video vs any file (two real inputs exist) */
let menu = null;
function attachMenu() {
  if (menu) { menu.remove(); menu = null; return; }
  menu = document.createElement('div');
  menu.className = 'attach-menu';
  const mk = (label, fn) => {
    const b = document.createElement('button');
    b.className = 'attach-item'; b.textContent = label; b.onclick = fn;
    menu.appendChild(b);
  };
  mk('photo or video', () => { menu.remove(); menu = null; file.click(); });
  mk('any file', () => { menu.remove(); menu = null; fileany.click(); });
  App.el.pill.appendChild(menu);
  setTimeout(() => document.addEventListener('pointerdown', function h(e) {
    if (menu && !menu.contains(e.target) && e.target !== plus) {
      menu.remove(); menu = null; document.removeEventListener('pointerdown', h);
    }
  }), 0);
}
plus.onclick = attachMenu;
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
      App.toast(st.isVideo ? 'sending video…' : (st.blob.type || '').startsWith('image/') ? 'sending image…' : 'sending file…');
      try {
        let r;
        if (st.isVideo || !(st.blob.type || '').startsWith('image/')) {
          r = await App.api.post('/send-file', st.blob,
            { 'Content-Type': st.blob.type || 'application/octet-stream',
              'X-File-Name': encodeURIComponent(st.name) });
        } else {
          const b = await squeeze(st.blob);
          r = await App.api.post('/send-image', b,
            { 'Content-Type': b.type || 'application/octet-stream' });
        }
        imgOk = r.ok; into = r.json && r.json.into;
        if (imgOk) clearStaged();
        if (r.ok) await App.sleep(500);
      } catch (e) { App.toast('laptop unreachable — run: wrap start', true); }
    }
    if (text.trim()) {
      try {
        const r = await App.api.post('/send', { text, mode: 'type' });
        txtOk = r.ok;
        if (r.ok) { box.value = ''; grow(); into = into || (r.json && r.json.into); }
      } catch (e) {}
    }
    if (imgOk || txtOk) {
      const kind = [imgOk && 'file', txtOk && 'text'].filter(Boolean).join(' + ');
      App.state.lastSend = { t_ms: Date.now(), ok: true, into, kind };
      App.renderStatus();
      App.toast('→ ' + kind + ' into ' + (into || 'laptop'));
      App.buzz();
    } else {
      App.toast('send failed — run: wrap start', true);
    }
  } finally {
    sending = false; go.disabled = false;
  }
}
go.onclick = send;
box.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

/* ---------- dictation bridge (Wispr Flow on Android) ---------- */
function armDictation() {
  if (!App.wispr.has()) return;
  box.focus();
  App.wispr.focusField();
}
addEventListener('load', () => {
  grow();
  if (!App.key) return;              // unpaired page doesn't steal focus
  box.focus();
  setTimeout(armDictation, 900);
});

/* re-focus when the keyboard dismissed itself but the user is composing */
App.on('route', name => { if (!name) setTimeout(() => box.focus(), 350); });
