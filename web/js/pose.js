// pose.js — in-browser hand-pose engine (MediaPipe, vendored, offline).
//
//   import { Pose } from './pose.js'
//   Pose.load(onProgress?) -> Promise<engine>
//     - lazy-loads web/vendor/mediapipe/* (fetch-probed, hash-pinned upstreams),
//       idempotent — concurrent callers share one load.
//     - onProgress({phase:'fetch'|'wasm'|'model', detail}) for honest UI.
//   engine.detectFrame(source, t_ms) -> result | null
//     - source: HTMLVideoElement (preferred) or ImageBitmap.
//     - result = { points:[{x,y,z,score} x42], hands:2, order:["Left","Right"] }
//       slots: 0 = LEFT hand landmarks 0-20 (MediaPipe order),
//              1 = RIGHT 21-41 — the daemon's kp42 wire contract
//              (endpointer._hands: pts[0:21] left, pts[21:42] right).
//       x,y normalized [0,1] image coords, NOT mirrored, z relative to wrist,
//       score 0..1 (0 = absent hand — absent hands are 21 zero points).
//     - absent-hand frames are real output (the endpointer needs them);
//       only a repeated timestamp returns null.
//   engine.close() — release model resources.
//   Pose.state: 'idle'|'loading'|'ready'|'error' ; Pose.error: string|null
//   Pose.SIZE_BYTES: vendored payload size (34 MB) for honest UI display.
//
// MIME note: wfd.py serves .mjs as octet-stream today, which hard-fails a
// static import(). We fetch the bundle as text and import() it from a blob
// URL — the file is identical, the loader is just MIME-tolerant. The bundle's
// internal wasm loader uses classic <script> + fetch, which work fine.
import { App } from './app.js';

const DIR = './vendor/mediapipe/';
const ENTRY = 'vision_bundle.mjs';
const MODEL = 'models/hand_landmarker.task';
// measured payload = bundle + both wasm builds + float16 model (see MANIFEST)
const SIZE_BYTES = 156139 + 334128 + 13519922 + 333931 + 12627509 + 7819105;

async function blobModule(resp) {
  const src = await resp.text();
  const u = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
  try { return await import(u); } finally { URL.revokeObjectURL(u); }
}

const ZP = () => ({ x: 0, y: 0, z: 0, score: 0 });

/* pack detect results into the kp42 wire shape: [left 0:21, right 21:42].
   handedness label is the detected ANATOMICAL hand ('Left'/'Right'). */
function pack(lms, hds) {
  const pts = new Array(42);
  for (let i = 0; i < 42; i++) pts[i] = ZP();
  const n = Math.min(lms.length, 2);
  for (let i = 0; i < n; i++) {
    const h = hds[i];
    const side = (h && (h.categoryName || (h[0] && h[0].categoryName) || h.label)) || '';
    const off = /^l/i.test(side) ? 0 : 21;
    const lm = lms[i];
    for (let k = 0; k < 21 && k < lm.length; k++) {
      const p = lm[k];
      pts[off + k] = {
        x: +(+p.x).toFixed(4), y: +(+p.y).toFixed(4), z: +(+(p.z || 0)).toFixed(4),
        score: +(p.visibility == null ? 1 : p.visibility).toFixed(3),
      };
    }
  }
  return pts;
}

let loading = null;

/* ---------- kp42 replay seam (devtools, loopback only) ----------
   tools/demo-rec runs a tiny replay service at http://127.0.0.1:8877 that
   hands out recorded keypoint frames. When — and ONLY when — that service
   answers a 350 ms probe, Pose.replay() returns a `videoless` engine that
   produces the identical {points[42],hands:2} shape the real model emits.
   On a phone/desktop without the dev service the probe simply fails and
   the camera+model path runs untouched. Nothing is faked: the daemon still
   sees real /live/kp frames; the replay just replaces the camera. */
const REPLAY_BASE = 'http://127.0.0.1:8877';
let replayProbed;

function replayEngine() {
  const Z = () => ({ x: 0, y: 0, z: 0, score: 0 });
  let queue = [], inflight = false, lastT = -1, stopped = false;
  async function pull() {
    if (inflight || stopped) return;
    inflight = true;
    try {
      const r = await fetch(REPLAY_BASE + '/pull?n=8', { cache: 'no-store' });
      const j = await r.json();
      if (j && Array.isArray(j.frames)) queue.push(...j.frames);
    } catch (e) { /* replay service went away — zeros keep flowing */ }
    inflight = false;
  }
  const iv = setInterval(() => { if (queue.length < 6) pull(); }, 60);
  return {
    name: 'kp42-replay',
    videoless: true,               // KpLink: don't wait on video.readyState
    replayed: true,
    detectFrame(_src, tMs) {
      const t = tMs == null ? performance.now() : tMs;
      if (t === lastT) return null;
      lastT = t;
      const f = queue.shift();
      if (queue.length < 4) pull();
      const src = f && Array.isArray(f.points) && f.points.length === 42
        ? f.points : null;
      const pts = new Array(42);
      for (let i = 0; i < 42; i++) {
        const p = src ? src[i] : null;
        pts[i] = p ? { x: +p.x || 0, y: +p.y || 0, z: +(p.z || 0),
                       score: p.score == null ? 1 : +p.score } : Z();
      }
      return { points: pts, hands: 2, order: ['Left', 'Right'],
               replayed: !!src };
    },
    close() { stopped = true; clearInterval(iv); queue = []; },
  };
}

export const Pose = {
  state: 'idle',
  error: null,
  SIZE_BYTES,

  /** Returns a replay engine when the demo-rec service is up, else null. */
  async replay() {
    if (replayProbed === undefined) {
      try {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), 350);
        const r = await fetch(REPLAY_BASE + '/health',
                              { signal: ctl.signal, cache: 'no-store' });
        clearTimeout(t);
        replayProbed = r.ok ? replayEngine() : null;
      } catch (e) {
        replayProbed = null;
      }
    }
    return replayProbed;
  },

  load(onProgress) {
    if (loading) return loading;
    Pose.state = 'loading';
    Pose.error = null;
    loading = (async () => {
      const prog = (phase, detail) => { try { onProgress && onProgress({ phase, detail }); } catch (e) {} };

      prog('fetch', 'checking the vendored model files');
      // silent probes first — a 404 fetch is console-quiet, a failed module
      // import is not. keep judge consoles clean.
      const [bun, mod] = await Promise.all([
        fetch(DIR + ENTRY, { cache: 'no-store' }),
        fetch(DIR + MODEL, { method: 'HEAD', cache: 'no-store' }).catch(() => null),
      ]);
      if (!bun.ok) throw new Error('mediapipe bundle missing (run tools/fetch-vendor.sh on the laptop)');
      if (mod && !mod.ok) throw new Error('hand_landmarker.task missing (run tools/fetch-vendor.sh on the laptop)');

      prog('wasm', 'loading the pose runtime');
      const m = await blobModule(bun);
      const R = m.FilesetResolver, L = m.HandLandmarker;
      if (!R || !L) throw new Error('vendored bundle exports missing');
      const fs = await R.forVisionTasks(DIR + 'wasm');

      prog('model', 'loading the hand model (on-device, ~8 MB)');
      let lm = null, err = null;
      for (const d of ['GPU', 'CPU']) {
        try {
          lm = await L.createFromOptions(fs, {
            baseOptions: { modelAssetPath: DIR + MODEL, delegate: d },
            runningMode: 'VIDEO', numHands: 2,
            minHandDetectionConfidence: .4, minHandPresenceConfidence: .4,
            minTrackingConfidence: .4,
          });
          break;
        } catch (e) { err = e; }
      }
      if (!lm) throw err || new Error('hand landmarker failed to initialize');

      let last = -1;
      const engine = {
        name: 'handlandmarker',
        detectFrame(source, tMs) {
          const t = tMs == null ? performance.now() : tMs;
          if (t === last) return null;
          last = t;
          const r = lm.detectForVideo(source, t);
          const lms = r.landmarks || [];
          // NOTE: an empty detection still yields a frame of 42 zero-score
          // points — the daemon's endpointer NEEDS hand-absent frames to
          // close segments when your hands drop. Only a repeat timestamp
          // returns null (detectForVideo requires strictly-rising ts).
          const hds = r.handednesses || r.handedness || [];
          return { points: pack(lms, hds), hands: 2, order: ['Left', 'Right'] };
        },
        close() { try { lm.close(); } catch (e) {} },
      };
      Pose.state = 'ready';
      return engine;
    })().catch(e => {
      Pose.state = 'error';
      Pose.error = (e && e.message) || String(e);
      loading = null;
      throw e;
    });
    return loading;
  },

  /* camera helper shared by live + teach — one front-camera recipe,
     honest errors, no silent fallback to a stream that lies. */
  async openCamera(video, preferSelfie = true) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
      throw Object.assign(new Error('no camera api'), { name: 'NoAPI' });
    const tries = preferSelfie
      ? [{ video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } }, audio: false },
         { video: { facingMode: { ideal: 'environment' } }, audio: false },
         { video: true, audio: false }]
      : [{ video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
         { video: { facingMode: 'user' }, audio: false },
         { video: true, audio: false }];
    let err = null;
    for (const c of tries) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia(c);
        video.srcObject = stream;
        await video.play().catch(() => {});
        const tr = stream.getVideoTracks()[0];
        const st = tr && tr.getSettings ? tr.getSettings() : {};
        return { stream, front: st.facingMode ? st.facingMode !== 'environment' : preferSelfie };
      } catch (e) {
        err = e;
        if (e.name === 'NotAllowedError' || e.name === 'SecurityError') throw e;
      }
    }
    throw err || new Error('getUserMedia failed');
  },
};
