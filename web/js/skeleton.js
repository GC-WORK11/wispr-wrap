// skeleton.js — keypoint -> canvas drawing for the kp42 wire format.
//
//   import { Skel } from './skeleton.js'
//   Skel.CONN — MediaPipe Hands 21-pt bone topology ([a,b] index pairs).
//   Skel.draw(canvas, points, opts?)
//     - points: kp42 [{x,y,z,score}] — the SAME frame sent to the daemon
//       (WYSIWYS: what you see is what the matcher scores).
//     - opts: {video?: HTMLVideoElement,   // enables object-fit:cover mapping
//              mirror?: bool,              // selfie preview flips x
//              accent, dim, jointR, minScore}
//   Skel.drawTrail(canvas, frames, opts?)
//     - frames: [{t_ms, points}] take recording — draws the wrist paths plus
//       the mid-frame hand shape (teach preview / bank review).
import { App } from './app.js';

const CONN = [
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
    // object-fit:cover mapping so points land exactly on the video image;
    // without a video element, normalized coords map onto the whole canvas.
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

  /* take preview: wrist trajectory per hand + mid-frame shape, like the
     daemon's fused traj+shape evidence — what you preview is what it sees. */
  drawTrail(canvas, frames, opts) {
    const o = opts || {};
    const S = o.size || 92;
    const dpr = Math.min(2, devicePixelRatio || 1);
    canvas.width = Math.round(S * dpr); canvas.height = Math.round(S * dpr);
    const x = canvas.getContext('2d');
    if (!x) return;
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    x.fillStyle = o.bg || '#f4f1ea';
    x.fillRect(0, 0, S, S);
    const fr = (frames || []).map(f => f.points || f);
    if (!fr.length) return;
    const minS = o.minScore == null ? 0.28 : o.minScore;
    for (const off of [0, 21]) {
      const path = fr.map(p => p[off]).filter(p => p && p.score > minS);
      if (path.length < 2) continue;
      const col = off ? 'rgba(108,103,94,.75)' : 'rgba(21,73,63,.92)';
      x.strokeStyle = col; x.lineWidth = 1.8; x.lineCap = 'round';
      x.beginPath();
      path.forEach((p, i) => i ? x.lineTo(p.x * S, p.y * S) : x.moveTo(p.x * S, p.y * S));
      x.stroke();
      x.fillStyle = col;
      x.beginPath(); x.arc(path[0].x * S, path[0].y * S, 3, 0, 7); x.fill();
      const e = path[path.length - 1];
      x.beginPath(); x.arc(e.x * S, e.y * S, 3.4, 0, 7); x.stroke();
    }
    const mid = fr[fr.length >> 1];
    if (mid) {
      x.save(); x.scale(.62, .62); x.translate(S * .31, S * .31);
      x.strokeStyle = 'rgba(28,27,24,.4)'; x.lineWidth = 1.4;
      for (const off of [0, 21]) {
        if (Skel.present(mid, off, minS) < 4) continue;
        x.beginPath();
        for (const [a, b] of CONN) {
          const p = mid[off + a], q = mid[off + b];
          if (p && q && p.score > minS && q.score > minS) {
            x.moveTo(p.x * S, p.y * S); x.lineTo(q.x * S, q.y * S);
          }
        }
        x.stroke();
      }
      x.restore();
    }
  },
};
