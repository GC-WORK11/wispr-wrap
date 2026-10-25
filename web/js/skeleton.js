// skeleton.js — keypoint -> canvas drawing. Owned by the LIVE lane (P2).
//
// INTERFACE (frozen — live.js and teach.js build against this):
//   import { Skel } from './skeleton.js'
//   Skel.CONN — MediaPipe Hands 21-pt bone topology (frozen array of [a,b] pairs).
//   Skel.draw(canvas, points, opts?)
//     - points: kp42 array [{x,y,z,score}] — the SAME frame sent to the daemon.
//     - draws bones + joints inside `canvas` sized to its client box.
//     - opts: {mirror:true, color:{hand:'#4a7dff',joint:'#0f172a'}, lineWidth,
//              minScore:0.5, alpha}
//   Skel.drawTrail(canvas, trail, opts?)
//     - trail: array of kp42 frames (recorded take) — draws a motion-trail
//       replay (teach preview + bank review), newest frame brightest.
import { App } from './app.js';

export const Skel = {
  CONN: [],
  draw(canvas, points, opts) {},
  drawTrail(canvas, trail, opts) {},
};
