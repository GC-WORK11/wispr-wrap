// handlines.js — live hand-line canvas renderer for the Go Live view.
// Pure ES module, no deps.
//
// Brand: white lines over the camera feed, ONE lotus-pink accent (#FF309C /
// shade #F12F92). Light, premium, calm — no neon, no heavy glow, no blue.
//
// Coordinates: MediaPipe Hands, 21 landmarks {x,y} normalized 0..1 in DISPLAY
// space (already mirrored), relative to the VIDEO frame. We map them onto the
// canvas like CSS `object-fit: cover` of the video element.

const PINK = '#F12F92';      // brand pink (shade)
let INK = '#ffffff';           // line colour: white over camera, ink over the light replay stage
const PINK_HI = '#FF309C';   // lotus pink

const BONES = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [17, 18], [18, 19], [19, 20],
  [0, 17],
];
const TIPS = [4, 8, 12, 16, 20];
const INDEX_TIP = 8;

const STIFF = 380;                 // spring stiffness (1/s^2)
const DAMP = 2 * Math.sqrt(STIFF); // critical damping
const FADE_MS = 250;               // hand fade-out on null
const TRAIL_MS = 450;              // index fingertip trail window

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
const lerp = (a, b, t) => a + (b - a) * t;
// smootherstep-ish easing for crisp but gentle transitions
const ease = t => t * t * (3 - 2 * t);

export function createHandLines(canvas) {
  const ctx = canvas.getContext('2d');

  let video = null;

  // ---- hand springs (21 landmarks) --------------------------------------
  // position (px display space), velocity, and the live target.
  const px = new Float64Array(21);
  const py = new Float64Array(21);
  const vx = new Float64Array(21);
  const vy = new Float64Array(21);
  const tx = new Float64Array(21); // target px
  const ty = new Float64Array(21);
  let haveHand = false;    // springs are seeded with a real target
  let handAlpha = 0;       // 0..1 visible amount
  let handPresent = false; // last setHand was non-null
  let snapNext = false;    // snap springs on (re)appearance

  // index fingertip trail: ring buffer of {x,y,t}
  const TRAIL_CAP = 64;
  const trX = new Float64Array(TRAIL_CAP);
  const trY = new Float64Array(TRAIL_CAP);
  const trT = new Float64Array(TRAIL_CAP);
  let trHead = 0, trLen = 0;

  // ---- ink (live air-writing stroke) ------------------------------------
  let ink = null; // array of {x,y} in display space

  // ---- ignite animation -------------------------------------------------
  let ignition = null; // { letter, t0, src[64], tpl[64], bbox, resolve }

  // ---- browse cursor ----------------------------------------------------
  let cursor = null; // { x, y, dwell }

  // ---- click ripples ----------------------------------------------------
  const ripples = []; // { x, y, t0 }

  // ---- loop / sizing ----------------------------------------------------
  let raf = 0;
  let running = false;
  let lastT = 0;
  let cssW = 0, cssH = 0, dpr = 1;

  // reusable scratch for mapping (avoid per-frame alloc)
  const mapOut = { x: 0, y: 0 };

  function ensureSize() {
    const d = Math.min(2, window.devicePixelRatio || 1);
    const w = canvas.clientWidth || canvas.width || 1;
    const h = canvas.clientHeight || canvas.height || 1;
    if (w !== cssW || h !== cssH || d !== dpr) {
      cssW = w; cssH = h; dpr = d;
      canvas.width = Math.max(1, Math.round(w * d));
      canvas.height = Math.max(1, Math.round(h * d));
    }
  }

  // object-fit: cover mapping of normalized (0..1, display space) -> css px
  function mapNorm(nx, ny, out) {
    const vw = video && video.videoWidth || 0;
    const vh = video && video.videoHeight || 0;
    if (!vw || !vh || !cssW || !cssH) {
      out.x = nx * cssW; out.y = ny * cssH; return out;
    }
    const scale = Math.max(cssW / vw, cssH / vh);
    const dw = vw * scale, dh = vh * scale;
    const ox = (cssW - dw) / 2, oy = (cssH - dh) / 2;
    out.x = ox + nx * dw;
    out.y = oy + ny * dh;
    return out;
  }

  // ---------------------------------------------------------------- public
  function setVideo(v) { video = v || null; }

  function setHand(lm, tMs) {
    if (!lm || lm.length < 21) {
      handPresent = false;
      haveHand = false; // springs keep last pos; alpha fades out
      return;
    }
    if (!handPresent) snapNext = true; // (re)appearance
    handPresent = true;
    haveHand = true;
    for (let i = 0; i < 21; i++) {
      mapNorm(lm[i].x, lm[i].y, mapOut);
      tx[i] = mapOut.x; ty[i] = mapOut.y;
    }
    // record index fingertip target into the trail (by time)
    pushTrail(tx[INDEX_TIP], ty[INDEX_TIP], tMs == null ? now() : tMs);
    ensureRunning();
  }

  function pushTrail(x, y, t) {
    trX[trHead] = x; trY[trHead] = y; trT[trHead] = t;
    trHead = (trHead + 1) % TRAIL_CAP;
    if (trLen < TRAIL_CAP) trLen++;
  }

  function setInk(points) {
    ink = (points && points.length) ? points : null;
    if (ink) ensureRunning();
  }

  function setCursor(x, y, dwell = 0) {
    if (x == null || y == null) { cursor = null; return; }
    if (!cursor) cursor = { x, y, dwell };
    else { cursor.x = x; cursor.y = y; cursor.dwell = dwell; }
    ensureRunning();
  }

  function pulse(x, y) {
    ripples.push({ x, y, t0: now() });
    ensureRunning();
  }

  function ignite(letter, points) {
    const pts = (points && points.length > 1) ? points : (ink || []);
    const bbox = bboxOf(pts);
    const src = resample(pts, 64);
    let tpl = template(letter, bbox, 64);
    // morph along the direction the stroke was drawn (a reversed pairing loops)
    if (src && src.length === 64 && tpl && tpl.length === 64) {
      const d = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
      if (d(src[0], tpl[63]) + d(src[63], tpl[0]) < d(src[0], tpl[0]) + d(src[63], tpl[63])) tpl = tpl.slice().reverse();
    }
    // src must be a valid 64-array even if pts was tiny
    const srcSafe = (src && src.length === 64) ? src : template(letter, bbox, 64);
    ensureRunning();
    return new Promise(resolve => {
      ignition = { letter: String(letter || '').toUpperCase(), t0: now(), src: srcSafe, tpl, bbox, resolve };
    });
  }

  function start() {
    if (running) return;
    running = true;
    lastT = now();
    raf = requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    ensureSize();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  function ensureRunning() { if (running && !raf) raf = requestAnimationFrame(frame); }

  // --------------------------------------------------------------- helpers
  function now() {
    return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
  }

  function bboxOf(pts) {
    if (!pts || !pts.length) return { x: cssW * 0.4, y: cssH * 0.4, w: cssW * 0.2, h: cssH * 0.2 };
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const p of pts) {
      if (p.x < minx) minx = p.x; if (p.x > maxx) maxx = p.x;
      if (p.y < miny) miny = p.y; if (p.y > maxy) maxy = p.y;
    }
    let w = maxx - minx, h = maxy - miny;
    if (w < 1) { w = 1; } if (h < 1) { h = 1; }
    return { x: minx, y: miny, w, h };
  }

  // arc-length resample a polyline to n evenly spaced points -> [{x,y}...]
  function resample(pts, n) {
    if (!pts || pts.length === 0) return null;
    if (pts.length === 1) {
      const out = new Array(n);
      for (let i = 0; i < n; i++) out[i] = { x: pts[0].x, y: pts[0].y };
      return out;
    }
    // cumulative lengths
    let total = 0;
    const seg = new Array(pts.length - 1);
    for (let i = 0; i < pts.length - 1; i++) {
      const d = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
      seg[i] = d; total += d;
    }
    if (total === 0) {
      const out = new Array(n);
      for (let i = 0; i < n; i++) out[i] = { x: pts[0].x, y: pts[0].y };
      return out;
    }
    const step = total / (n - 1);
    const out = new Array(n);
    out[0] = { x: pts[0].x, y: pts[0].y };
    let si = 0, acc = 0;
    for (let i = 1; i < n - 1; i++) {
      const target = i * step;
      while (si < seg.length - 1 && acc + seg[si] < target) { acc += seg[si]; si++; }
      const t = seg[si] > 0 ? (target - acc) / seg[si] : 0;
      out[i] = { x: lerp(pts[si].x, pts[si + 1].x, t), y: lerp(pts[si].y, pts[si + 1].y, t) };
    }
    out[n - 1] = { x: pts[pts.length - 1].x, y: pts[pts.length - 1].y };
    return out;
  }

  // clean glyph template fitted to bbox -> [{x,y}...] length n
  function template(letter, bb, n) {
    const L = String(letter || '').toUpperCase();
    const out = new Array(n);
    if (L === 'V') {
      // two straight strokes forming a V, apex at bottom-centre
      const x0 = bb.x, x1 = bb.x + bb.w;
      const yTop = bb.y, yBot = bb.y + bb.h;
      const apexX = bb.x + bb.w / 2;
      const half = Math.floor(n / 2);
      for (let i = 0; i < n; i++) {
        if (i < half) {
          const t = i / (half - 1 || 1);
          out[i] = { x: lerp(x0, apexX, t), y: lerp(yTop, yBot, t) };
        } else {
          const t = (i - half) / (n - half - 1 || 1);
          out[i] = { x: lerp(apexX, x1, t), y: lerp(yBot, yTop, t) };
        }
      }
      return out;
    }
    // 'C' default: an open arc of ~300deg whose gap faces right (+x), like the
    // letter. Start at +30deg (upper right), sweep counter-clockwise through the
    // top (90), the left side (180) and the bottom (270) to 330deg (lower right).
    const cx = bb.x + bb.w / 2, cy = bb.y + bb.h / 2;
    const rx = bb.w / 2, ry = bb.h / 2;
    const a0 = (30 * Math.PI) / 180;
    const sweep = (300 * Math.PI) / 180;
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1);
      const a = a0 + sweep * t;
      out[i] = { x: cx + rx * Math.cos(a), y: cy - ry * Math.sin(a) };
    }
    return out;
  }

  // quadratic-midpoint smoothed polyline stroke
  function strokeSmooth(pts) {
    if (!pts || pts.length < 2) return;
    ctx.moveTo(pts[0].x, pts[0].y);
    if (pts.length === 2) { ctx.lineTo(pts[1].x, pts[1].y); return; }
    for (let i = 1; i < pts.length - 1; i++) {
      const mx = (pts[i].x + pts[i + 1].x) / 2;
      const my = (pts[i].y + pts[i + 1].y) / 2;
      ctx.quadraticCurveTo(pts[i].x, pts[i].y, mx, my);
    }
    const last = pts[pts.length - 1];
    ctx.lineTo(last.x, last.y);
  }

  // --------------------------------------------------------------- drawing
  function frame() {
    raf = 0;
    if (!running) return;
    const t = now();
    let dt = (t - lastT) / 1000;
    lastT = t;
    if (dt > 0.05) dt = 0.05; // clamp <= 50ms
    if (dt < 0) dt = 0;

    ensureSize();

    // ---- advance hand springs
    if (handPresent) {
      if (snapNext) {
        for (let i = 0; i < 21; i++) { px[i] = tx[i]; py[i] = ty[i]; vx[i] = 0; vy[i] = 0; }
        snapNext = false;
      } else if (haveHand) {
        for (let i = 0; i < 21; i++) {
          // critically damped spring: a = -k*(x - target) - c*v
          const ax = -STIFF * (px[i] - tx[i]) - DAMP * vx[i];
          const ay = -STIFF * (py[i] - ty[i]) - DAMP * vy[i];
          vx[i] += ax * dt; vy[i] += ay * dt;
          px[i] += vx[i] * dt; py[i] += vy[i] * dt;
        }
      }
      // fade in over ~180ms
      handAlpha = Math.min(1, handAlpha + (dt * 1000) / 180);
    } else {
      // fade out over FADE_MS (~250ms)
      handAlpha = Math.max(0, handAlpha - (dt * 1000) / FADE_MS);
    }

    // figure out whether there's anything to draw
    const igniteActive = ignition != null;
    const rippleActive = ripples.length > 0;
    const anything =
      handAlpha > 0.001 || ink || igniteActive || cursor || rippleActive;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); // work in CSS px
    ctx.clearRect(0, 0, cssW, cssH);

    if (handAlpha > 0.001 && haveHand) drawHand(handAlpha, t);
    if (ink && !igniteActive) drawInk(ink);
    if (igniteActive) drawIgnite(t);
    if (cursor) drawCursor(cursor);
    if (rippleActive) drawRipples(t);

    // keep looping only while something is visible/animating
    if (running) {
      if (anything || handPresent) raf = requestAnimationFrame(frame);
      else running && (raf = requestAnimationFrame(idleCheck));
    }
  }

  // light idle tick: if still nothing, we stop scheduling until an input wakes us
  function idleCheck() {
    raf = 0;
    if (!running) return;
    const anything = handPresent || handAlpha > 0.001 || ink || ignition || cursor || ripples.length;
    if (anything) { lastT = now(); raf = requestAnimationFrame(frame); }
    // else: stay parked; setHand/setInk/pulse/ignite call ensureRunning()
  }

  function drawHand(alpha, t) {
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // bones — 1.25px white, alpha 0.82
    ctx.strokeStyle = INK;
    ctx.globalAlpha = 0.82 * alpha;
    ctx.lineWidth = 1.25;
    ctx.beginPath();
    for (let b = 0; b < BONES.length; b++) {
      const a = BONES[b][0], c = BONES[b][1];
      ctx.moveTo(px[a], py[a]);
      ctx.lineTo(px[c], py[c]);
    }
    ctx.stroke();

    // index fingertip motion trail (last ~450ms), tapering 1.5px
    drawIndexTrail(alpha, t);

    // joints — 2.2px dots alpha 0.9
    ctx.fillStyle = INK;
    ctx.globalAlpha = 0.9 * alpha;
    for (let i = 0; i < 21; i++) {
      const r = TIPS.indexOf(i) >= 0 ? 3 : 2.2; // fingertips 3px
      ctx.beginPath();
      ctx.arc(px[i], py[i], r / 2, 0, Math.PI * 2);
      ctx.fill();
    }

    // index fingertip soft ring (radius 7px, alpha 0.35)
    ctx.globalAlpha = 0.35 * alpha;
    ctx.strokeStyle = INK;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(px[INDEX_TIP], py[INDEX_TIP], 7, 0, Math.PI * 2);
    ctx.stroke();

    ctx.globalAlpha = 1;
  }

  function drawIndexTrail(alpha, t) {
    if (trLen < 2) return;
    ctx.lineCap = 'round';
    ctx.strokeStyle = INK;
    // oldest -> newest; alpha and width taper up toward newest
    // iterate ring buffer in chronological order
    let prevX = NaN, prevY = NaN;
    for (let k = 0; k < trLen; k++) {
      const idx = (trHead - trLen + k + TRAIL_CAP * 2) % TRAIL_CAP;
      const age = t - trT[idx];
      if (age > TRAIL_MS || age < 0) { prevX = NaN; continue; }
      const f = 1 - age / TRAIL_MS; // 1 newest .. 0 oldest
      if (!isNaN(prevX)) {
        ctx.globalAlpha = 0.5 * f * alpha;
        ctx.lineWidth = Math.max(0.25, 1.5 * f);
        ctx.beginPath();
        ctx.moveTo(prevX, prevY);
        ctx.lineTo(trX[idx], trY[idx]);
        ctx.stroke();
      }
      prevX = trX[idx]; prevY = trY[idx];
    }
    ctx.globalAlpha = 1;
  }

  function drawInk(pts) {
    if (!pts || pts.length < 2) return;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 2;
    ctx.strokeStyle = INK;
    const n = pts.length;
    const freshFrom = Math.floor(n * 0.75); // newest ~25%
    // older portion: fade toward 0.35
    for (let i = 1; i < n; i++) {
      const frac = i / (n - 1);
      let a;
      if (i >= freshFrom) a = 1; // newest 25% brightest
      else a = lerp(0.35, 1, frac / 0.75); // older fading to 0.35
      ctx.globalAlpha = clamp(a, 0.35, 1);
      // draw as a short smoothed segment pair for continuity
      const p0 = pts[i - 1], p1 = pts[i];
      ctx.beginPath();
      ctx.moveTo(p0.x, p0.y);
      const mx = (p0.x + p1.x) / 2, my = (p0.y + p1.y) / 2;
      ctx.quadraticCurveTo(p0.x, p0.y, mx, my);
      ctx.lineTo(p1.x, p1.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function drawIgnite(t) {
    const g = ignition;
    const ms = t - g.t0;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    const src = g.src;     // 64
    const tpl = g.tpl;     // 64
    const N = src.length;

    if (ms < 250) {
      // phase 1: pink highlight travels start->end; line behind turns pink
      const p = ease(clamp(ms / 250, 0, 1));
      const edge = Math.floor(p * (N - 1));
      ctx.lineWidth = 2;
      // white ahead
      ctx.globalAlpha = 1;
      ctx.strokeStyle = INK;
      ctx.beginPath();
      strokeSmooth(src);
      ctx.stroke();
      // pink behind, up to edge
      ctx.strokeStyle = PINK_HI;
      ctx.beginPath();
      ctx.moveTo(src[0].x, src[0].y);
      for (let i = 1; i <= edge; i++) ctx.lineTo(src[i].x, src[i].y);
      ctx.stroke();
      // bright travelling head
      if (edge >= 0 && edge < N) {
        ctx.fillStyle = PINK_HI;
        ctx.globalAlpha = 1;
        ctx.beginPath();
        ctx.arc(src[edge].x, src[edge].y, 3, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      return;
    }

    if (ms < 650) {
      // phase 2: morph src -> tpl point-wise
      const p = ease(clamp((ms - 250) / 400, 0, 1));
      ctx.lineWidth = 2;
      ctx.strokeStyle = PINK_HI;
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.moveTo(lerp(src[0].x, tpl[0].x, p), lerp(src[0].y, tpl[0].y, p));
      for (let i = 1; i < N; i++) {
        ctx.lineTo(lerp(src[i].x, tpl[i].x, p), lerp(src[i].y, tpl[i].y, p));
      }
      ctx.stroke();
      ctx.globalAlpha = 1;
      return;
    }

    if (ms < 1000) {
      // phase 3: text glyph fades in (scale 0.92->1), morphed path fades out
      const p = ease(clamp((ms - 650) / 350, 0, 1));
      // fading morphed template path
      ctx.lineWidth = 2;
      ctx.strokeStyle = PINK_HI;
      ctx.globalAlpha = (1 - p) * 1;
      ctx.beginPath();
      ctx.moveTo(tpl[0].x, tpl[0].y);
      for (let i = 1; i < N; i++) ctx.lineTo(tpl[i].x, tpl[i].y);
      ctx.stroke();

      // text glyph
      const cx = g.bbox.x + g.bbox.w / 2;
      const cy = g.bbox.y + g.bbox.h / 2;
      const size = Math.max(12, 0.9 * g.bbox.h);
      const scale = lerp(0.92, 1, p);
      ctx.save();
      ctx.translate(cx, cy);
      ctx.scale(scale, scale);
      ctx.globalAlpha = p;
      ctx.fillStyle = INK;
      ctx.font = `600 ${size}px Inter, system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.shadowColor = 'rgba(0,0,0,.35)';
      ctx.shadowBlur = 4;
      ctx.fillText(g.letter, 0, 0);
      ctx.restore();
      ctx.globalAlpha = 1;
      ctx.shadowBlur = 0;
      return;
    }

    // done — final brief fade then resolve
    if (ms >= 1000) {
      const g2 = ignition;
      ignition = null;
      if (g2 && g2.resolve) g2.resolve();
    }
    ctx.globalAlpha = 1;
  }

  function drawCursor(c) {
    ctx.lineCap = 'round';
    // 14px white ring (1.5px line)
    ctx.globalAlpha = 1;
    ctx.strokeStyle = INK;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(c.x, c.y, 7, 0, Math.PI * 2);
    ctx.stroke();
    // 3px centre dot
    ctx.fillStyle = INK;
    ctx.beginPath();
    ctx.arc(c.x, c.y, 1.5, 0, Math.PI * 2);
    ctx.fill();
    // dwell progress arc (deep pink)
    if (c.dwell > 0) {
      ctx.strokeStyle = PINK;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(c.x, c.y, 10, -Math.PI / 2, -Math.PI / 2 + clamp(c.dwell, 0, 1) * Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function drawRipples(t) {
    ctx.lineWidth = 1.5;
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i];
      const ms = t - r.t0;
      if (ms >= 380) { ripples.splice(i, 1); continue; }
      const p = ms / 380;
      const rad = lerp(14, 34, ease(p));
      ctx.globalAlpha = (1 - p);
      ctx.strokeStyle = INK;
      ctx.beginPath();
      ctx.arc(r.x, r.y, rad, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  return {
    setVideo,
    setHand,
    setInk,
    setInkColor(c) { INK = c || '#ffffff'; },
    ignite,
    setCursor,
    pulse,
    start,
    stop,
  };
}

export default createHandLines;
