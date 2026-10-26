// pose.mjs — in-browser hand-pose engine (MediaPipe, vendored, offline).
// Same engine the phone app runs (web/js/pose.js), minus the dev replay seam:
//   Pose.load(onProgress?) -> engine
//   engine.detectFrame(video, t_ms) -> {points:[{x,y,z,score} x42], hands:2}
//   Pose.openCamera(video) -> {stream, front}
// Slots: 0-20 left hand, 21-41 right (the kp42 wire contract). An absent hand
// is 21 zero-score points — a real frame, the segmenter needs it.

const DIR = '/vendor/mediapipe/';
const ENTRY = 'vision_bundle.mjs';
const MODEL = 'models/hand_landmarker.task';
// bundle + both wasm builds + float16 model (see vendor MANIFEST)
const SIZE_BYTES = 156139 + 334128 + 13519922 + 333931 + 12627509 + 7819105;

async function blobModule(resp) {
  const src = await resp.text();
  const u = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
  try { return await import(u); } finally { URL.revokeObjectURL(u); }
}

const ZP = () => ({ x: 0, y: 0, z: 0, score: 0 });

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

export const Pose = {
  state: 'idle',
  error: null,
  SIZE_BYTES,

  load(onProgress) {
    if (loading) return loading;
    Pose.state = 'loading';
    Pose.error = null;
    loading = (async () => {
      const prog = (phase, detail) => { try { onProgress && onProgress({ phase, detail }); } catch (e) {} };

      prog('fetch', 'checking the model files');
      const probe = async (u) => {
        const c = new AbortController();
        try { const r = await fetch(u, { cache: 'no-store', signal: c.signal }); c.abort(); return r; }
        catch (e) { return null; }
      };
      const [bun, mod] = await Promise.all([
        fetch(DIR + ENTRY, { cache: 'no-store' }),
        probe(DIR + MODEL),
      ]);
      if (!bun.ok) throw new Error('mediapipe bundle missing');
      if (mod && !mod.ok) throw new Error('hand_landmarker.task missing');

      prog('wasm', 'loading the pose runtime');
      const m = await blobModule(bun);
      const R = m.FilesetResolver, L = m.HandLandmarker;
      if (!R || !L) throw new Error('vendored bundle exports missing');
      const fs = await R.forVisionTasks(DIR + 'wasm');

      prog('model', 'loading the hand model');
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
