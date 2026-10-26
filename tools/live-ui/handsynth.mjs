// handsynth.mjs — synthetic right-hand landmark generator in DISPLAY space.
// Produces MediaPipe-Hands-shaped 21-point frames and clip builders for tests.
// Display space: normalized 0..1, selfie-mirrored, origin top-left, y down.

const FPS = 30;
const DT = 1000 / FPS;

function L(x, y) { return { x, y, z: 0, score: 0.92 }; }

// Build a 21-landmark right hand for a given pose.
// wrist is {x,y}; scale sets hand size (wrist->middle-MCP distance ~ scale).
// For 'point'/'pinch', if `tip` is given, index tip (8) is placed exactly there
// and the wrist sits below it.
export function handPose(pose, wrist, { scale = 0.16, tip } = {}) {
  // Local frame: y up is -y in display (y down). "up" = toward smaller y.
  // We lay fingers pointing up from the wrist.
  let wx = wrist.x, wy = wrist.y;

  // If a tip is given for point/pinch, orient so index tip lands on it and the
  // wrist is directly below the tip.
  if (tip && (pose === 'point' || pose === 'pinch')) {
    wx = tip.x;
    wy = tip.y + scale * 2.0; // wrist below the tip
  }

  const s = scale;
  // palm column x-offsets per finger (thumb..pinky), relative to wrist
  const colX = { thumb: -0.9, index: -0.35, middle: 0.0, ring: 0.35, pinky: 0.7 };

  // helper to place a finger's 4 points going up, with given extension factor
  // ext in [0..1]: 1 fully extended, ~0.25 curled (tip near palm)
  function finger(colKey, mcpLenF, lengths, ext) {
    const bx = wx + colX[colKey] * s;
    const mcpY = wy - mcpLenF * s;           // MCP height above wrist
    const pts = [{ x: bx, y: mcpY }];        // MCP
    let y = mcpY, x = bx;
    for (let i = 0; i < 3; i++) {
      // extended -> continue upward; curled -> fold back down toward palm
      const seg = lengths[i] * s;
      if (ext >= 0.6) {
        y -= seg;
      } else {
        // curl: joints bend, tip ends up near/below MCP
        y -= seg * (ext - 0.5) * 2; // for ext~0.25 this is negative -> downward
      }
      pts.push({ x, y });
    }
    return pts; // [mcp, pip, dip, tip]
  }

  const extended = 1.0, curled = 0.25;
  const idxExt = (pose === 'point' || pose === 'v' || pose === 'palm') ? extended
    : (pose === 'pinch') ? 0.75 : curled;
  const midExt = (pose === 'v' || pose === 'palm') ? extended : curled;
  const ringExt = (pose === 'palm') ? extended : curled;
  const pkyExt = (pose === 'palm') ? extended : curled;

  // finger landmark lengths (mcpHeight, [seg1,seg2,seg3])
  const idx = finger('index', 1.0, [0.55, 0.4, 0.35], idxExt);
  let mid = finger('middle', 1.05, [0.6, 0.42, 0.36], midExt);
  const ring = finger('ring', 1.0, [0.55, 0.4, 0.34], ringExt);
  const pky = finger('pinky', 0.85, [0.45, 0.33, 0.3], pkyExt);

  // V spread: push index left, middle right so tips separate > 0.55*size
  if (pose === 'v') {
    for (const p of idx) p.x -= 0.5 * s;
    for (const p of mid) p.x += 0.5 * s;
  }

  // thumb: points out to the side. For pinch, bring thumb tip to index tip.
  const thumbBaseX = wx + colX.thumb * s;
  const thumbBaseY = wy - 0.2 * s;
  let thumb;
  if (pose === 'pinch') {
    // CMC, MCP along the side, IP, then TIP meeting the index tip
    const it = idx[3];
    const cmc = { x: thumbBaseX, y: thumbBaseY };
    const mcp = { x: thumbBaseX + 0.3 * s, y: thumbBaseY - 0.3 * s };
    const ip = { x: it.x - 0.25 * s, y: it.y + 0.25 * s };
    const ttip = { x: it.x - 0.02 * s, y: it.y + 0.02 * s }; // ~ touching index tip
    thumb = [cmc, mcp, ip, ttip];
  } else {
    const cmc = { x: thumbBaseX, y: thumbBaseY };
    const mcp = { x: thumbBaseX - 0.25 * s, y: thumbBaseY - 0.3 * s };
    const ip = { x: thumbBaseX - 0.5 * s, y: thumbBaseY - 0.55 * s };
    const ttip = { x: thumbBaseX - 0.75 * s, y: thumbBaseY - 0.8 * s };
    thumb = [cmc, mcp, ip, ttip];
  }

  // For point/pinch with a given tip, snap index tip exactly onto it.
  if (tip && (pose === 'point' || pose === 'pinch')) {
    const cur = idx[3];
    const shx = tip.x - cur.x, shy = tip.y - cur.y;
    for (const p of idx) { p.x += shx; p.y += shy; }
    if (pose === 'pinch') {
      // keep thumb tip at index tip
      thumb[3] = { x: tip.x - 0.02 * s, y: tip.y + 0.02 * s };
      thumb[2] = { x: tip.x - 0.25 * s, y: tip.y + 0.25 * s };
    }
  }

  // assemble: 0 wrist; 1-4 thumb; 5-8 index; 9-12 middle; 13-16 ring; 17-20 pinky
  const out = [];
  out[0] = L(wx, wy);
  out[1] = L(thumb[0].x, thumb[0].y);
  out[2] = L(thumb[1].x, thumb[1].y);
  out[3] = L(thumb[2].x, thumb[2].y);
  out[4] = L(thumb[3].x, thumb[3].y);
  out[5] = L(idx[0].x, idx[0].y);
  out[6] = L(idx[1].x, idx[1].y);
  out[7] = L(idx[2].x, idx[2].y);
  out[8] = L(idx[3].x, idx[3].y);
  out[9] = L(mid[0].x, mid[0].y);
  out[10] = L(mid[1].x, mid[1].y);
  out[11] = L(mid[2].x, mid[2].y);
  out[12] = L(mid[3].x, mid[3].y);
  out[13] = L(ring[0].x, ring[0].y);
  out[14] = L(ring[1].x, ring[1].y);
  out[15] = L(ring[2].x, ring[2].y);
  out[16] = L(ring[3].x, ring[3].y);
  out[17] = L(pky[0].x, pky[0].y);
  out[18] = L(pky[1].x, pky[1].y);
  out[19] = L(pky[2].x, pky[2].y);
  out[20] = L(pky[3].x, pky[3].y);
  return out;
}

// ---- clip helpers ----
function frames(n, fn, startT = 0) {
  const out = [];
  for (let i = 0; i < n; i++) out.push({ t_ms: Math.round(startT + i * DT), lm: fn(i, n) });
  return out;
}

function nFor(ms) { return Math.max(1, Math.round(ms / DT)); }

// A writing clip: 250ms still lead-in at start point, the stroke, 600ms hold.
function writeClip(tipFn, nStroke) {
  const lead = nFor(250);
  const hold = nFor(600);
  const seq = [];
  let t = 0;
  const p0 = tipFn(0);
  for (let i = 0; i < lead; i++) { seq.push({ t_ms: Math.round(t), lm: handPose('point', { x: p0.x, y: p0.y + 0.3 }, { tip: p0 }) }); t += DT; }
  for (let i = 0; i < nStroke; i++) {
    const p = tipFn(i / (nStroke - 1));
    seq.push({ t_ms: Math.round(t), lm: handPose('point', { x: p.x, y: p.y + 0.3 }, { tip: p }) });
    t += DT;
  }
  const pe = tipFn(1);
  for (let i = 0; i < hold; i++) { seq.push({ t_ms: Math.round(t), lm: handPose('point', { x: pe.x, y: pe.y + 0.3 }, { tip: pe }) }); t += DT; }
  return seq;
}

// air-write 'C': open arc top-right -> left -> bottom-right, counterclockwise.
export function airC({ cx = 0.5, cy = 0.5, r = 0.18 } = {}) {
  const nStroke = nFor(900);
  const tipFn = (t) => {
    // start -60deg, sweep to -300deg (CCW through left). y down => y = cy - r*sin
    const a = (-60 + (-300 - -60) * t) * Math.PI / 180;
    return { x: cx + r * Math.cos(a), y: cy - r * Math.sin(a) };
  };
  return writeClip(tipFn, nStroke);
}

// air-write 'V': down-right then up-right.
export function airV({ x = 0.4, y = 0.35, w = 0.18, h = 0.22 } = {}) {
  const nStroke = nFor(800);
  const topL = { x, y };
  const bottom = { x: x + w / 2, y: y + h };
  const topR = { x: x + w, y };
  const tipFn = (t) => {
    if (t <= 0.5) {
      const u = t / 0.5;
      return { x: topL.x + u * (bottom.x - topL.x), y: topL.y + u * (bottom.y - topL.y) };
    }
    const u = (t - 0.5) / 0.5;
    return { x: bottom.x + u * (topR.x - bottom.x), y: bottom.y + u * (topR.y - bottom.y) };
  };
  return writeClip(tipFn, nStroke);
}

// static V-sign hold
export function vSign(ms = 900) {
  return frames(nFor(ms), () => handPose('v', { x: 0.5, y: 0.7 }));
}

// static palm hold
export function palm(ms = 900) {
  return frames(nFor(ms), () => handPose('palm', { x: 0.5, y: 0.7 }));
}

// no hand
export function idle(ms = 500) {
  return frames(nFor(ms), () => null);
}

// vertical slide in point pose from y0 to y1
export function slide({ x = 0.5, y0 = 0.8, y1 = 0.2, ms = 400 } = {}) {
  const n = nFor(ms);
  return frames(n, (i) => {
    const t = n > 1 ? i / (n - 1) : 0;
    const y = y0 + (y1 - y0) * t;
    return handPose('point', { x, y: y + 0.3 }, { tip: { x, y } });
  });
}

// horizontal hover (point pose) from (x0,y0) to (x1,y1)
export function hover({ x0 = 0.3, y0 = 0.5, x1 = 0.7, y1 = 0.5, ms = 500 } = {}) {
  const n = nFor(ms);
  return frames(n, (i) => {
    const t = n > 1 ? i / (n - 1) : 0;
    const x = x0 + (x1 - x0) * t;
    const y = y0 + (y1 - y0) * t;
    return handPose('point', { x, y: y + 0.3 }, { tip: { x, y } });
  });
}

// hold the cursor still at (x,y) long enough for a dwell click
export function dwellAt({ x = 0.5, y = 0.5, ms = 1100 } = {}) {
  // tiny lead-in movement so dwellMovedSinceClick becomes true, then hold
  const n = nFor(ms);
  return frames(n, () => handPose('point', { x, y: y + 0.3 }, { tip: { x, y } }));
}

// point -> pinch -> point to produce exactly one pinch click
export function pinchAt({ x = 0.5, y = 0.5 } = {}) {
  const seq = [];
  let t = 0;
  const openN = nFor(200);   // point (pinch ratio high)
  const closeN = nFor(200);  // pinch closed
  const reopenN = nFor(200);
  for (let i = 0; i < openN; i++) { seq.push({ t_ms: Math.round(t), lm: handPose('point', { x, y: y + 0.3 }, { tip: { x, y } }) }); t += DT; }
  for (let i = 0; i < closeN; i++) { seq.push({ t_ms: Math.round(t), lm: handPose('pinch', { x, y: y + 0.3 }, { tip: { x, y } }) }); t += DT; }
  for (let i = 0; i < reopenN; i++) { seq.push({ t_ms: Math.round(t), lm: handPose('point', { x, y: y + 0.3 }, { tip: { x, y } }) }); t += DT; }
  return seq;
}

// ---- raw-camera 42-point export (two-hand slot layout) ----
// slots 0..20 = left hand, 21..41 = right hand; empty slot is 21 zero points.
export function toKp42(lm, { mirrored = true, slot = 'right' } = {}) {
  const zero = () => ({ x: 0, y: 0, z: 0, score: 0 });
  const conv = (p) => ({
    x: mirrored ? 1 - p.x : p.x,
    y: p.y,
    z: p.z || 0,
    score: p.score == null ? 0.92 : p.score,
  });
  const hand = (lm || []).slice(0, 21).map(conv);
  while (hand.length < 21) hand.push(zero());
  const empty = Array.from({ length: 21 }, zero);
  return slot === 'right' ? [...empty, ...hand] : [...hand, ...empty];
}

export { FPS, DT };
