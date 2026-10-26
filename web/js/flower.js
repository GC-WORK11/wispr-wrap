// flower.js — the ASCII lotus. Ported from the ORIGINAL wispr-wrap composer
// (git show main:web/index.html): a flower of characters grows out of the
// composer, OPENS when the laptop link is up, charges while you type, and
// fires a pulse up the stem + a pollen ring on every send.
// Change from the original: colour only. The three brightness tiers are now
// one deep-pink lotus gradient on a light page (css: #f0/#f1/#f2); the silver
// aura, ring and stars are gone (no glow). 12 fps idle, 25 fps active, paused
// when hidden or when a full-screen sheet covers it.
const cc = s => s.charCodeAt(0);
const RAMP = [...' .,:-=+*#%@'].map(cc);
const DASH = cc('-'), UND = cc('_'), SL = cc('/'), BS = cc('\\'), PIPE = cc('|'),
      PLUS = cc('+'), STAR = cc('*'), AT = cc('@'), HASH = cc('#'), OO = cc('o'), SP = 32;
const NF = 8, LF = [], LB = [], G = { x: 0, y: 0, m: 1 };
const dec = new TextDecoder();

function hash(x, y) {
  let h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177); h ^= h >>> 16; return (h >>> 0) / 4294967296;
}
for (let k = 0; k < NF; k++) { LF.push(.84 + .22 * hash(k, 3)); LB.push(.8 + .22 * hash(k, 9)); }

let field, LY, anchor, topPad = 64;
let cols = 0, rows = 0, cw = 6, lh = 11, stride = 1, bufs = [], tmpl = null, P = null, lastF = 0, PQ = 0;
let bloom = 0, charge = 0, burstAt = -99, online = true, paused = false;   // blooms fully on every load; closes to a bud only if the first health check fails
const RM = matchMedia('(prefers-reduced-motion: reduce)');

// five tonal layers, deep -> light (css --r0..--r4). A cell's tone comes from depth: the core is deepest, outlines strong,
// fills soften toward the rim, the back petals and veins are the lightest. Asleep (offline) shifts everything one step lighter.
const NT = 5;
let off = 0;
const put = (i, l, ch, tone) => {
  let t = tone == null ? (l === 2 ? 1 : l === 1 ? 2 : 3) : tone;
  t = t + off; if (t > NT - 1) t = NT - 1; else if (t < 0) t = 0;
  for (let k = 0; k < NT; k++) bufs[k][i] = k === t ? ch : SP;
};
const ramp = b => RAMP[b <= 0 ? 0 : b >= 1 ? 10 : Math.round(b * 10)];
// glyph for a line whose normal is (nx,ny) in screen space
function dirc(nx, ny, under) {
  let a = Math.atan2(-nx, -ny) * 57.29578; if (a < 0) a += 180; if (a >= 180) a -= 180;
  if (a < 24 || a >= 156) return under && ny > .3 ? UND : DASH;
  return a < 76 ? SL : a < 104 ? PIPE : BS;
}
function grad(F, x, y) {
  const gx = F(x + 1.5, y) - F(x - 1.5, y), gy = F(x, y + 1.5) - F(x, y - 1.5), m = Math.hypot(gx, gy) || 1;
  G.x = gx / m; G.y = gy / m; G.m = m / 3; return G;
}
const band = g => .55 * (Math.abs(g.x) * cw + Math.abs(g.y) * lh);

function measure() {
  if (!anchor) return;
  const r = anchor.getBoundingClientRect();
  // centre on the screen, not on the pill: the + button sits left of the input
  P = { l: r.left, t: r.top, r: r.right, b: r.bottom, cx: innerWidth / 2 };
}
function grid() {
  const W = innerWidth, H = innerHeight, m = document.createElement('pre');
  m.style.cssText = 'position:absolute;visibility:hidden;margin:0;line-height:1;font-size:100px';
  m.textContent = '0'.repeat(40); field.appendChild(m);
  const fs = Math.max(8, Math.min(13, W / (66 * m.getBoundingClientRect().width / 4000)));
  m.style.fontSize = fs + 'px'; m.textContent = '0'.repeat(200);
  cw = m.getBoundingClientRect().width / 200; m.remove();
  lh = Math.round(fs * 112) / 100;
  const ds = document.documentElement.style;
  ds.setProperty('--fs', fs + 'px'); ds.setProperty('--lh', lh + 'px');
  cols = Math.ceil(W / cw) + 1; rows = Math.ceil(H / lh) + 1; stride = cols + 1;
  tmpl = new Uint8Array(rows * stride).fill(SP);
  for (let j = 0; j < rows; j++) tmpl[j * stride + cols] = 10;
  bufs = Array.from({ length: NT }, () => new Uint8Array(tmpl.length));
  measure();
}

function petalR(a, N, lens, t, fl) { // petal radius as a fraction of R; leaves petal-local offset in PQ
  const seg = 6.2831853 / N; let k = Math.round(a / seg); const q = (a - k * seg) / (seg / 2);
  k = ((k % N) + N) % N; PQ = q;
  return .16 + .84 * Math.pow(Math.max(0, 1 - q * q), .58) * lens[k] * (1 + fl * Math.sin(t * 1.7 + k * 1.9));
}

function leaf(ax, ay, ang, len, wid) {
  if (len < cw * 2) return;
  const dx = Math.cos(ang), dy = -Math.sin(ang),
        i0 = Math.max(0, Math.floor((ax - len) / cw)), i1 = Math.min(cols - 1, Math.ceil((ax + len) / cw)),
        j0 = Math.max(0, Math.floor((ay - len) / lh)), j1 = Math.min(rows - 1, Math.ceil((ay + len) / lh));
  for (let j = j0; j <= j1; j++) {
    const y = (j + .5) * lh;
    for (let i = i0; i <= i1; i++) {
      const x = (i + .5) * cw, px = x - ax, py = y - ay, u = px * dx + py * dy; if (u <= 0 || u >= len) continue;
      const sn = Math.sin(Math.PI * u / len), half = wid * Math.pow(sn, .8), v = -px * dy + py * dx, av = Math.abs(v);
      if (av >= half) continue;
      const idx = j * stride + i, dh = wid * .8 * Math.pow(sn, -.2) * Math.cos(Math.PI * u / len) * Math.PI / len,
            sv = v < 0 ? -1 : 1, gx = -sv * dy - dh * dx, gy = sv * dx - dh * dy, gm = Math.hypot(gx, gy) || 1;
      G.x = gx / gm; G.y = gy / gm;
      if ((half - av) / gm < band(G)) put(idx, 1, dirc(G.x, G.y, true), 2);
      else if (av < cw * .5) put(idx, 1, dirc(-dy, dx, false), 3);
      else put(idx, 0, ramp(.3 + .4 * (1 - av / half)), 4);
    }
  }
}

function drawFlower(t, tb) {
  const top = topPad, base = P.t + 2, avail = base - top;
  if (avail < 90) return;
  const R = Math.min(.3 * Math.min(innerWidth, 560), avail / 3.6), vital = online ? 1 : .5, TILT = .92;
  const hx = P.cx + Math.sin(t * .7) * .045 * R + Math.sin(t * 1.9) * .012 * R, hyFull = top + R * 1.12;
  const fl = tb > .38 && tb < 3 ? Math.exp(-(tb - .38) * 3.2) : 0;   // flare once the stem pulse lands
  const B = bloom * (1 + .08 * fl + .04 * charge), rot = t * .06 + charge * .2, rd = R * .2 * (.55 + .45 * Math.min(1, bloom));

  // sprout: the stem grows from the composer and the head rides its tip up to
  // the final height while it opens (the original left the bud floating).
  const sLenFull = base - (hyFull + rd * .5), yTip = base - sLenFull * Math.min(1, bloom * 1.7);
  const hy = Math.max(hyFull, yTip - rd * .5);
  // stem: a pulse rides it on send
  const yTop = hy + rd * .5, sLen = base - yTop, yGrow = yTop,
        bend = .09 * R * Math.sin(t * .45 + 1.3), sp = tb >= 0 && tb < .6 ? tb / .42 : -1;
  const xs = s => P.cx + (hx - P.cx) * Math.pow(s, 1.5) + bend * Math.sin(Math.PI * s);
  if (sLen > R * .7) {
    const lg = Math.min(1, Math.max(0, (bloom - .25) * 1.6));
    leaf(xs(.3), base - sLen * .3, 2.55 + .06 * Math.sin(t * .8), R * .5 * lg, R * .14);
    leaf(xs(.52), base - sLen * .52, .62 + .06 * Math.sin(t * .8 + 2), R * .46 * lg, R * .13);
  }
  if (sLen > 1) for (let j = Math.max(0, Math.floor(yGrow / lh)); j <= Math.min(rows - 1, Math.floor(base / lh)); j++) {
    const y = (j + .5) * lh; if (y < yGrow || y > base) continue;
    const s = (base - y) / sLen, x = xs(s), i = Math.floor(x / cw); if (i < 0 || i >= cols) continue;
    const tx = xs(Math.min(1, s + .02)) - x, ty = -.02 * sLen, idx = j * stride + i;
    if (sp >= 0 && Math.abs(s - sp) < .08) put(idx, 2, HASH, 0); else put(idx, 1, dirc(-ty, tx, false), s > .5 ? 1 : 2);
  }

  // head: disc + front petals + offset back petals, outlined with directional glyphs
  const Ff = (x, y) => { const dx = x - hx, dy = (y - hy) / TILT; return Math.hypot(dx, dy) - R * B * petalR(Math.atan2(dy, dx) - rot, NF, LF, t, .025); };
  const Fb = (x, y) => { const dx = x - hx, dy = (y - hy) / TILT; return Math.hypot(dx, dy) - R * B * 1.12 * petalR(Math.atan2(dy, dx) - rot - Math.PI / NF, NF, LB, t, .02); };
  const Rmax = fl > 0 ? R * 3 : R * 1.25 * B + 2,
        i0 = Math.max(0, Math.floor((hx - Rmax) / cw)), i1 = Math.min(cols - 1, Math.ceil((hx + Rmax) / cw)),
        j0 = Math.max(0, Math.floor((hy - Rmax) / lh)), j1 = Math.min(rows - 1, Math.ceil((hy + Rmax) / lh));
  for (let j = j0; j <= j1; j++) {
    const y = (j + .5) * lh; if (y > P.t - 2) break;
    for (let i = i0; i <= i1; i++) {
      const x = (i + .5) * cw, dx = x - hx, dy = (y - hy) / TILT, r = Math.hypot(dx, dy); if (r > Rmax) continue;
      const idx = j * stride + i, th = Math.atan2(dy, dx);
      if (r < rd) { const u = r / rd; put(idx, u < .8 ? 2 : 1, u < .42 ? AT : u < .8 ? OO : STAR, u < .8 ? 0 : 1); continue; }
      const rf = R * B * petalR(th - rot, NF, LF, t, .025), q = PQ;
      if (r < rf) {
        const e = rf - r;
        if (e < lh * 1.1) { const g = grad(Ff, x, y); if (e / g.m < band(g)) { put(idx, 2, dirc(g.x, g.y, true), 1); continue; } }
        const tt = r / rf, aq = Math.abs(q);
        if (aq < .1 && tt > .28 && tt < .88) { put(idx, 0, dirc(-dy, dx, false), 3); continue; }   // vein
        put(idx, 1, ramp((.08 + .3 * tt + .12 * (1 - aq)) * vital + .28 * fl + .07 * charge), Math.max(0, (tt < .4 ? 1 : tt < .7 ? 2 : 3) - (fl > .55 ? 1 : 0)));
        continue;
      }
      const rb = R * B * 1.12 * petalR(th - rot - Math.PI / NF, NF, LB, t, .02);
      if (r < rb) {
        const e = rb - r;
        if (e < lh * 1.1) { const g = grad(Fb, x, y); if (e / g.m < band(g)) { put(idx, 1, dirc(g.x, g.y, true), 3); continue; } }
        put(idx, 0, ramp((.1 + .3 * r / rb) * vital), 4); continue;
      }
      if (fl > 0) { // pollen ring blasting outward after a send
        const ringR = R * (1.08 + (tb - .38) * 1.5);
        if (Math.abs(r - ringR) < lh * .9 && hash(i, j + (tb * 12 | 0)) < .5 * fl) put(idx, 1, hash(i + 5, j) > .6 ? STAR : PLUS, fl > .5 ? 2 : 3);
      }
    }
  }
}

function frame(now) {
  requestAnimationFrame(frame);
  const t = now / 1000;
  const active = charge > .04 || t - burstAt < 2.2 || Math.abs((online ? 1 : .42) - bloom) > .01;
  if (paused || document.hidden || !P || now - lastF < (RM.matches ? 400 : active ? 40 : 80)) return;
  const dt = Math.min(.1, (now - lastF) / 1000); lastF = now;
  bloom += ((online ? 1 : .42) - bloom) * (1 - Math.exp(-dt * 1.6));
  charge *= Math.exp(-dt * .9);
  const tb = t - burstAt;
  off = online ? 0 : 1;
  for (const b of bufs) b.set(tmpl);
  drawFlower(t, tb);
  for (let k = 0; k < NT; k++) LY[k].textContent = dec.decode(bufs[k]);
}

export const Flower = {
  /** init({field, anchor, top}) — field holds three <pre>, anchor is the composer row */
  init(o) {
    field = o.field; anchor = o.anchor; topPad = o.top || 64;
    LY = Array.from({ length: NT }, (_, i) => field.querySelector('#f' + i));
    grid();
    addEventListener('resize', grid);
    addEventListener('load', measure);
    if (window.ResizeObserver) new ResizeObserver(measure).observe(anchor);
    requestAnimationFrame(frame);
  },
  setOnline(v) { online = !!v; },
  poke(a) { charge = Math.min(1, charge + (a == null ? .22 : a)); },
  burst() { burstAt = performance.now() / 1000; },
  pause(v) { paused = !!v; },
  /** replay the sprout from a bud (called as the loading screen dissolves) */
  restart() { bloom = 0; charge = 0; burstAt = -99; lastF = 0; },
  measure,
  get bloom() { return bloom; },
};
