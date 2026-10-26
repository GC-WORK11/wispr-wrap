// signbank.mjs — browser port of server/signbank.py: the teach-by-example
// sign bank. kp42 frames -> dominant-hand centroid trajectory (resample 32,
// RMS-normalize) + 84-dim hand-shape clip median; fused banded-DTW distance;
// accept = dist <= threshold AND traj-anchor veto (veto_eps tie band).
// Same math, same defaults — a take recorded here is scored the way the
// laptop daemon scores it.

const RESAMPLE_N = 32;
const MATCH_BAND = 4;
const MIN_POINTS = 8;
const MAX_EXEMPLARS = 5;
const HAND_KP = 21;
const N_HANDS = 2;
const SHAPE_DIMS = HAND_KP * 2 * N_HANDS; // 84
const INF = Infinity;

function resample(pts, n) {
  if (pts.length < 2 || n < 2) return pts.slice();
  const cum = [0];
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
    cum[i] = cum[i - 1] + Math.hypot(dx, dy);
  }
  const total = cum[cum.length - 1];
  if (total <= 1e-9) return new Array(n).fill([pts[0][0], pts[0][1]]);
  const out = [];
  let j = 0;
  for (let k = 0; k < n; k++) {
    const target = (total * k) / (n - 1);
    while (j + 1 < pts.length - 1 && cum[j + 1] < target) j++;
    const seg = Math.max(cum[j + 1] - cum[j], 1e-9);
    const t = Math.min(Math.max((target - cum[j]) / seg, 0), 1);
    out.push([pts[j][0] + (pts[j + 1][0] - pts[j][0]) * t,
              pts[j][1] + (pts[j + 1][1] - pts[j][1]) * t]);
  }
  return out;
}

function normalize(pts) {
  if (!pts.length) return [];
  const n = pts.length;
  let mx = 0, my = 0;
  for (const p of pts) { mx += p[0]; my += p[1]; }
  mx /= n; my /= n;
  let rms = 0;
  for (const p of pts) rms += (p[0] - mx) ** 2 + (p[1] - my) ** 2;
  rms = Math.sqrt(rms / n);
  const s = Math.max(rms, 1e-6);
  return pts.map(p => [(p[0] - mx) / s, (p[1] - my) / s]);
}

// banded Sakoe-Chiba DTW; add folds a per-pair constant channel into the DP
function dtwAdd(a, b, band, add) {
  const n = a.length, m = b.length;
  if (!n || !m) return INF;
  band = Math.max(band, Math.abs(n - m) + 1, 1);
  let prev = new Array(m + 1).fill(INF);
  let cur = new Array(m + 1).fill(INF);
  prev[0] = 0;
  for (let i = 1; i <= n; i++) {
    const lo = Math.max(i - band, 1), hi = Math.min(i + band, m);
    cur[lo - 1] = INF;
    const ai = a[i - 1], ax = ai[0], ay = ai[1];
    for (let j = lo; j <= hi; j++) {
      const bj = b[j - 1];
      const dx = ax - bj[0], dy = ay - bj[1];
      let best = prev[j];
      if (cur[j - 1] < best) best = cur[j - 1];
      if (prev[j - 1] < best) best = prev[j - 1];
      cur[j] = Math.sqrt(dx * dx + dy * dy + add) + best;
    }
    [prev, cur] = [cur, prev];
  }
  return prev[m] / Math.max(n, m);
}

function shapeSs(a, b, w) {
  if (a == null || b == null) return [0, 0];
  let ss = 0, k = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const x = a[i], y = b[i];
    if (Number.isFinite(x) && Number.isFinite(y)) {
      const dd = x - y;
      ss += w * dd * dd;
      k++;
    }
  }
  return [ss, k];
}

function pt(p) {
  if (p == null) return [0, 0, 0];
  if (typeof p === 'object' && !Array.isArray(p)) {
    const s = p.score == null ? 1 : +p.score;
    return [+p.x || 0, +p.y || 0, s];
  }
  return [+p[0] || 0, +p[1] || 0, p.length > 2 ? +p[2] : 1];
}

// one hand in one frame -> [centroid|null, shape42|null, bboxScale]
function handObs(pts, minScore, minKeys) {
  const conf = [];
  for (let i = 0; i < pts.length && i < HAND_KP; i++) {
    if (pts[i][2] >= minScore) conf.push([i, pts[i]]);
  }
  if (conf.length < minKeys) return [null, null, 0];
  const sw = conf.reduce((a, [, p]) => a + p[2], 0);
  if (sw <= 0) return [null, null, 0];
  const cx = conf.reduce((a, [, p]) => a + p[0] * p[2], 0) / sw;
  const cy = conf.reduce((a, [, p]) => a + p[1] * p[2], 0) / sw;
  let minX = INF, minY = INF, maxX = -INF, maxY = -INF;
  for (const [, p] of conf) {
    if (p[0] < minX) minX = p[0]; if (p[0] > maxX) maxX = p[0];
    if (p[1] < minY) minY = p[1]; if (p[1] > maxY) maxY = p[1];
  }
  const bx = (minX + maxX) / 2, by = (minY + maxY) / 2;
  const bs = Math.max(maxX - minX, maxY - minY, 1e-6);
  const shape = new Array(HAND_KP * 2).fill(NaN);
  for (const [i, p] of conf) {
    shape[2 * i] = (p[0] - bx) / bs;
    shape[2 * i + 1] = (p[1] - by) / bs;
  }
  return [[cx, cy], shape, bs];
}

function frameHands(frame, minScore, minKeys) {
  const raw = frame.points || [];
  const pts = raw.map(pt);
  let hands;
  if (pts.length === HAND_KP) {
    const idx = String(frame.hand || 'right').toLowerCase() === 'right' ? 1 : 0;
    hands = [null, null];
    hands[idx] = pts;
  } else {
    hands = [pts.slice(0, HAND_KP), pts.slice(HAND_KP, 2 * HAND_KP)];
  }
  return hands.map(h => (h == null || h.length < HAND_KP) ? [null, null, 0] : handObs(h, minScore, minKeys));
}

// upper median per dim; never-observed dims -> 0.0 fill (eval's own rule)
function upperMedian(rows, dims) {
  if (!rows.length) return [null, 0];
  const out = [];
  let allNan = 0;
  for (let dd = 0; dd < dims; dd++) {
    const vals = rows.map(r => r[dd]).filter(Number.isFinite).sort((x, y) => x - y);
    if (vals.length) out.push(vals[vals.length >> 1]);
    else { out.push(0); allNan++; }
  }
  return [out, allNan];
}

export class SignBank {
  constructor({
    rejectThreshold = 0.6, minScore = 0.5, minKeys = 8,
    shapeDimWeight = 0.1, vetoEps = 0.05, stillFrac = 0.08,
  } = {}) {
    this.rejectThreshold = rejectThreshold;
    this.minScore = minScore;
    this.minKeys = minKeys;
    this.shapeDimWeight = shapeDimWeight;
    this.vetoEps = vetoEps;
    this.stillFrac = stillFrac;
    this.templates = []; // {gloss,label,samples:[{traj,shape}]}
  }

  // frames -> [traj32, shape84|null, nCentroidPts]
  prep(frames) {
    frames = frames || [];
    const cents = [[], []], rows = [[], []], scales = [[], []];
    for (const fr of frames) {
      const obs = frameHands(fr, this.minScore, this.minKeys);
      for (let h = 0; h < N_HANDS; h++) {
        const [c, s, bs] = obs[h];
        if (c != null) { cents[h].push(c); scales[h].push(bs); }
        if (s != null) rows[h].push(s);
      }
    }
    const dom = cents[1].length >= cents[0].length ? 1 : 0;
    let raw = cents[dom];
    const npts = raw.length;
    if (npts >= 2) {
      let mx = 0, my = 0;
      for (const p of raw) { mx += p[0]; my += p[1]; }
      mx /= npts; my /= npts;
      let rms = 0;
      for (const p of raw) rms += (p[0] - mx) ** 2 + (p[1] - my) ** 2;
      rms = Math.sqrt(rms / npts);
      const handPx = scales[dom].length ? scales[dom].reduce((a, b) => a + b, 0) / scales[dom].length : 0;
      // stillness gate: jitter on a held hand is a hold, not a motion
      if (handPx > 0 ? rms < this.stillFrac * handPx : rms < 1e-9) {
        raw = new Array(npts).fill([mx, my]);
      }
    }
    const traj = normalize(resample(raw, RESAMPLE_N));
    const [lv] = rows[0].length ? upperMedian(rows[0], HAND_KP * 2) : [null];
    const [rv] = rows[1].length ? upperMedian(rows[1], HAND_KP * 2) : [null];
    let shape = null;
    if (lv != null || rv != null) {
      shape = (lv || new Array(HAND_KP * 2).fill(0)).concat(rv || new Array(HAND_KP * 2).fill(0));
    }
    return [traj, shape, npts];
  }

  _find(gloss) {
    for (const t of this.templates) if (t.gloss === gloss) return t;
    const g = String(gloss).toLowerCase();
    for (const t of this.templates) if (t.gloss.toLowerCase() === g) return t;
    return null;
  }

  addExemplar(gloss, frames, label = '') {
    gloss = String(gloss || '').trim();
    if (!gloss) return false;
    const [traj, shape, npts] = this.prep(frames);
    if (npts < MIN_POINTS || !traj.length) return false;
    let t = this._find(gloss);
    if (!t) {
      t = { gloss, label, samples: [] };
      this.templates.push(t);
    }
    t.samples.push({ traj: traj.map(p => [p[0], p[1]]), shape });
    while (t.samples.length > MAX_EXEMPLARS) t.samples.shift();
    if (label) t.label = label;
    return true;
  }

  remove(gloss) {
    const t = this._find(gloss);
    if (!t) return false;
    this.templates.splice(this.templates.indexOf(t), 1);
    return true;
  }

  list() {
    return this.templates.map(t => ({ gloss: t.gloss, label: t.label || '', n: t.samples.length }));
  }

  _score(frames) {
    const [traj, shape, npts] = this.prep(frames);
    if (npts < MIN_POINTS || !traj.length || !this.templates.length) return null;
    const wS = this.shapeDimWeight, wTraj = 2.0;
    const totalW = wTraj + wS * SHAPE_DIMS;
    const trajD = [], fusedD = [], shapeD = [];
    for (const t of this.templates) {
      let bt = INF, bf = INF, bs = INF;
      for (const s of t.samples) {
        const dT = dtwAdd(traj, s.traj, MATCH_BAND, 0);
        if (dT < bt) bt = dT;
        const [shapeSS, k] = shapeSs(shape, s.shape, wS);
        let dF, dS;
        if (k > 0) {
          const used = wTraj + wS * k;
          const r = totalW / used;
          dF = dtwAdd(traj, s.traj, MATCH_BAND, shapeSS) * Math.sqrt(r);
          dS = Math.sqrt(shapeSS * (wS * SHAPE_DIMS) / (wS * k));
        } else if (shape == null && s.shape == null) {
          dF = dT; dS = INF;
        } else {
          dF = dT * Math.sqrt(totalW / wTraj); dS = INF;
        }
        if (dF < bf) bf = dF;
        if (dS < bs) bs = dS;
      }
      trajD.push(bt); fusedD.push(bf); shapeD.push(bs);
    }
    const order = this.templates.map((_, i) => i).sort((a, b) => fusedD[a] - fusedD[b]);
    const bi = order[0];
    const tMin = trajD.reduce((a, v, i) => (v < trajD[a] ? i : a), 0);
    const sMin = shapeD.reduce((a, v, i) => (v < shapeD[a] ? i : a), 0);
    return {
      gloss: this.templates[bi].gloss, dist: fusedD[bi],
      secondGloss: order.length > 1 ? this.templates[order[1]].gloss : null,
      second: order.length > 1 ? fusedD[order[1]] : INF,
      trajGloss: this.templates[tMin].gloss, trajD: trajD[tMin],
      trajDWinner: trajD[bi],
      shapeGloss: this.templates[sMin].gloss,
    };
  }

  match(frames) {
    const sc = this._score(frames);
    if (sc == null) {
      return { gloss: null, dist: INF, accepted: false, margin: INF,
               score: 0, reason: this.templates.length ? 'thin-query' : 'empty-bank' };
    }
    const vetoOk = sc.trajDWinner <= sc.trajD + this.vetoEps;
    const accepted = sc.dist <= this.rejectThreshold && vetoOk;
    const reason = sc.dist > this.rejectThreshold ? 'threshold' : !vetoOk ? 'veto' : 'ok';
    const t = this._find(sc.gloss);
    const margin = Number.isFinite(sc.dist) ? sc.second - sc.dist : INF;
    return {
      gloss: sc.gloss, dist: sc.dist, accepted,
      secondGloss: sc.secondGloss, margin,
      score: 1 / (1 + sc.dist),
      label: t ? t.label || '' : '', reason,
      trajGloss: sc.trajGloss, shapeGloss: sc.shapeGloss,
    };
  }

  toJSON() {
    return {
      format: 'wf-signbank/1', engine: 'js-dtw',
      reject_threshold: this.rejectThreshold,
      templates: this.templates.map(t => ({
        gloss: t.gloss, label: t.label || '',
        samples: t.samples.map(s => ({
          traj: s.traj,
          shape: s.shape == null ? null : s.shape.map(v => (Number.isFinite(v) ? v : null)),
        })),
      })),
    };
  }

  static fromJSON(d) {
    const b = new SignBank({ rejectThreshold: d && d.reject_threshold != null ? d.reject_threshold : 0.6 });
    for (const t of (d && d.templates) || []) {
      if (!t || typeof t !== 'object') continue;
      const samples = [];
      for (const s of t.samples || []) {
        if (!s || typeof s !== 'object') continue;
        const traj = (s.traj || []).map(p => [+p[0] || 0, +p[1] || 0]);
        const shape = s.shape == null ? null : s.shape.map(v => (v == null ? NaN : +v));
        samples.push({ traj, shape });
      }
      if (t.gloss) b.templates.push({ gloss: String(t.gloss), label: t.label || '', samples });
    }
    return b;
  }
}
