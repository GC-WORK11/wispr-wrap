// endpointer.mjs — motion-based segmenter for kp42 hand-keypoint streams.
// Direct port of server/endpointer.py (same thresholds, same events):
// feed(frame) -> events: {type:'segment', frames, dur_s} | {type:'segment-drop'}
//   | {type:'mode', name} for a held open palm / held palms-together.
// Velocities are palm-widths/s, so normalized 0..1 coords work unchanged.

const WRIST = 0;
const TIPS = [4, 8, 12, 16, 20];
const MID_MCP = 9;
const ANCHORS = [0, 5, 9, 13, 17];
const PRESENCE_SCORE = 0.25;
const PRESENCE_KEYS = 6;

const d = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by);

function pt(p) {
  if (p == null) return [0, 0, 0];
  if (typeof p === 'object' && !Array.isArray(p)) {
    const s = p.score == null ? 1 : +p.score;
    return [+p.x || 0, +p.y || 0, s];
  }
  return [+p[0] || 0, +p[1] || 0, p.length > 2 ? +p[2] : 1];
}

class Hand {
  constructor(pts) {
    this.present = false;
    this.wx = this.wy = this.cx = this.cy = 0;
    this.palm = 1e-6;
    this.openness = 0;
    this.speed = 0;
    if (!pts || pts.length < 21) return;
    let conf = 0;
    for (let i = 0; i < 21; i++) if (pts[i][2] >= PRESENCE_SCORE) conf++;
    if (conf < PRESENCE_KEYS) return;
    if (d(pts[0][0], pts[0][1], pts[9][0], pts[9][1]) < 1e-4 &&
        d(pts[0][0], pts[0][1], pts[17][0], pts[17][1]) < 1e-4) return;
    this.present = true;
    this.wx = pts[WRIST][0]; this.wy = pts[WRIST][1];
    this.cx = ANCHORS.reduce((a, i) => a + pts[i][0], 0) / ANCHORS.length;
    this.cy = ANCHORS.reduce((a, i) => a + pts[i][1], 0) / ANCHORS.length;
    this.palm = Math.max(d(pts[0][0], pts[0][1], pts[9][0], pts[9][1]), 1e-6);
    this.openness = TIPS.reduce((a, t) => a + d(pts[t][0], pts[t][1], this.wx, this.wy), 0) / (TIPS.length * this.palm);
  }
}

export class Endpointer {
  constructor({
    enterV = 1.5, exitV = 0.7, ema = 0.45,
    quietS = 0.35, minSegS = 0.20, maxSegS = 3.0,
    preRoll = 4, minFrames = 4,
    holdS = 0.45, holdV = 0.6,
    openRatio = 1.75, rearmRatio = 1.40,
    namasteGap = 1.4, namasteOpen = 1.35,
    palmSign = 'PALM-OPEN', namasteSign = 'NAMASTE',
  } = {}) {
    this.enterV = enterV; this.exitV = exitV; this.ema = ema;
    this.quietS = quietS; this.minSegS = minSegS; this.maxSegS = maxSegS;
    this.minFrames = minFrames; this.holdS = holdS; this.holdV = holdV;
    this.openRatio = openRatio; this.rearmRatio = rearmRatio;
    this.namasteGap = namasteGap; this.namasteOpen = namasteOpen;
    this.palmSign = palmSign || null; this.namasteSign = namasteSign || null;
    this._preLen = Math.max(1, preRoll | 0);
    this.reset();
  }

  reset() {
    this._state = 'idle';
    this._seg = [];
    this._segT0 = 0;
    this._quiet = null;
    this._absent = 0;
    this._pre = [];
    this._prev = [null, null];
    this._t = null;
    this._ema = null;
    this._palm = [{ since: null, armed: false, seen: false },
                  { since: null, armed: false, seen: false }];
    this._nam = { since: null, armed: true };
    this._n = 0;
  }

  get active() { return this._state === 'active'; }
  get currentLen() { return this._seg.length; }

  _hands(frame) {
    const raw = frame.points || [];
    const pts = raw.map(pt);
    let hands;
    if (pts.length === 21) {
      const idx = String(frame.hand || 'right').toLowerCase() === 'left' ? 0 : 1;
      hands = [null, null];
      hands[idx] = pts;
    } else {
      hands = [pts.slice(0, 21), pts.slice(21, 42)];
    }
    return [new Hand(hands[0]), new Hand(hands[1])];
  }

  feed(frame) {
    const events = [];
    const t = (+frame.t_ms || 0) / 1000;
    const dt = this._t != null && t > this._t ? t - this._t : 0;
    const hands = this._hands(frame);

    let energy = 0;
    for (let i = 0; i < 2; i++) {
      const h = hands[i], p = this._prev[i];
      if (h.present && p && p.present && dt > 0) {
        const ruler = Math.max(h.palm, p.palm, 1e-6);
        const vw = d(h.wx, h.wy, p.wx, p.wy) / (dt * ruler);
        const vc = d(h.cx, h.cy, p.cx, p.cy) / (dt * ruler);
        const vs = Math.abs(h.openness - p.openness) / dt;
        h.speed = vw;
        energy += vw + 0.4 * vc + 0.5 * vs;
      }
    }
    this._prev = hands;
    this._t = t;
    this._ema = this._ema == null ? energy : this.ema * energy + (1 - this.ema) * this._ema;

    events.push(...this._modeEvents(hands, t));

    this._pre.push(frame);
    if (this._pre.length > this._preLen) this._pre.shift();

    if (hands.some(h => h.present)) this._absent = 0; else this._absent++;

    if (this._state === 'idle') {
      if (this._ema >= this.enterV && hands.some(h => h.present)) {
        this._state = 'active';
        this._seg = this._pre.slice();
        this._segT0 = (+this._pre[0].t_ms || 0) / 1000;
        this._quiet = null;
      }
    } else {
      this._seg.push(frame);
      if (t - this._segT0 >= this.maxSegS) {
        events.push(this._close(t, 'maxlen'));
      } else if (this._absent >= 2) {
        events.push(this._close(t, 'absent'));
        this._absent = 0;
      } else if (this._ema < this.exitV) {
        if (this._quiet == null) this._quiet = t;
        else if (t - this._quiet >= this.quietS) events.push(this._close(t, 'quiet'));
      } else {
        this._quiet = null;
      }
    }
    this._n++;
    return events;
  }

  _close(t, how) {
    const seg = this._seg;
    this._seg = [];
    this._state = 'idle';
    this._quiet = null;
    this._absent = 0;
    this._ema = null;
    const dur = t - this._segT0;
    if (seg.length >= this.minFrames && dur >= this.minSegS) {
      return { type: 'segment', frames: seg, dur_s: dur, t_ms: Math.round(t * 1000), how };
    }
    return { type: 'segment-drop', reason: 'short', n: seg.length, dur_s: dur, t_ms: Math.round(t * 1000) };
  }

  _modeEvents(hands, t) {
    const ev = [];
    const [a, b] = hands;
    let together = false;
    if (a.present && b.present) {
      const gap = d(a.cx, a.cy, b.cx, b.cy);
      together = gap <= this.namasteGap * (a.palm + b.palm) / 2;
    }
    for (let i = 0; i < 2; i++) {
      const h = hands[i], st = this._palm[i];
      if (h.present && !st.seen) {
        st.seen = true;
        if (this._n > 0) st.armed = true;
      } else if (!h.present && st.seen) {
        st.seen = false;
        st.armed = true;
      }
      const heldOpen = h.present && !together && this._state !== 'active' &&
        h.openness >= this.openRatio && h.speed <= this.holdV;
      if (heldOpen) {
        if (st.since == null) st.since = t;
        if (st.armed && t - st.since >= this.holdS) {
          st.armed = false;
          if (this.palmSign) ev.push({ type: 'mode', name: this.palmSign, t_ms: Math.round(t * 1000) });
        }
      } else {
        st.since = null;
        if (!h.present || h.openness <= this.rearmRatio) st.armed = true;
      }
    }
    const st = this._nam;
    if (together && a.openness >= this.namasteOpen && b.openness >= this.namasteOpen &&
        a.speed <= this.holdV && b.speed <= this.holdV) {
      if (st.since == null) st.since = t;
      if (st.armed && t - st.since >= this.holdS) {
        st.armed = false;
        if (this.namasteSign) ev.push({ type: 'mode', name: this.namasteSign, t_ms: Math.round(t * 1000) });
      }
    } else {
      st.since = null;
      st.armed = true;
    }
    return ev;
  }
}
