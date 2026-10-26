// test-hand.mjs — node unit tests (no deps). Exits non-zero on any failure.
import { classify, recognize, createHand } from '../../web/js/hand.js';
import {
  handPose, airC, airV, vSign, palm, idle, slide, hover, dwellAt, pinchAt, toKp42,
} from './handsynth.mjs';

const results = [];
function check(name, cond, info = '') {
  results.push({ name, pass: !!cond, info });
}

// --------------------------------------------------------------------------
// classify() on each synthetic pose
// --------------------------------------------------------------------------
{
  const w = { x: 0.5, y: 0.75 };
  const point = handPose('point', w, { tip: { x: 0.5, y: 0.4 } });
  const v = handPose('v', w);
  const pm = handPose('palm', w);
  const fist = handPose('fist', w);
  const pinch = handPose('pinch', w, { tip: { x: 0.5, y: 0.4 } });

  check('classify point', classify(point).pose === 'point', classify(point).pose);
  check('classify v', classify(v).pose === 'v', classify(v).pose);
  check('classify palm', classify(pm).pose === 'palm', classify(pm).pose);
  check('classify fist', classify(fist).pose === 'fist', classify(fist).pose);
  check('classify pinch', classify(pinch).pose === 'pinch', classify(pinch).pose + ' pinch=' + classify(pinch).pinch.toFixed(2));
}

// --------------------------------------------------------------------------
// recognize(): accept airC as C, airV as V; reject line / circle
// --------------------------------------------------------------------------
function strokeOf(clip) {
  return clip.filter(f => f.lm).map(f => ({ x: f.lm[8].x, y: f.lm[8].y }));
}
{
  const cPts = strokeOf(airC({ cx: 0.5, cy: 0.5, r: 0.18 }));
  const vPts = strokeOf(airV({ x: 0.4, y: 0.35, w: 0.18, h: 0.22 }));
  const rc = recognize(cPts);
  const rv = recognize(vPts);
  check('recognize airC -> C >=0.8', rc.letter === 'C' && rc.score >= 0.8, JSON.stringify(rc));
  check('recognize airV -> V >=0.8', rv.letter === 'V' && rv.score >= 0.8, JSON.stringify(rv));

  // straight vertical line
  const vert = [];
  for (let i = 0; i < 40; i++) vert.push({ x: 0.5, y: 0.2 + i * (0.6 / 39) });
  const rVert = recognize(vert);
  check('reject vertical line', rVert.letter === null, JSON.stringify(rVert));

  // horizontal line
  const horiz = [];
  for (let i = 0; i < 40; i++) horiz.push({ x: 0.2 + i * (0.6 / 39), y: 0.5 });
  const rHoriz = recognize(horiz);
  check('reject horizontal line', rHoriz.letter === null, JSON.stringify(rHoriz));

  // closed circle
  const circ = [];
  for (let i = 0; i < 64; i++) {
    const a = (i / 63) * 2 * Math.PI;
    circ.push({ x: 0.5 + 0.18 * Math.cos(a), y: 0.5 + 0.18 * Math.sin(a) });
  }
  const rCirc = recognize(circ);
  check('reject closed circle as C', rCirc.letter !== 'C', JSON.stringify(rCirc));
}

// --------------------------------------------------------------------------
// createHand WRITE: airC -> one letter C; airV -> one letter V
// --------------------------------------------------------------------------
function runClip(engine, clip) {
  const evs = [];
  for (const f of clip) for (const e of engine.update(f.lm, f.t_ms)) evs.push(e);
  return evs;
}
{
  const h = createHand({ mode: 'write' });
  const evs = runClip(h, airC({ cx: 0.5, cy: 0.5, r: 0.18 }));
  const letters = evs.filter(e => e.type === 'letter');
  check('write airC -> exactly one letter', letters.length === 1, 'got ' + letters.length);
  check('write airC letter is C', letters.length === 1 && letters[0].letter === 'C', JSON.stringify(letters[0] || null));
}
{
  const h = createHand({ mode: 'write' });
  const evs = runClip(h, airV({ x: 0.4, y: 0.35, w: 0.18, h: 0.22 }));
  const letters = evs.filter(e => e.type === 'letter');
  check('write airV -> exactly one letter', letters.length === 1, 'got ' + letters.length);
  check('write airV letter is V', letters.length === 1 && letters[0].letter === 'V', JSON.stringify(letters[0] || null));
}

// --------------------------------------------------------------------------
// vsign and palm holds
// --------------------------------------------------------------------------
{
  const h = createHand({ mode: 'write' });
  const evs = runClip(h, vSign(900));
  const vs = evs.filter(e => e.type === 'vsign');
  check('vSign -> exactly one vsign', vs.length === 1, 'got ' + vs.length);
}
{
  const h = createHand({ mode: 'write' });
  const evs = runClip(h, palm(900));
  const ps = evs.filter(e => e.type === 'palm');
  check('palm -> exactly one palm', ps.length === 1, 'got ' + ps.length);
}

// --------------------------------------------------------------------------
// BROWSE scroll: slide up -> positive dy summing ~ travel/0.05, no click
// --------------------------------------------------------------------------
{
  const h = createHand({ mode: 'browse' });
  const y0 = 0.85, y1 = 0.15;
  const evs = runClip(h, slide({ x: 0.5, y0, y1, ms: 500 }));
  const scrolls = evs.filter(e => e.type === 'scroll');
  const clicks = evs.filter(e => e.type === 'click');
  const cursors = evs.filter(e => e.type === 'cursor');
  const sumDy = scrolls.reduce((a, e) => a + e.dy, 0);
  // expected notches derive from the travel the engine actually tracks
  // (smoothed cursor), upward travel -> positive dy. EMA lag + the 110ms
  // scroll throttle make the sum land a notch or two under the ideal count.
  const smoothedTravel = cursors[0].y - cursors[cursors.length - 1].y;
  const expected = Math.round(smoothedTravel / 0.05);
  check('slide up -> scroll events exist', scrolls.length > 0, 'got ' + scrolls.length);
  check('slide up -> all positive dy', scrolls.every(e => e.dy > 0), JSON.stringify(scrolls.map(e => e.dy)));
  check('slide up -> sum ~ travel/0.05', Math.abs(sumDy - expected) <= 3, `sum=${sumDy} expected≈${expected} (smoothed ${smoothedTravel.toFixed(3)})`);
  check('slide up -> no click', clicks.length === 0, 'got ' + clicks.length);
}

// --------------------------------------------------------------------------
// BROWSE dwell: dwellAt -> dwell progress then exactly one click via dwell
// --------------------------------------------------------------------------
{
  const h = createHand({ mode: 'browse' });
  // lead-in move so dwell is armed, then hold still
  const lead = slide({ x: 0.5, y0: 0.55, y1: 0.5, ms: 120 });
  const hold = dwellAt({ x: 0.5, y: 0.5, ms: 1200 });
  const evs = runClip(h, [...lead, ...hold.map(f => ({ ...f, t_ms: f.t_ms + 120 }))]);
  const dwells = evs.filter(e => e.type === 'dwell');
  const clicks = evs.filter(e => e.type === 'click' && e.via === 'dwell');
  check('dwellAt -> dwell progress events', dwells.length > 0, 'got ' + dwells.length);
  check('dwellAt -> exactly one dwell click', clicks.length === 1, 'got ' + clicks.length);
}

// --------------------------------------------------------------------------
// BROWSE pinch: pinchAt -> exactly one click via pinch
// --------------------------------------------------------------------------
{
  const h = createHand({ mode: 'browse' });
  const evs = runClip(h, pinchAt({ x: 0.5, y: 0.5 }));
  const clicks = evs.filter(e => e.type === 'click' && e.via === 'pinch');
  check('pinchAt -> exactly one pinch click', clicks.length === 1, 'got ' + clicks.length);
}

// --------------------------------------------------------------------------
// BROWSE hover: cursor events, no clicks
// --------------------------------------------------------------------------
{
  const h = createHand({ mode: 'browse' });
  const evs = runClip(h, hover({ x0: 0.3, y0: 0.5, x1: 0.7, y1: 0.5, ms: 500 }));
  const cursors = evs.filter(e => e.type === 'cursor');
  const clicks = evs.filter(e => e.type === 'click');
  check('hover -> cursor events', cursors.length > 0, 'got ' + cursors.length);
  check('hover -> no clicks', clicks.length === 0, 'got ' + clicks.length);
}

// --------------------------------------------------------------------------
// toKp42 sanity
// --------------------------------------------------------------------------
{
  const lm = handPose('point', { x: 0.5, y: 0.75 }, { tip: { x: 0.5, y: 0.4 } });
  const kp = toKp42(lm, { mirrored: true, slot: 'right' });
  check('toKp42 length 42', kp.length === 42, 'len ' + kp.length);
  check('toKp42 left slot zeroed', kp.slice(0, 21).every(p => p.score === 0), '');
  check('toKp42 right slot filled', kp.slice(21).every(p => p.score > 0), '');
  check('toKp42 mirror x_raw=1-x', Math.abs(kp[21].x - (1 - lm[0].x)) < 1e-9, '');
}

// --------------------------------------------------------------------------
// report
// --------------------------------------------------------------------------
let passed = 0;
const width = Math.max(...results.map(r => r.name.length));
console.log('\n  RESULT  | TEST' + ' '.repeat(Math.max(0, width - 4)) + ' | INFO');
console.log('  --------+-' + '-'.repeat(width) + '-+------');
for (const r of results) {
  if (r.pass) passed++;
  const tag = r.pass ? '  PASS  ' : '  FAIL  ';
  console.log(`  ${tag}| ${r.name.padEnd(width)} | ${r.pass ? '' : r.info}`);
}
console.log(`\n  ${passed}/${results.length} passed\n`);
if (passed !== results.length) process.exit(1);
