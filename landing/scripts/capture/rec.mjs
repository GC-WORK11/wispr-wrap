// rec.mjs - page recorder for the landing captures: CDP screencast -> H.264 mp4 + poster WebP.
// Frames are timed in real time; idle gaps are capped, skipped stretches are cut, and the result is
// sped up uniformly only when it would run longer than maxSec.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { sleep } from '../../../tools/site/lib.mjs';

export function recorder(page, dir) {
  fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
  const frames = []; let n = 0, skipping = false, cutNext = false;
  page.on('Page.screencastFrame', async (p) => {
    try {
      await page.send('Page.screencastFrameAck', { sessionId: p.sessionId });
      if (skipping) return;
      const f = path.join(dir, 'f' + String(n++).padStart(6, '0') + '.jpg');
      fs.writeFileSync(f, Buffer.from(p.data, 'base64'));
      frames.push({ f, t: Date.now(), cut: cutNext }); cutNext = false;
    } catch (e) { /* page closing */ }
  });
  const run = (args) => spawnSync('ffmpeg', ['-y', '-loglevel', 'error', ...args], { encoding: 'utf8' });
  return {
    start: () => page.send('Page.startScreencast', { format: 'jpeg', quality: 85, everyNthFrame: 1, maxWidth: 780, maxHeight: 1688 }),
    // drop everything between skip(true) and skip(false) (dead waiting time)
    skip(on) { skipping = on; if (!on) cutNext = true; },
    async stop(mp4, { width = 450, fps = 24, gapCap = 0.9, maxSec = 19, maxBytes = 1.3e6 } = {}) {
      try { await page.send('Page.stopScreencast'); } catch (e) { /* gone */ }
      await sleep(300);
      if (frames.length < 2) return { ok: false, frames: frames.length };
      const raw = frames.map((fr, i) => i + 1 < frames.length
        ? (fr.cut || frames[i + 1].cut ? Math.min(0.25, (frames[i + 1].t - fr.t) / 1000) : Math.min(gapCap, (frames[i + 1].t - fr.t) / 1000))
        : 1.0);
      const total = raw.reduce((a, b) => a + b, 0);
      const k = Math.max(1, total / maxSec), durs = raw.map((d) => Math.max(0.001, d / k));
      const lines = [];
      frames.forEach((fr, i) => lines.push(`file '${fr.f}'`, `duration ${durs[i].toFixed(4)}`));
      lines.push(`file '${frames[frames.length - 1].f}'`);
      const list = path.join(dir, 'list.txt'); fs.writeFileSync(list, lines.join('\n'));
      let crf = 27, size = 0;
      for (; crf <= 35; crf += 2) {
        const r = run(['-f', 'concat', '-safe', '0', '-i', list, '-an', '-vf', `scale=${width}:-2:flags=lanczos,fps=${fps},format=yuv420p`,
          '-c:v', 'libx264', '-preset', 'slow', '-crf', String(crf), '-movflags', '+faststart', mp4]);
        if (r.status !== 0) return { ok: false, err: r.stderr };
        size = fs.statSync(mp4).size; if (size <= maxBytes) break;
      }
      // poster: the source frame at the middle of the timeline
      let acc = 0, mid = frames[0];
      for (let i = 0; i < frames.length; i++) { acc += durs[i]; if (acc >= (total / k) / 2) { mid = frames[i]; break; } }
      const poster = mp4.replace(/\.mp4$/, '-poster.webp');
      run(['-i', mid.f, '-vf', `scale=${width}:-2:flags=lanczos`, '-c:v', 'libwebp', '-quality', '85', poster]);
      const out = { ok: true, frames: frames.length, seconds: +(Math.max(...[durs.reduce((a, b) => a + b, 0)])).toFixed(1), size, crf, speedup: +k.toFixed(2), poster };
      fs.rmSync(dir, { recursive: true, force: true });
      return out;
    },
  };
}
