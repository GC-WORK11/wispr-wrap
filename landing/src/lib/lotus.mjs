// ASCII lotus renderer. Same petal geometry and tonal shading as the app's
// web/js/flower.js, without the composer, charge and send-burst states.
// Pure functions: the build uses them for the static frame, the browser uses
// them for the bloom. A frame is five text layers, deepest tone first.

const cc = (s) => s.charCodeAt(0);
const RAMP = [...' .,:-=+*#%@'].map(cc);
const DASH = cc('-'), UND = cc('_'), SL = cc('/'), BS = cc('\\'), PIPE = cc('|');
const STAR = cc('*'), AT = cc('@'), OO = cc('o'), SP = 32;
const NF = 8;
const NT = 5;

export const LOTUS_TONES = ['#c2185b', '#e91e8c', '#ff309c', '#ff6fb8', '#ffb3d9'];

function hash(x, y) {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

const LF = [];
const LB = [];
for (let k = 0; k < NF; k++) {
  LF.push(0.84 + 0.22 * hash(k, 3));
  LB.push(0.8 + 0.22 * hash(k, 9));
}

const ramp = (b) => RAMP[b <= 0 ? 0 : b >= 1 ? 10 : Math.round(b * 10)];

/**
 * @param {{ cols: number, rows: number, cw: number, lh: number }} g grid: columns, rows, cell width and height in px
 */
export function createLotus({ cols, rows, cw, lh }) {
  const stride = cols + 1;
  const W = cols * cw;
  const H = rows * lh;
  const bufs = Array.from({ length: NT }, () => new Uint8Array(rows * stride));
  const tmpl = new Uint8Array(rows * stride).fill(SP);
  for (let j = 0; j < rows; j++) tmpl[j * stride + cols] = 10;
  const dec = new TextDecoder();
  const G = { x: 0, y: 0, m: 1 };
  let PQ = 0;

  const put = (i, layer, ch, tone) => {
    const t = tone == null ? (layer === 2 ? 1 : layer === 1 ? 2 : 3) : tone;
    for (let k = 0; k < NT; k++) bufs[k][i] = k === t ? ch : SP;
  };

  function dirc(nx, ny, under) {
    let a = Math.atan2(-nx, -ny) * 57.29578;
    if (a < 0) a += 180;
    if (a >= 180) a -= 180;
    if (a < 24 || a >= 156) return under && ny > 0.3 ? UND : DASH;
    return a < 76 ? SL : a < 104 ? PIPE : BS;
  }

  function grad(F, x, y) {
    const gx = F(x + 1.5, y) - F(x - 1.5, y);
    const gy = F(x, y + 1.5) - F(x, y - 1.5);
    const m = Math.hypot(gx, gy) || 1;
    G.x = gx / m;
    G.y = gy / m;
    G.m = m / 3;
    return G;
  }

  const band = (g) => 0.55 * (Math.abs(g.x) * cw + Math.abs(g.y) * lh);

  function petalR(a, N, lens, t, fl) {
    const seg = 6.2831853 / N;
    let k = Math.round(a / seg);
    const q = (a - k * seg) / (seg / 2);
    k = ((k % N) + N) % N;
    PQ = q;
    return 0.16 + 0.84 * Math.pow(Math.max(0, 1 - q * q), 0.58) * lens[k] * (1 + fl * Math.sin(t * 1.7 + k * 1.9));
  }

  function leaf(ax, ay, ang, len, wid) {
    if (len < cw * 2) return;
    const dx = Math.cos(ang);
    const dy = -Math.sin(ang);
    const i0 = Math.max(0, Math.floor((ax - len) / cw));
    const i1 = Math.min(cols - 1, Math.ceil((ax + len) / cw));
    const j0 = Math.max(0, Math.floor((ay - len) / lh));
    const j1 = Math.min(rows - 1, Math.ceil((ay + len) / lh));
    for (let j = j0; j <= j1; j++) {
      const y = (j + 0.5) * lh;
      for (let i = i0; i <= i1; i++) {
        const x = (i + 0.5) * cw;
        const px = x - ax;
        const py = y - ay;
        const u = px * dx + py * dy;
        if (u <= 0 || u >= len) continue;
        const sn = Math.sin((Math.PI * u) / len);
        const half = wid * Math.pow(sn, 0.8);
        const v = -px * dy + py * dx;
        const av = Math.abs(v);
        if (av >= half) continue;
        const idx = j * stride + i;
        const dh = wid * 0.8 * Math.pow(sn, -0.2) * Math.cos((Math.PI * u) / len) * Math.PI / len;
        const sv = v < 0 ? -1 : 1;
        const gx = -sv * dy - dh * dx;
        const gy = sv * dx - dh * dy;
        const gm = Math.hypot(gx, gy) || 1;
        G.x = gx / gm;
        G.y = gy / gm;
        if ((half - av) / gm < band(G)) put(idx, 1, dirc(G.x, G.y, true), 2);
        else if (av < cw * 0.5) put(idx, 1, dirc(-dy, dx, false), 3);
        else put(idx, 0, ramp(0.3 + 0.4 * (1 - av / half)), 4);
      }
    }
  }

  /** @returns {string[]} five text layers, tone 0 (deep) to 4 (light) */
  function frame(t, bloom) {
    for (const b of bufs) b.set(tmpl);
    const top = lh * 3;
    const base = H - 2;
    const cx = W / 2;
    const R = Math.min(0.3 * W, (base - top) / 3.6);
    const TILT = 0.92;
    const hx = cx + Math.sin(t * 0.7) * 0.045 * R + Math.sin(t * 1.9) * 0.012 * R;
    const hyFull = top + R * 1.12;
    const B = bloom;
    const rot = t * 0.06;
    const rd = R * 0.2 * (0.55 + 0.45 * Math.min(1, bloom));

    // The stem grows from the base and the head rides its tip while it opens.
    const sLenFull = base - (hyFull + rd * 0.5);
    const yTip = base - sLenFull * Math.min(1, bloom * 1.7);
    const hy = Math.max(hyFull, yTip - rd * 0.5);
    const yTop = hy + rd * 0.5;
    const sLen = base - yTop;
    const bend = 0.09 * R * Math.sin(t * 0.45 + 1.3);
    const xs = (s) => cx + (hx - cx) * Math.pow(s, 1.5) + bend * Math.sin(Math.PI * s);
    if (sLen > R * 0.7) {
      const lg = Math.min(1, Math.max(0, (bloom - 0.25) * 1.6));
      leaf(xs(0.3), base - sLen * 0.3, 2.55 + 0.06 * Math.sin(t * 0.8), R * 0.5 * lg, R * 0.14);
      leaf(xs(0.52), base - sLen * 0.52, 0.62 + 0.06 * Math.sin(t * 0.8 + 2), R * 0.46 * lg, R * 0.13);
    }
    if (sLen > 1) {
      for (let j = Math.max(0, Math.floor(yTop / lh)); j <= Math.min(rows - 1, Math.floor(base / lh)); j++) {
        const y = (j + 0.5) * lh;
        if (y < yTop || y > base) continue;
        const s = (base - y) / sLen;
        const x = xs(s);
        const i = Math.floor(x / cw);
        if (i < 0 || i >= cols) continue;
        const tx = xs(Math.min(1, s + 0.02)) - x;
        const ty = -0.02 * sLen;
        put(j * stride + i, 1, dirc(-ty, tx, false), s > 0.5 ? 1 : 2);
      }
    }

    // Head: disc, front petals and offset back petals, outlined with directional glyphs.
    const Ff = (x, y) => {
      const dx = x - hx;
      const dy = (y - hy) / TILT;
      return Math.hypot(dx, dy) - R * B * petalR(Math.atan2(dy, dx) - rot, NF, LF, t, 0.025);
    };
    const Fb = (x, y) => {
      const dx = x - hx;
      const dy = (y - hy) / TILT;
      return Math.hypot(dx, dy) - R * B * 1.12 * petalR(Math.atan2(dy, dx) - rot - Math.PI / NF, NF, LB, t, 0.02);
    };
    const Rmax = R * 1.25 * B + 2;
    const i0 = Math.max(0, Math.floor((hx - Rmax) / cw));
    const i1 = Math.min(cols - 1, Math.ceil((hx + Rmax) / cw));
    const j0 = Math.max(0, Math.floor((hy - Rmax) / lh));
    const j1 = Math.min(rows - 1, Math.ceil((hy + Rmax) / lh));
    for (let j = j0; j <= j1; j++) {
      const y = (j + 0.5) * lh;
      for (let i = i0; i <= i1; i++) {
        const x = (i + 0.5) * cw;
        const dx = x - hx;
        const dy = (y - hy) / TILT;
        const r = Math.hypot(dx, dy);
        if (r > Rmax) continue;
        const idx = j * stride + i;
        const th = Math.atan2(dy, dx);
        if (r < rd) {
          const u = r / rd;
          put(idx, u < 0.8 ? 2 : 1, u < 0.42 ? AT : u < 0.8 ? OO : STAR, u < 0.8 ? 0 : 1);
          continue;
        }
        const rf = R * B * petalR(th - rot, NF, LF, t, 0.025);
        const q = PQ;
        if (r < rf) {
          const e = rf - r;
          if (e < lh * 1.1) {
            const g = grad(Ff, x, y);
            if (e / g.m < band(g)) {
              put(idx, 2, dirc(g.x, g.y, true), 1);
              continue;
            }
          }
          const tt = r / rf;
          const aq = Math.abs(q);
          if (aq < 0.1 && tt > 0.28 && tt < 0.88) {
            put(idx, 0, dirc(-dy, dx, false), 3);
            continue;
          }
          put(idx, 1, ramp(0.08 + 0.3 * tt + 0.12 * (1 - aq)), tt < 0.4 ? 1 : tt < 0.7 ? 2 : 3);
          continue;
        }
        const rb = R * B * 1.12 * petalR(th - rot - Math.PI / NF, NF, LB, t, 0.02);
        if (r < rb) {
          const e = rb - r;
          if (e < lh * 1.1) {
            const g = grad(Fb, x, y);
            if (e / g.m < band(g)) {
              put(idx, 1, dirc(g.x, g.y, true), 3);
              continue;
            }
          }
          put(idx, 0, ramp(0.1 + (0.3 * r) / rb), 4);
        }
      }
    }
    return bufs.map((b) => dec.decode(b));
  }

  return { frame, cols, rows, cw, lh };
}
