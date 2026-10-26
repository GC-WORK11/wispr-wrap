// try.mjs — the landing page's in-browser Go Live + Teach, running the same
// pipeline the phone runs: MediaPipe kp42 -> endpointer -> DTW sign bank, and
// hand.js air gestures (point/ink letters, palm, pinch, cursor, V sign).
// Nothing leaves the browser: video stays on the page, keypoints stay in JS.

import { Pose } from '/js/pose.mjs';
import { Skel } from '/js/skel.mjs';
import { Endpointer } from '/js/endpointer.mjs';
import { SignBank } from '/js/signbank.mjs';
import { createHand, classify } from '/js/hand.js';

const root = document.querySelector('[data-try]');
if (root) boot();

function boot() {
  const video = root.querySelector('[data-try-video]');
  const stage = root.querySelector('[data-try-stage]');
  const skelC = root.querySelector('[data-try-skel]');
  const inkC = root.querySelector('[data-try-ink]');
  const words = root.querySelector('[data-try-words]');
  const status = root.querySelector('[data-try-status]');
  const startBtn = root.querySelector('[data-try-start]');
  const teachBtn = root.querySelector('[data-try-teach]');
  const modeEl = root.querySelector('[data-try-mode]');
  const teachBox = root.querySelector('[data-try-teachbox]');
  const teachName = root.querySelector('[data-try-name]');
  const teachGo = root.querySelector('[data-try-record]');
  const takeDots = [...root.querySelectorAll('[data-try-take]')];
  const cursor = root.querySelector('[data-try-cursor]');
  const note = root.querySelector('[data-try-note]');

  const S = {
    on: false, stream: null, engine: null, front: true,
    ep: new Endpointer(), bank: new SignBank(), H: createHand({ mode: 'write' }),
    mode: 'live',            // live | teach
    browse: false, pose: 'none', slot: -1,
    teaching: false, takes: [], pending: null,
    raf: 0, ink: [], inkUntil: 0, cur: null, dwell: 0,
  };
  const ZERO42 = Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score: 0 }));
  const GESTURE = new Set(['point', 'pinch', 'v']);
  const OWN = new Set(['point', 'v', 'pinch']);

  const say = (t, ms = 2600) => {
    status.textContent = t;
    clearTimeout(say._t);
    if (ms) say._t = setTimeout(() => { status.textContent = ''; }, ms);
  };
  const fail = (t) => { note.textContent = t; note.hidden = !t; stage.classList.toggle('is-off', !!t); };

  function word(text) {
    const w = document.createElement('span');
    w.className = 'tw';
    w.textContent = text;
    words.append(w, ' ');
    while (words.children.length > 30) words.removeChild(words.firstChild);
    for (const el of words.querySelectorAll('.tw.new')) el.classList.remove('new');
    w.classList.add('new');
    words.scrollLeft = words.scrollWidth;
  }

  /* ---------- camera + engine ---------- */
  async function start() {
    if (S.on) return;
    startBtn.disabled = true;
    say('Starting camera', 0);
    try {
      const cam = await Pose.openCamera(video, true);
      S.stream = cam.stream; S.front = cam.front;
    } catch (e) {
      startBtn.disabled = false;
      say('');
      fail(e && (e.name === 'NotAllowedError' || e.name === 'SecurityError')
        ? 'Camera is off. Allow it to try signing.'
        : 'No camera on this device.');
      return;
    }
    video.classList.toggle('mir', S.front);
    try {
      S.engine = await Pose.load((p) => say(p.phase === 'model' ? 'Loading the hand model' : 'Loading', 0));
    } catch (e) {
      say('');
      fail('The hand model did not load.');
      S.stream.getTracks().forEach(t => t.stop());
      return;
    }
    say('');
    S.on = true;
    stage.classList.add('is-live');
    startBtn.hidden = true;
    teachBtn.hidden = false;
    modeEl.hidden = false;
    loop();
  }

  /* ---------- the per-frame loop ---------- */
  function loop() {
    if (!S.on) return;
    S.raf = requestAnimationFrame(loop);
    if (!S.engine || video.readyState < 2) return;
    const t = performance.now();
    let res;
    try { res = S.engine.detectFrame(video, t); } catch (e) { return; }
    if (!res || !res.points) return;
    const pts = res.points;
    Skel.draw(skelC, pts, { video, mirror: S.front, accent: 'rgba(255,48,156,.95)', dim: 'rgba(10,10,10,.5)' });

    // gestures (write/browse) — same pick-hand the app uses
    const lm = pickHand(pts);
    if (!S.teaching) {
      const evs = S.H.update(lm, t);
      for (const ev of evs) onGesture(ev);
    }

    // sign matching — a hand busy gesturing is never also a sign
    const owns = !S.teaching && (OWN.has(S.pose) || (S.browse && S.pose !== 'none' && S.pose !== 'palm'));
    const feed = { t_ms: Math.round(t), points: owns ? ZERO42 : pts };
    for (const ev of S.ep.feed(feed)) onSegment(ev);
  }

  const present = (pts, o) => pts[o] && pts[o + 9] && pts[o].score > .28 && pts[o + 9].score > .28;
  function handAt(pts, o) {
    const m = S.front, lm = new Array(21);
    for (let i = 0; i < 21; i++) { const p = pts[o + i]; lm[i] = { x: m ? 1 - p.x : p.x, y: p.y }; }
    return lm;
  }
  function pickHand(pts) {
    const a = present(pts, 0), b = present(pts, 21);
    if (!a && !b) { S.slot = -1; return null; }
    if (a !== b) { S.slot = a ? 0 : 21; return handAt(pts, S.slot); }
    const L = handAt(pts, 0), R = handAt(pts, 21);
    const gl = GESTURE.has(classify(L).pose), gr = GESTURE.has(classify(R).pose);
    if (gl !== gr) S.slot = gl ? 0 : 21;
    else if (S.slot !== 0 && S.slot !== 21) S.slot = 0;
    return S.slot === 0 ? L : R;
  }

  /* ---------- ink + cursor drawing ---------- */
  const inkX = inkC.getContext('2d');
  function fitInk() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = inkC.clientWidth, H = inkC.clientHeight;
    if (inkC.width !== Math.round(W * dpr)) { inkC.width = Math.round(W * dpr); inkC.height = Math.round(H * dpr); }
    inkX.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function toPx(p) {
    const W = stage.clientWidth, H = stage.clientHeight;
    const vw = video.videoWidth || 0, vh = video.videoHeight || 0;
    if (!vw || !vh) return { x: p.x * W, y: p.y * H };
    const k = Math.max(W / vw, H / vh), dw = vw * k, dh = vh * k;
    return { x: (W - dw) / 2 + p.x * dw, y: (H - dh) / 2 + p.y * dh };
  }
  function drawInk() {
    fitInk();
    const W = inkC.clientWidth, H = inkC.clientHeight;
    inkX.clearRect(0, 0, W, H);
    if (!S.ink.length || performance.now() > S.inkUntil) return;
    inkX.strokeStyle = '#FF309C';
    inkX.lineWidth = 5; inkX.lineCap = 'round'; inkX.lineJoin = 'round';
    inkX.beginPath();
    S.ink.forEach((p, i) => i ? inkX.lineTo(p.x, p.y) : inkX.moveTo(p.x, p.y));
    inkX.stroke();
  }

  /* ---------- gesture events ---------- */
  function onGesture(ev) {
    switch (ev.type) {
      case 'pose': S.pose = ev.pose; break;
      case 'ink': S.ink = ev.points.map(toPx); S.inkUntil = performance.now() + 900; drawInk(); break;
      case 'letter':
        S.ink = []; drawInk();
        word(ev.letter);
        break;
      case 'stroke-cancel': S.ink = []; drawInk(); break;
      case 'reject': S.ink = []; drawInk(); say('Again', 1400); break;
      case 'vsign': word('V'); break;
      case 'pinch': if (!S.browse) setBrowse(true); break;
      case 'palm': if (S.browse) setBrowse(false); else if (!S.teaching) setBrowse(true); break;
      case 'cursor': {
        S.cur = { x: ev.x, y: ev.y };
        const c = toPx(S.cur);
        cursor.hidden = false;
        cursor.style.transform = `translate(${c.x}px, ${c.y}px)`;
        cursor.style.setProperty('--p', S.dwell || 0);
        break;
      }
      case 'dwell':
        S.dwell = ev.p;
        if (S.cur) cursor.style.setProperty('--p', ev.p);
        break;
      case 'scroll':
        window.scrollBy({ top: ev.dy * 56, behavior: 'auto' });
        break;
      case 'click': {
        S.dwell = 0;
        const c = toPx({ x: ev.x, y: ev.y });
        cursor.style.transform = `translate(${c.x}px, ${c.y}px)`;
        cursor.classList.remove('pulse'); void cursor.offsetWidth; cursor.classList.add('pulse');
        break;
      }
    }
  }
  function setBrowse(on) {
    S.browse = !!on;
    S.H.setMode(S.browse ? 'browse' : 'write');
    stage.classList.toggle('browse', S.browse);
    if (!S.browse) { cursor.hidden = true; S.cur = null; S.dwell = 0; }
    modeEl.textContent = S.browse ? 'point' : 'draw';
  }

  /* ---------- segments -> teach takes / sign matches ---------- */
  function onSegment(ev) {
    if (ev.type === 'mode') return;         // palm/namaste holds: laptop functions
    if (ev.type === 'segment-drop') { if (S.teaching) say('Again', 1400); return; }
    if (ev.type !== 'segment') return;
    if (S.teaching) {
      S.takes.push(ev.frames);
      const n = S.takes.length;
      takeDots.forEach((d, i) => d.classList.toggle('got', i < n));
      if (n >= 3) finishTeach();
      else say(`Take ${n} of 3`, 1600);
      return;
    }
    if (!S.bank.templates.length) return;
    const m = S.bank.match(ev.frames);
    if (m.accepted) word(m.label || m.gloss.toLowerCase());
    else say('Again', 1400);
  }

  /* ---------- teach flow ---------- */
  function enterTeach() {
    S.mode = 'teach';
    teachBox.hidden = false;
    teachBtn.hidden = true;
    teachName.value = '';
    teachName.focus();
    setBrowse(false);
  }
  function recordTeach() {
    const name = teachName.value.trim();
    if (!name) { teachName.focus(); return; }
    S.teaching = true;
    S.takes = [];
    takeDots.forEach(d => d.classList.remove('got'));
    teachGo.hidden = true;
    teachName.disabled = true;
    say('Take 1 of 3', 0);
    S.ep.reset();
  }
  function finishTeach() {
    const gloss = 'TYPE-' + teachName.value.trim().toUpperCase().replace(/\s+/g, '-');
    const label = teachName.value.trim();
    let ok = 0;
    for (const fr of S.takes) if (S.bank.addExemplar(gloss, fr, label)) ok++;
    S.teaching = false;
    S.mode = 'live';
    teachBox.hidden = true;
    teachName.disabled = false;
    teachGo.hidden = false;
    teachBtn.hidden = false;
    S.ep.reset();
    say(ok ? 'Saved — sign it' : 'Not saved', 2400);
  }

  startBtn.addEventListener('click', start);
  teachBtn.addEventListener('click', enterTeach);
  teachGo.addEventListener('click', recordTeach);
  teachName.addEventListener('keydown', (e) => { if (e.key === 'Enter') recordTeach(); });
}
