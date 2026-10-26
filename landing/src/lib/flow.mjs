// Geometry for the "one line" board. One table per layout gives each node a
// box in board units; the line is a Catmull-Rom spline through the box
// centres, and each node sprouts when the line reaches it (its arc length
// fraction). Same numbers drive the CSS, the SVG and the timing.

const fmt = (n) => +n.toFixed(1);

/** Smooth path through points (Catmull-Rom, tension 0.5) as cubic Béziers. */
export function spline(pts) {
  let d = `M${fmt(pts[0][0])} ${fmt(pts[0][1])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${fmt(c1[0])} ${fmt(c1[1])} ${fmt(c2[0])} ${fmt(c2[1])} ${fmt(p2[0])} ${fmt(p2[1])}`;
  }
  return d;
}

/** Arc length fraction at each interior point of the spline, by sampling. */
function fractions(pts) {
  const seg = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    let len = 0;
    let prev = p1;
    for (let k = 1; k <= 40; k++) {
      const t = k / 40;
      const u = 1 - t;
      const p = [0, 1].map((j) => u * u * u * p1[j] + 3 * u * u * t * c1[j] + 3 * u * t * t * c2[j] + t * t * t * p2[j]);
      len += Math.hypot(p[0] - prev[0], p[1] - prev[1]);
      prev = p;
    }
    seg.push(len);
  }
  const total = seg.reduce((a, b) => a + b, 0);
  const out = [];
  let acc = 0;
  for (let i = 0; i < seg.length - 1; i++) {
    acc += seg[i];
    out.push(acc / total);
  }
  return out;
}

/**
 * @param {{ W: number, H: number, start: number[], end: number[], boxes: {id,x,y,w,ratio}[] }} L
 */
export function layout(L) {
  const centres = L.boxes.map((b) => [b.x + b.w / 2, b.y + b.w / b.ratio / 2]);
  const pts = [L.start, ...centres, L.end];
  const t = fractions(pts);
  return {
    W: L.W,
    H: L.H,
    d: spline(pts),
    nodes: L.boxes.map((b, i) => ({
      id: b.id,
      left: `${fmt((b.x / L.W) * 100)}%`,
      top: `${fmt((b.y / L.H) * 100)}%`,
      width: `${fmt((b.w / L.W) * 100)}%`,
      t: +t[i].toFixed(3),
    })),
  };
}
