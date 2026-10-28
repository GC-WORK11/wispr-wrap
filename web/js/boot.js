// boot.js — in-app loading screen controller (BOOT stage owns this file).
// Classic script (not a module) loaded right after the #boot div so the splash
// is live before the module app parses. Drives a hairline progress bar off
// THREE real readiness signals, never a fake percentage:
//   1) dom      — DOM parsed (DOMContentLoaded / interactive)
//   2) app      — the module app executed (window 'load' = all <script type=module> ran)
//   3) health   — the daemon answered GET /health
// When all three land (or 2.5s elapses, whichever first) it fades out in 240ms,
// revealing the lotus bloom (flower.js replays on load; we never touch it).
// prefers-reduced-motion: instant swap. A pure-CSS fallback in boot.css hides
// the splash after 3s even if this script throws.
(function () {
  'use strict';
  var el = document.getElementById('boot');
  if (!el) return;

  var reduce = false;
  try { reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  // weighted steps so the bar advances in honest, legible increments
  var STEPS = { dom: 0.34, app: 0.33, health: 0.33 };
  var done = { dom: false, app: false, health: false };
  var finished = false;
  var started = (window.performance && performance.now) ? performance.now() : Date.now();
  var MAX_MS = 2500; // never block longer than this
  var MIN_MS = 1000; // ...nor hide the lotus mark before it finished blooming

  function bar() {
    var i = el.querySelector('.boot-bar > i');
    return i;
  }

  function render() {
    var p = 0.06; // a visible sliver from the first paint
    for (var k in STEPS) if (done[k]) p += STEPS[k];
    if (p > 1) p = 1;
    var i = bar();
    if (i) i.style.setProperty('--boot-p', p.toFixed(3));
  }

  function allDone() { return done.dom && done.app && done.health; }

  function mark(step) {
    if (finished) return;
    if (step && Object.prototype.hasOwnProperty.call(done, step)) done[step] = true;
    render();
    if (allDone()) finish();
  }

  function finish() {
    if (finished) return;
    finished = true;
    render();
    // let the full bar paint for a beat (and the mark finish blooming: MIN_MS since navigation start), then fade into the ASCII lotus
    var now = (window.performance && performance.now) ? performance.now() : Date.now();
    var hold = reduce ? 0 : Math.max(60, MIN_MS - (now - started));
    setTimeout(function () {
      try { window.dispatchEvent(new Event('wl:boot-done')); } catch (e) {}   // app.js replays the ASCII lotus from a bud right now
      el.classList.add('boot-done');
      if (reduce) {
        el.classList.add('boot-gone');
      } else {
        var killed = false;
        var kill = function () { if (killed) return; killed = true; el.classList.add('boot-gone'); };
        el.addEventListener('transitionend', kill, { once: true });
        setTimeout(kill, 320); // safety: display:none even if transitionend is missed
      }
    }, hold);
  }

  // expose a tiny API so the app (or tests) can push real signals in
  window.__boot = { mark: mark, get done() { return finished; }, _state: done };

  // 1) DOM parsed
  if (document.readyState === 'interactive' || document.readyState === 'complete') mark('dom');
  else document.addEventListener('DOMContentLoaded', function () { mark('dom'); }, { once: true });

  // 2) app module executed — window 'load' fires only after every
  //    <script type="module"> has run (and subresources settled)
  if (document.readyState === 'complete') mark('app');
  else window.addEventListener('load', function () { mark('app'); }, { once: true });

  // 3) first /health answered (real daemon reachability)
  try {
    fetch('/health', { cache: 'no-store' })
      .then(function () { mark('health'); })
      .catch(function () { mark('health'); }); // offline is still "we tried" — don't hang the splash
  } catch (e) { mark('health'); }

  // hard ceiling: never block the UI longer than MAX_MS
  setTimeout(function () { finish(); }, MAX_MS);

  render();
})();
