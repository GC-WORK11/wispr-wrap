// pose.js — in-browser hand-pose engine. Owned by the pose lane (P3).
//
// INTERFACE (frozen — live.js and teach.js build against this):
//   import { Pose } from './pose.js'
//   Pose.load(onProgress?) -> Promise<engine>
//     - lazy-loads vendored MediaPipe (web/vendor/mediapipe/*), idempotent.
//     - onProgress({phase:'wasm'|'model', loaded:bool}) for UI progress.
//   engine.detectFrame(source, t_ms) -> result | null
//     - source: HTMLVideoElement (preferred) or ImageBitmap.
//     - result = { points:[{x,y,z,score} x42], hands:2, order:["Right","Left"] }
//       slots: 0 = anatomical RIGHT hand landmarks 0-20 (MediaPipe order),
//              1 = LEFT 21-41. Absent hand => its 21 points {x:0,y:0,z:0,score:0}.
//       x,y normalized [0,1] image coords, NOT mirrored.
//     - returns null when nothing detected this frame.
//   engine.close() — release model resources.
//   Pose.state: 'idle'|'loading'|'ready'|'error' ; Pose.error: string|null
//   Pose.SIZE_BYTES: number — vendored payload size for honest UI display.
import { App } from './app.js';

export const Pose = {
  state: 'idle',
  error: null,
  SIZE_BYTES: 0,
  async load(onProgress) {
    throw new Error('pose lane not implemented yet');
  },
};
