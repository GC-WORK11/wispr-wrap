// hand.js — pure ES module one-hand gesture engine.
// No DOM, no imports, no globals. MediaPipe Hands 21-landmark input in
// DISPLAY space (normalized 0..1, already selfie-mirrored, y down).

// ---------------------------------------------------------------------------
// geometry helpers
// ---------------------------------------------------------------------------
function dist(a, b) {
  const dx = a.x - b.x, dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}

// ---------------------------------------------------------------------------
// classify — static pose from a single frame of landmarks
// ---------------------------------------------------------------------------
export function classify(lm) {
  if (!lm || lm.length < 21) {
    return { pose: 'none', ext: [false, false, false, false, false], size: 0, pinch: Infinity };
  }
  const wrist = lm[0];
  const size = dist(wrist, lm[9]) || 1e-6;

  // finger extension: dist(wrist,tip) > 1.18 * dist(wrist,pip)
  const pip = [6, 10, 14, 18];
  const tip = [8, 12, 16, 20];
  const ext = [false, false, false, false, false];
  // thumb: dist(5,4) > 1.25 * dist(5,3)
  ext[0] = dist(lm[5], lm[4]) > 1.25 * dist(lm[5], lm[3]);
  for (let i = 0; i < 4; i++) {
    ext[i + 1] = dist(wrist, lm[tip[i]]) > 1.18 * dist(wrist, lm[pip[i]]);
  }

  const pinch = dist(lm[4], lm[8]) / size;
  const [, idx, mid, ring, pky] = ext;

  let pose = 'other';
  if (pinch < 0.33 && (idx || !idx)) {
    // pinch takes precedence: pinch < 0.33 and index extended OR curled
    pose = 'pinch';
  } else if (idx && mid && !ring && !pky && dist(lm[8], lm[12]) > 0.55 * size) {
    pose = 'v';
  } else if (idx && !mid && !ring && !pky) {
    pose = 'point';
  } else if (idx && mid && ring && pky) {
    pose = 'palm';
  } else if (!idx && !mid && !ring && !pky) {
    pose = 'fist';
  }

  return { pose, ext, size, pinch };
}

// ---------------------------------------------------------------------------
// $1-style recognizer — orientation sensitive (no rotation normalization)
// ---------------------------------------------------------------------------
const N = 64;

function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += dist(pts[i - 1], pts[i]);
  return d;
}

function resample(pts, n) {
  const I = pathLength(pts) / (n - 1);
  let D = 0;
  const out = [{ x: pts[0].x, y: pts[0].y }];
  const src = pts.map(p => ({ x: p.x, y: p.y }));
  for (let i = 1; i < src.length; i++) {
    const d = dist(src[i - 1], src[i]);
    if (D + d >= I && d > 0) {
      const t = (I - D) / d;
      const q = {
        x: src[i - 1].x + t * (src[i].x - src[i - 1].x),
        y: src[i - 1].y + t * (src[i].y - src[i - 1].y),
      };
      out.push(q);
      src.splice(i, 0, q);
      D = 0;
    } else {
      D += d;
    }
  }
  while (out.length < n) out.push({ x: src[src.length - 1].x, y: src[src.length - 1].y });
  return out.slice(0, n);
}

function bbox(pts) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) {
    if (p.x < minX) minX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.x > maxX) maxX = p.x;
    if (p.y > maxY) maxY = p.y;
  }
  return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY };
}

// translate centroid to origin, scale to unit box preserving aspect
function normalize(pts) {
  let cx = 0, cy = 0;
  for (const p of pts) { cx += p.x; cy += p.y; }
  cx /= pts.length; cy /= pts.length;
  const b = bbox(pts);
  const s = Math.max(b.w, b.h) || 1e-6;
  return pts.map(p => ({ x: (p.x - cx) / s, y: (p.y - cy) / s }));
}

function meanDist(a, b) {
  let d = 0;
  for (let i = 0; i < a.length; i++) d += dist(a[i], b[i]);
  return d / a.length;
}

// ---- template builders (in raw ~unit coordinates, normalized at load) ----
function arcC(startDeg, endDeg, cx, cy, rx, ry) {
  // counterclockwise/clockwise handled by caller via deg direction
  const pts = [];
  for (let i = 0; i < N; i++) {
    const t = i / (N - 1);
    const a = (startDeg + (endDeg - startDeg) * t) * Math.PI / 180;
    pts.push({ x: cx + rx * Math.cos(a), y: cy - ry * Math.sin(a) });
  }
  return pts;
}

function vShape(topL, bottom, topR) {
  const pts = [];
  const half = Math.floor(N / 2);
  for (let i = 0; i < half; i++) {
    const t = i / (half - 1);
    pts.push({ x: topL.x + t * (bottom.x - topL.x), y: topL.y + t * (bottom.y - topL.y) });
  }
  for (let i = 0; i < N - half; i++) {
    const t = i / (N - half - 1);
    pts.push({ x: bottom.x + t * (topR.x - bottom.x), y: bottom.y + t * (topR.y - bottom.y) });
  }
  return pts;
}

// C: open arc, opening faces right. Top-right -> up -> left -> down -> bottom-right.
// In screen coords y is down. Angle 0 = +x (right), we go CCW in math => but y
// is flipped, so use y = cy - r*sin. Start near top-right (~ -60deg), sweep to
// bottom-right (~ -300deg) going counterclockwise (through the left side).
const RAW_TEMPLATES = {
  C: [
    arcC(-60, -300, 0, 0, 0.5, 0.6),   // standard open C
    arcC(-50, -310, 0, 0, 0.45, 0.6),  // tighter
    arcC(-70, -290, 0, 0, 0.55, 0.62), // wider
    arcC(-40, -300, 0, 0, 0.5, 0.6),   // started slightly lower
    arcC(-300, -60, 0, 0, 0.5, 0.6),   // drawn clockwise from bottom-right
  ],
  V: [
    vShape({ x: -0.5, y: -0.6 }, { x: 0, y: 0.6 }, { x: 0.5, y: -0.6 }),  // standard
    vShape({ x: -0.6, y: -0.6 }, { x: 0, y: 0.6 }, { x: 0.6, y: -0.6 }),  // wider
    vShape({ x: -0.35, y: -0.6 }, { x: 0, y: 0.6 }, { x: 0.35, y: -0.6 }),// narrower
  ],
};

const TEMPLATES = (() => {
  const out = {};
  for (const letter of Object.keys(RAW_TEMPLATES)) {
    out[letter] = RAW_TEMPLATES[letter].map(t => normalize(resample(t, N)));
  }
  return out;
})();

export function recognize(points) {
  if (!points || points.length < 2) return { letter: null, score: 0 };
  const len = pathLength(points);
  const b = bbox(points);
  const maxSide = Math.max(b.w, b.h);
  const diag = Math.sqrt(b.w * b.w + b.h * b.h) || 1e-6;

  const cand = normalize(resample(points, N));

  const half = 0.5 * Math.sqrt(2);
  let best = { letter: null, score: 0 };
  for (const letter of Object.keys(TEMPLATES)) {
    for (const tmpl of TEMPLATES[letter]) {
      const md = meanDist(cand, tmpl);
      const score = 1 - md / half;
      if (score > best.score) best = { letter, score };
    }
  }

  // closure test — a near-closed loop (O) must not be C
  const endToStart = dist(points[0], points[points.length - 1]);
  const closed = endToStart < 0.25 * diag;

  // gating
  if (best.score < 0.80 || len < 0.12 || maxSide < 0.08) {
    return { letter: null, score: best.score };
  }
  if (best.letter === 'C' && closed) {
    return { letter: null, score: best.score };
  }
  return best;
}

// ---------------------------------------------------------------------------
// createHand — stateful engine
// ---------------------------------------------------------------------------
export function createHand(opts = {}) {
  const engine = {
    mode: opts.mode === 'browse' ? 'browse' : 'write',
    setMode(m) { if (m === 'write' || m === 'browse') { this.mode = m; this.reset(); } },
    reset() { resetState(); },
    update,
  };

  // ---- internal state ----
  let lastPose = null;        // last raw classify pose (for stability)
  let stablePose = 'none';    // current stable pose
  let stableSince = 0;

  // write / ink
  let writing = false;
  let stroke = [];            // smoothed points of current stroke
  let emaTip = null;          // EMA(alpha 0.5) of index tip
  let pointAnchor = null;     // where pointing began
  let stillSince = 0;         // time tip has been ~still
  let stillRef = null;        // ref point for stillness
  let nullSince = -1;         // time hand first became null
  let lastHadHand = false;

  // v/palm hold flags
  let vHeldFired = false;
  let palmHeldFired = false;
  let palmRef = null;         // palm centre where the still hold began
  let palmSince = 0;

  // browse / cursor
  let emaCur = null;          // EMA(0.35) cursor
  let cursorHist = [];        // [{t,x,y}] recent for velocity
  let frameTravel = 0;        // accumulated vertical travel toward next notch
  let lastScrollAt = -1e9;
  let scrollingUntil = -1e9;

  // write mode: a pinch means "I want to point" (it never clicks there)
  let wPinchArmed = false;

  // click / dwell
  let pinchArmed = false;     // pinch was above 0.5
  let lastClickAt = -1e9;
  let dwellRef = null;
  let dwellSince = 0;
  let dwellActive = false;
  let dwellMovedSinceClick = true;

  function resetState() {
    lastPose = null; stablePose = 'none'; stableSince = 0;
    writing = false; stroke = []; emaTip = null; pointAnchor = null;
    stillSince = 0; stillRef = null; nullSince = -1; lastHadHand = false;
    vHeldFired = false; palmHeldFired = false;
    emaCur = null; cursorHist = []; frameTravel = 0;
    lastScrollAt = -1e9; scrollingUntil = -1e9;
    pinchArmed = false; lastClickAt = -1e9;
    dwellRef = null; dwellSince = 0; dwellActive = false; dwellMovedSinceClick = true;
    wPinchArmed = false; palmRef = null; palmSince = 0;
  }

  function endStroke(events) {
    writing = false;
    const pts = stroke.slice();
    events.push({ type: 'stroke-end', points: pts });
    if (pathLength(pts) < 0.08) {          // not a letter attempt: no "try again"
      events.push({ type: 'stroke-cancel', points: pts });
      stroke = []; pointAnchor = null; stillRef = null; stillSince = 0;
      return;
    }
    const r = recognize(pts);
    if (r.letter) {
      events.push({ type: 'letter', letter: r.letter, score: r.score, points: pts });
    } else {
      events.push({ type: 'reject', points: pts });
    }
    stroke = [];
    pointAnchor = null;
    stillRef = null;
    stillSince = 0;
  }

  function update(lm, tMs) {
    const events = [];
    const c = classify(lm);
    const hasHand = !!(lm && lm.length >= 21);

    // ---- null-hand handling for write stroke termination ----
    if (!hasHand) {
      if (lastHadHand) nullSince = tMs;
      lastHadHand = false;
    } else {
      nullSince = -1;
      lastHadHand = true;
    }

    // ---- stable pose tracking ----
    const rawPose = hasHand ? c.pose : 'none';
    if (rawPose === lastPose) {
      // same as previous raw -> this is stable
      if (stablePose !== rawPose) {
        stablePose = rawPose;
        stableSince = tMs;
        vHeldFired = false;
        palmHeldFired = false;
        events.push({ type: 'pose', pose: stablePose });
      }
    }
    lastPose = rawPose;

    // index tip EMA (alpha 0.5) for writing
    if (hasHand) {
      const tip = { x: lm[8].x, y: lm[8].y };
      emaTip = emaTip ? { x: 0.5 * tip.x + 0.5 * emaTip.x, y: 0.5 * tip.y + 0.5 * emaTip.y } : tip;
    }

    // =====================================================================
    // WRITE MODE
    // =====================================================================
    // V hold (any mode: after C opened Chrome, a V still opens YouTube)
    if (stablePose === 'v' && !vHeldFired && tMs - stableSince >= 600) {
      vHeldFired = true;
      events.push({ type: 'vsign' });
    }
    if (engine.mode === 'write') {
      // pinch: switch to pointing (the click gesture only clicks once pointing)
      if (hasHand) {
        if (c.pinch > 0.5 && (stablePose === 'point' || stablePose === 'other' || stablePose === 'palm')) wPinchArmed = true;
        if (wPinchArmed && stablePose === 'pinch') {
          wPinchArmed = false;
          events.push({ type: 'pinch' });
        }
      }

      // stroke lifecycle
      if (!writing) {
        if (stablePose === 'point' && hasHand && emaTip) {
          if (!pointAnchor) pointAnchor = { x: emaTip.x, y: emaTip.y };
          if (dist(emaTip, pointAnchor) > 0.02) {
            writing = true;
            stroke = [{ x: pointAnchor.x, y: pointAnchor.y }, { x: emaTip.x, y: emaTip.y }];
            stillRef = { x: emaTip.x, y: emaTip.y };
            stillSince = tMs;
            events.push({ type: 'ink', points: stroke.slice() });
          }
        } else {
          pointAnchor = null;
        }
      } else {
        // currently writing
        let end = false;
        if (!hasHand) {
          if (nullSince >= 0 && tMs - nullSince >= 150) end = true;
        } else if (stablePose !== 'point') {
          end = true;
        } else {
          // add point, check stillness
          stroke.push({ x: emaTip.x, y: emaTip.y });
          if (stillRef && dist(emaTip, stillRef) <= 0.012) {
            if (tMs - stillSince >= 380) end = true;
          } else {
            stillRef = { x: emaTip.x, y: emaTip.y };
            stillSince = tMs;
          }
          events.push({ type: 'ink', points: stroke.slice() });
        }
        if (end) endStroke(events);
      }
    }

    // =====================================================================
    // BROWSE MODE
    // =====================================================================
    if (engine.mode === 'browse') {
      const isCursorPose = stablePose === 'point' || stablePose === 'pinch';
      if (hasHand && isCursorPose) {
        const tip = { x: lm[8].x, y: lm[8].y };
        emaCur = emaCur ? { x: 0.35 * tip.x + 0.65 * emaCur.x, y: 0.35 * tip.y + 0.65 * emaCur.y } : tip;
        events.push({ type: 'cursor', x: emaCur.x, y: emaCur.y });

        // velocity history
        cursorHist.push({ t: tMs, x: emaCur.x, y: emaCur.y });
        while (cursorHist.length && tMs - cursorHist[0].t > 150) cursorHist.shift();

        // ---- SCROLL ----
        let didScroll = false;
        if (cursorHist.length >= 2) {
          const prev = cursorHist[cursorHist.length - 2];
          const dy = emaCur.y - prev.y;
          const dx = emaCur.x - prev.x;
          const dtp = (tMs - prev.t) / 1000 || 1e-6;
          // velocity over the recent window (~150ms) — resists per-frame jitter
          const a = cursorHist[0], b = cursorHist[cursorHist.length - 1];
          const dtWin = (b.t - a.t) / 1000 || 1e-6;
          const vy = (b.y - a.y) / dtWin;
          const vx = (b.x - a.x) / dtWin;
          const scrollLike = Math.abs(vy) > 0.8 && Math.abs(vy) > 2 * Math.abs(vx);
          if (scrollLike) {
            // reset accumulation if direction reversed
            if (frameTravel !== 0 && Math.sign(dy) !== Math.sign(frameTravel)) frameTravel = 0;
            frameTravel += dy;
          }
          // else: keep whatever we've accumulated; do not destroy displacement
        }

        if (Math.abs(frameTravel) >= 0.05 && tMs - lastScrollAt >= 110) {
          // finger UP (y decreasing, frameTravel negative) => positive dy
          const notches = -Math.trunc(frameTravel / 0.05);
          if (notches !== 0) {
            events.push({ type: 'scroll', dy: notches });
            frameTravel -= -notches * 0.05;
            lastScrollAt = tMs;
            scrollingUntil = tMs + 250;
            didScroll = true;
          }
        }

        // ---- CLICK via pinch ----
        const pinch = c.pinch;
        if (pinch > 0.5) pinchArmed = true;
        if (pinchArmed && pinch < 0.33 && tMs - lastClickAt >= 900) {
          events.push({ type: 'click', x: emaCur.x, y: emaCur.y, via: 'pinch' });
          lastClickAt = tMs;
          pinchArmed = false;
          dwellActive = false;
          dwellRef = null;
          dwellMovedSinceClick = false;
        }

        // ---- DWELL ----
        const scrolling = tMs < scrollingUntil;
        if (!dwellRef) { dwellRef = { x: emaCur.x, y: emaCur.y }; dwellSince = tMs; }
        if (dist(emaCur, dwellRef) > 0.05) dwellMovedSinceClick = true;
        if (dist(emaCur, dwellRef) > 0.03) {
          dwellRef = { x: emaCur.x, y: emaCur.y };
          dwellSince = tMs;
          dwellActive = false;
        } else if (!scrolling && !didScroll && dwellMovedSinceClick && tMs - lastClickAt >= 900) {
          const held = tMs - dwellSince;
          if (held >= 0 && held < 900) {
            const p = Math.min(1, held / 900);
            if (p > 0) events.push({ type: 'dwell', p });
          }
          if (held >= 900 && !dwellActive) {
            events.push({ type: 'click', x: emaCur.x, y: emaCur.y, via: 'dwell' });
            lastClickAt = tMs;
            dwellActive = true;
            dwellMovedSinceClick = false;
          }
        }
      } else {
        // not a cursor pose: reset transient browse state
        emaCur = null;
        cursorHist = [];
        frameTravel = 0;
        dwellRef = null;
        dwellActive = false;
        pinchArmed = false;
      }
    }

    // =====================================================================
    // PALM (any mode)
    // =====================================================================
    if (stablePose === 'palm' && hasHand) {
      const pc = { x: (lm[0].x + lm[5].x + lm[9].x + lm[13].x + lm[17].x) / 5,
                   y: (lm[0].y + lm[5].y + lm[9].y + lm[13].y + lm[17].y) / 5 };
      if (!palmRef || dist(pc, palmRef) > 0.03) { palmRef = pc; palmSince = tMs; }
      else if (!palmHeldFired && tMs - palmSince >= 600) {
        palmHeldFired = true;
        events.push({ type: 'palm' });
      }
    } else { palmRef = null; }

    return events;
  }

  resetState();
  return engine;
}
