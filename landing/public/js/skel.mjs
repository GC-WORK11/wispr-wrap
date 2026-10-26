// skel.mjs — kp42 keypoints -> canvas drawing (web/js/skeleton.js, minus the
// app shell import). WYSIWYS: what draws is what the matcher scores.
export const CONN = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [17, 18], [18, 19], [19, 20], [0, 17],
];

function fit(canvas) {
  const dpr = Math.min(2, devicePixelRatio || 1);
  const W = canvas.clientWidth || 0, H = canvas.clientHeight || 0;
  if (!W || !H) return null;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  }
  const x = canvas.getContext('2d');
  if (!x) return null;
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { x, W, H };
}

export const Skel = {
  CONN,

  present(pts, off, minScore = 0.28) {
    let n = 0;
    for (let i = 0; i < 21; i++) {
      const p = pts[off + i];
      if (p && p.score > minScore) n++;
    }
    return n;
  },

  draw(canvas, pts, opts) {
    const f = fit(canvas);
    if (!f || !pts) return;
    const { x, W, H } = f;
    const o = opts || {};
    const minS = o.minScore == null ? 0.28 : o.minScore;
    const accent = o.accent || 'rgba(255, 255, 255, .95)';
    const dim = o.dim || 'rgba(255, 255, 255, .62)';
    const mirror = !!o.mirror;
    x.clearRect(0, 0, W, H);
    let px = p => (mirror ? 1 - p.x : p.x) * W, py = p => p.y * H;
    const v = o.video;
    if (v && v.videoWidth && v.videoHeight) {
      const vw = v.videoWidth, vh = v.videoHeight;
      const s = Math.max(W / vw, H / vh), w = vw * s, h = vh * s;
      const ox = (W - w) / 2, oy = (H - h) / 2;
      px = p => ox + (mirror ? 1 - p.x : p.x) * w;
      py = p => oy + p.y * h;
    }
    for (const off of [0, 21]) {
      if (Skel.present(pts, off, minS) < 4) continue;
      const col = off === 0 ? accent : dim;
      x.lineWidth = 2.4; x.lineCap = 'round'; x.strokeStyle = col;
      x.shadowColor = 'rgba(0, 0, 0, .38)'; x.shadowBlur = 5;
      x.beginPath();
      for (const [a, b] of CONN) {
        const A = pts[off + a], B = pts[off + b];
        if (!A || !B || A.score <= minS || B.score <= minS) continue;
        x.moveTo(px(A), py(A)); x.lineTo(px(B), py(B));
      }
      x.stroke();
      x.fillStyle = col;
      for (let i = 0; i < 21; i++) {
        const p = pts[off + i];
        if (!p || p.score <= minS) continue;
        x.beginPath(); x.arc(px(p), py(p), i === 0 ? 3.6 : 2.4, 0, 6.2832); x.fill();
      }
      x.shadowBlur = 0;
    }
  },
};
