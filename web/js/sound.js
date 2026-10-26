// sound.js — a very small, tasteful sound design. No toggle: five short synthesised
// tones, all quiet, none looping, nothing on load (browsers block that anyway). The phone's own
// volume/silent switch is the mute. Tones are plain sine/triangle with a soft attack and a long
// exponential release so they feel like a touch, not a notification.
import { App } from './app.js';

let ctx = null, master = null;
function ensure() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC(); master = ctx.createGain(); master.gain.value = 0.9; master.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume().catch(() => {});
  return ctx;
}
// browsers only allow audio after a gesture: unlock on the first touch/key
['pointerdown', 'keydown'].forEach(ev => addEventListener(ev, ensure, { once: true, passive: true, capture: true }));

function tone({ f, f2, dur = .14, gain = .04, type = 'sine', at = 0 }) {
  const c = ensure(); if (!c || c.state !== 'running') return;
  const t = c.currentTime + at, o = c.createOscillator(), g = c.createGain();
  o.type = type; o.frequency.setValueAtTime(f, t);
  if (f2) o.frequency.exponentialRampToValueAtTime(f2, t + dur);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(gain, t + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.03);
}

export const Sound = {
  send()  { tone({ f: 523.25, f2: 659.25, dur: .12, gain: .04 }); },                                  // lands on the laptop
  live()  { tone({ f: 392, f2: 523.25, dur: .16, gain: .035 }); tone({ f: 659.25, dur: .22, gain: .022, at: .07 }); },   // Go Live opens
  fired() { tone({ f: 659.25, dur: .24, gain: .04 }); tone({ f: 987.77, dur: .32, gain: .02, at: .05 }); },            // a sign ran
  word()  { tone({ f: 783.99, dur: .07, gain: .022 }); },                                              // Text mode: a word landed
  error() { tone({ f: 220, f2: 174.61, dur: .18, gain: .035, type: 'triangle' }); },                   // something did not go through
};

App.on('sent', r => { if (r && r.ok) Sound.send(); else Sound.error(); });
App.on('route', name => { if (name === 'live') Sound.live(); });
App.on('text:append', () => Sound.word());

// Go Live shows what a sign did in #lv-act; play the chime when that caption appears
// (soft "didn't catch that" / failures get the muted tone instead). Observed, so live.js stays untouched.
const watch = () => {
  const act = document.getElementById('lv-act');
  if (!act) return false;
  let was = act.classList.contains('show');
  new MutationObserver(() => {
    const now = act.classList.contains('show');
    if (now && !was) {
      const txt = (document.getElementById('lv-act-t') || {}).textContent || '';
      if (/catch|couldn|didn|failed|no action/i.test(txt)) Sound.error(); else Sound.fired();
    }
    was = now;
  }).observe(act, { attributes: true, attributeFilter: ['class'] });
  return true;
};
if (!watch()) addEventListener('load', watch, { once: true });
