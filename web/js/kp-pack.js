// kp-pack.js — packs HandLandmarker results into the kp42 wire shape.
//
// Slots: [left 0:21, right 21:42]. The handedness label is the detected
// anatomical hand ('Left'/'Right').
//
// Point score: HandLandmarker does not produce per-landmark visibility.
// The tasks-vision bundle fills `visibility` with 0 for every hand landmark,
// so it must not be used as a confidence. A landmark that belongs to a
// detected hand gets its `presence` when that is a positive number, and 1.0
// otherwise. Absent hands stay 21 zero points with score 0.

export const ZP = () => ({ x: 0, y: 0, z: 0, score: 0 });

function pointScore(p) {
  const pr = p && p.presence;
  if (typeof pr === 'number' && Number.isFinite(pr) && pr > 0) return Math.min(1, pr);
  return 1;
}

export function pack(lms, hds) {
  const pts = new Array(42);
  for (let i = 0; i < 42; i++) pts[i] = ZP();
  const n = Math.min((lms && lms.length) || 0, 2);
  for (let i = 0; i < n; i++) {
    const h = hds && hds[i];
    const side = (h && (h.categoryName || (h[0] && h[0].categoryName) || h.label)) || '';
    const off = /^l/i.test(side) ? 0 : 21;
    const lm = lms[i] || [];
    for (let k = 0; k < 21 && k < lm.length; k++) {
      const p = lm[k];
      pts[off + k] = {
        x: +(+p.x).toFixed(4), y: +(+p.y).toFixed(4), z: +(+(p.z || 0)).toFixed(4),
        score: +pointScore(p).toFixed(3),
      };
    }
  }
  return pts;
}
