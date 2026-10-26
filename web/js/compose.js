// compose.js — the composer: text/image/video/file to the laptop cursor.
// Transports (unchanged):
//   POST /send        {text, mode:'type'}        -> {ok, into}
//   POST /send-image  blob (sniffed)             -> {ok, into, mime, kb}
//   POST /send-file   blob + X-File-Name         -> {ok, into, path, mb}
// New in the peak pass: a feed that shows what landed (and what your signs
// ran), a greeting that gives way to it, drag-and-drop, suggestion pills.
import { App } from './app.js';

const $ = id => document.getElementById(id);
const box = $('box'), go = $('go'), plus = $('plus'), file = $('file'),
      fileany = $('fileany'), attach = $('attach'), thumb = $('thumb'),
      vthumb = $('vthumb'), aname = $('aname'), unattach = $('unattach'),
      feed = $('feed'), stage = $('stage'), pill = $('pill'), suggest = $('suggest');
if (!box) throw new Error('compose: #box missing');
if (matchMedia('(max-width: 720px)').matches) box.placeholder = 'Type or dictate…';

let staged = null;

const fmtSize = b => b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';

const ICON = {
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.01"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  hand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 11.5V6a1.5 1.5 0 0 1 3 0v5m0-1.5a1.5 1.5 0 0 1 3 0V11m0-.5a1.5 1.5 0 0 1 3 0v1m0-.5a1.5 1.5 0 0 1 3 0v4.5c0 3-2.5 5.5-6 5.5s-5-1.5-6.5-4l-2-3.6c-.6-1 .8-2.1 1.7-1.3l1.3 1.1"/></svg>',
  photo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><circle cx="9" cy="10.5" r="1.6"/><path d="m21 16-5-4.5L8 19"/></svg>',
};

/* ---------- growing, reviewable input ---------- */
function updateReady() {
  pill.classList.toggle('ready', !!box.value.trim() || !!staged);
}
function grow() {
  box.style.height = 'auto';
  box.style.height = Math.min(box.scrollHeight, 148) + 'px';
  box.style.overflowY = box.scrollHeight > 148 ? 'auto' : 'hidden';
  updateReady();
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
  updateReady();
  App.buzz();
}
function clearStaged() {
  if (staged) URL.revokeObjectURL(staged.url);
  staged = null; attach.hidden = true;
  thumb.removeAttribute('src'); vthumb.removeAttribute('src'); vthumb.load && vthumb.load();
  updateReady();
}
unattach.onclick = () => { clearStaged(); box.focus(); };

/* + opens a small picker: photo/video vs any file (two real inputs exist) */
let menu = null;
function closeMenu() { if (menu) { menu.remove(); menu = null; } plus.setAttribute('aria-expanded', 'false'); }
function attachMenu() {
  if (menu) { closeMenu(); return; }
  menu = document.createElement('div');
  menu.className = 'attach-menu';
  menu.setAttribute('role', 'menu');
  const mk = (icon, label, fn) => {
    const b = document.createElement('button');
    b.className = 'attach-item'; b.setAttribute('role', 'menuitem');
    b.innerHTML = icon + '<span></span>';
    b.lastChild.textContent = label;
    b.onclick = () => { closeMenu(); fn(); };
    menu.appendChild(b);
  };
  mk(ICON.photo, 'Photo or video', () => file.click());
  mk(ICON.file, 'Any file', () => fileany.click());
  pill.appendChild(menu);
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

/* suggestion pills under the greeting */
suggest.addEventListener('click', e => {
  const b = e.target.closest('.sugg');
  if (!b) return;
  const g = b.dataset.go;
  if (g === 'photo') file.click(); else App.nav(g);
});

/* copy an image on the phone, paste in the composer -> it stages */
document.addEventListener('paste', e => {
  const items = (e.clipboardData || {}).items || [];
  for (const it of items) {
    if (it.type && it.type.startsWith('image/')) {
      stageFile(it.getAsFile(), 'pasted image'); e.preventDefault(); return;
    }
  }
});

/* drop a file anywhere on the home screen */
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

/* ---------- the feed ---------- */
const MAX_FEED = 24;
const feedUrls = [];
function dock() {
  if (stage.classList.contains('has-feed')) return;
  const before = pill.getBoundingClientRect();
  stage.classList.add('has-feed');
  const after = pill.getBoundingClientRect();
  const dy = before.top - after.top;
  if (Math.abs(dy) > 1 && pill.animate) {
    pill.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }],
                 { duration: 560, easing: 'cubic-bezier(.2,.8,.2,1)' });
  }
}
function scrollFeed() { requestAnimationFrame(() => feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' })); }

/* build a message; returns {el, ok(metaText), fail(metaText)} */
function addMsg(kind, opts) {
  dock();
  const el = document.createElement('div');
  el.className = 'msg pending' + (kind === 'sys' ? ' sys' : '');
  let body;
  if (opts.media) {
    body = document.createElement('div'); body.className = 'msg-media';
    const m = document.createElement(opts.isVideo ? 'video' : 'img');
    if (opts.isVideo) { m.muted = true; m.playsInline = true; m.preload = 'metadata'; m.controls = true; }
    m.src = opts.media; m.alt = '';
    body.appendChild(m);
  } else if (opts.fileName) {
    body = document.createElement('div'); body.className = 'msg-file';
    body.innerHTML = '<span class="fi">' + ICON.file + '</span><span><b></b><small></small></span>';
    body.querySelector('b').textContent = opts.fileName;
    body.querySelector('small').textContent = opts.fileSize || '';
  } else {
    body = document.createElement('div'); body.className = 'msg-b';
    if (opts.icon) body.innerHTML = opts.icon + '<span></span>';
    (opts.icon ? body.lastChild : body).textContent = opts.text || '';
  }
  const meta = document.createElement('div'); meta.className = 'msg-m';
  meta.innerHTML = '<i class="spin"></i><span></span>';
  meta.lastChild.textContent = opts.pending || 'Sending…';
  el.append(body, meta);
  feed.appendChild(el);
  while (feed.children.length > MAX_FEED) {
    const old = feed.firstElementChild;
    const u = old && old.dataset.url; if (u) URL.revokeObjectURL(u);
    old.remove();
  }
  scrollFeed();
  const set = (cls, icon, text) => {
    el.classList.remove('pending'); el.classList.add(cls);
    meta.innerHTML = icon + '<span></span>';
    meta.lastChild.textContent = text;
  };
  return {
    el,
    ok: t => set('ok', ICON.check, t),
    fail: t => set('err', ICON.warn, t),
  };
}
const secs = ms => ms < 1000 ? Math.max(1, Math.round(ms)) + ' ms' : (ms / 1000).toFixed(1) + ' s';
/* the daemon reports window classes in whatever case X gave it ("sinkedit");
   prefer the focused-window name the status poll already knows */
function app(into) {
  if (!into) return 'the focused app';
  const f = App.state.focus;
  if (f && f.toLowerCase() === String(into).toLowerCase()) return f;
  return String(into).charAt(0).toUpperCase() + String(into).slice(1);
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
      const url = URL.createObjectURL(st.blob);
      const m = addMsg('out', isImg || st.isVideo
        ? { media: url, isVideo: st.isVideo, pending: st.isVideo ? 'Sending video…' : 'Sending image…' }
        : { fileName: st.name, fileSize: fmtSize(st.blob.size), pending: 'Sending file…' });
      m.el.dataset.url = url;
      const t0 = performance.now();
      try {
        let r;
        if (st.isVideo || !isImg) {
          r = await App.api.post('/send-file', st.blob,
            { 'Content-Type': st.blob.type || 'application/octet-stream',
              'X-File-Name': encodeURIComponent(st.name) });
        } else {
          const b = await squeeze(st.blob);
          r = await App.api.post('/send-image', b,
            { 'Content-Type': b.type || 'application/octet-stream' });
        }
        imgOk = r.ok; into = r.json && r.json.into;
        if (imgOk) {
          clearStaged();
          m.ok((isImg ? 'Pasted into ' : 'Saved on your laptop · path pasted into ') + app(into) + ' · ' + secs(performance.now() - t0));
        } else {
          m.fail('Laptop said no (' + r.status + ')' + (r.json && r.json.error ? ' — ' + r.json.error : ''));
        }
        if (r.ok) await App.sleep(300);
      } catch (e) {
        m.fail('Couldn’t reach your laptop — is wrap running?');
        App.toast('laptop unreachable — run: wrap start', true);
      }
    }
    if (text.trim()) {
      const m = addMsg('out', { text: text.trim(), pending: 'Typing on your laptop…' });
      const t0 = performance.now();
      try {
        const r = await App.api.post('/send', { text, mode: 'type' });
        txtOk = r.ok;
        if (r.ok) {
          box.value = ''; grow(); into = into || (r.json && r.json.into);
          m.ok('Typed into ' + app(r.json && r.json.into) + ' · ' + secs(performance.now() - t0));
        } else {
          m.fail(r.status === 409 || (r.json && r.json.error)
            ? ((r.json && r.json.error) || 'Laptop focus changed — not typed')
            : 'Laptop said no (' + r.status + ')');
        }
      } catch (e) {
        m.fail('Couldn’t reach your laptop — is wrap running?');
      }
    }
    if (imgOk || txtOk) {
      const kind = [imgOk && 'file', txtOk && 'text'].filter(Boolean).join(' + ');
      App.state.lastSend = { t_ms: Date.now(), ok: true, into, kind };
      App.renderStatus();
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
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});

/* ---------- signs that ran an action show up in the same feed ---------- */
let seenMatch;
App.on('livestate', j => {
  const m = j && j.last_match;
  if (!m) return;
  const id = (m.at_ms || m.at || '') + '|' + (m.gloss || '') + '|' + (m.accepted ? 'A' : 'R');
  if (seenMatch === undefined) { seenMatch = id; return; }   // baseline: ignore history
  if (id === seenMatch) return;
  seenMatch = id;
  const a = m.action || {};
  if (!m.accepted || m.mode || !(a.fired || a.pending)) return;
  const label = m.label || m.gloss;
  const x = addMsg('sys', { text: label, icon: ICON.hand, pending: 'Running…' });
  x.ok(a.pending ? 'Waiting for your OK on the laptop' : (a.label ? 'Did: ' + a.label : 'Done on your laptop'));
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
  /* a phone keyboard would cover the greeting — only auto-focus where there
     is a hardware keyboard (or the Android shell that drives Wispr) */
  const touch = matchMedia('(pointer: coarse)').matches;
  if (!touch || App.wispr.has()) box.focus();
  setTimeout(armDictation, 900);
});

/* re-focus when the keyboard dismissed itself but the user is composing */
App.on('route', name => {
  if (!name && !matchMedia('(pointer: coarse)').matches) setTimeout(() => box.focus(), 350);
});
