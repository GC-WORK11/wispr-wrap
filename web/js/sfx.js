// sfx.js — tiny toggle-less sound cues (BOOT stage owns this file).
// Four synthesised WebAudio cues, no sample files, no UI, no setting. The
// AudioContext is created LAZILY on the first real user gesture (never
// autoplay), kept very quiet (peak gain <= 0.06), each cue <= 160ms. Never
// plays while the tab is hidden, and at most one cue every 120ms.
//
// Cues (wired to the app's REAL events):
//   sent   soft two-note rising tick   App 'sent'
//   sign   one soft chime              App 'livestate' when last_match.accepted lands a NEW at_ms
//   ready  gentle major third          window CustomEvent 'wf:sign-ready' (teach.js dispatches)
//   error  short low thud              App.toast(..., isErr) — we wrap App.toast
//
// Export: window.WFSfx = { play(name) } for tests.
import { App } from './app.js';

let ctx = null;
let lastPlay = 0;

// cue recipes: minimal additive blips. g = peak gain (<= 0.06), ms <= 160.
const CUES = {
  sent:  { notes: [[660, 0, 0.07], [880, 0.06, 0.09]], g: 0.05, type: 'sine' },
  sign:  { notes: [[784, 0, 0.14]], g: 0.045, type: 'sine' },
  ready: { notes: [[523.25, 0, 0.12], [659.25, 0.02, 0.14]], g: 0.05, type: 'sine' },
  error: { notes: [[150, 0, 0.13]], g: 0.06, type: 'triangle' },
};

function ensureCtx() {
  if (ctx) return ctx;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
  } catch (e) { ctx = null; }
  return ctx;
}

// create the context on the first user gesture only (browsers require it, and
// nothing sounds before the person touches anything)
let armed = false;
function arm() {
  if (armed) return;
  armed = true;
  const c = ensureCtx();
  if (c && c.state === 'suspended') { try { c.resume(); } catch (e) {} }
}
for (const ev of ['pointerdown', 'keydown', 'touchstart']) {
  window.addEventListener(ev, arm, { once: false, passive: true });
}

function play(name) {
  const cue = CUES[name];
  if (!cue) return;
  if (!armed) return;                      // nothing before a user gesture
  if (document.hidden) return;             // never while the tab is hidden
  const now = (performance && performance.now) ? performance.now() : Date.now();
  if (now - lastPlay < 120) return;        // at most one cue per 120ms
  const c = ensureCtx();
  if (!c) return;
  if (c.state === 'suspended') { try { c.resume(); } catch (e) {} }
  lastPlay = now;
  const t0 = c.currentTime + 0.001;
  for (const [freq, at, dur] of cue.notes) {
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = cue.type;
    osc.frequency.value = freq;
    const start = t0 + at;
    const end = start + dur;
    // quick attack, smooth exponential release — no clicks
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(cue.g, start + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, end);
    osc.connect(gain).connect(c.destination);
    osc.start(start);
    osc.stop(end + 0.02);
  }
}

window.WFSfx = { play };

// ---- wire the real events -------------------------------------------------

// sent: compose.js fires App.emit('sent', {ok, into}) after a successful send
App.on('sent', (d) => { if (!d || d.ok !== false) play('sent'); });

// sign: /live/state carries last_match; a newly accepted match has a fresh
// at_ms. Chime once per new acceptance, never on repeats or rejections.
let lastSignAt = 0;
App.on('livestate', (j) => {
  if (!j) return;
  const m = j.last_match;
  if (!m || !m.accepted) return;
  const at = m.at_ms != null ? m.at_ms : (m.t_ms != null ? m.t_ms : 0);
  if (at && at !== lastSignAt) { lastSignAt = at; play('sign'); }
});

// ready: teach.js dispatches a window CustomEvent when a sign is saved
window.addEventListener('wf:sign-ready', () => play('ready'));

// error: wrap App.toast so an error toast gives a short low thud. We keep the
// original behaviour intact and only add the cue.
if (App && typeof App.toast === 'function') {
  const origToast = App.toast.bind(App);
  App.toast = function (msg, isErr) {
    if (isErr) play('error');
    return origToast(msg, isErr);
  };
}
