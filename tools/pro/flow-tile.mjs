// flow-tile.mjs — one /act tile end to end on THIS branch's daemon API:
// teach a sign bound to a tile action via /teach/begin + /live/kp takes,
// /teach/end verifies, then perform it in Control mode and check the
// action fired on real X11 (text into the allowlisted sink window).
//   SITE_ENV=<env> node tools/pro/flow-tile.mjs
import fs from 'node:fs';
import path from 'node:path';
import { stack, reporter, daemon, sleep, root, CLIPS } from './lib.mjs';

const S = stack(process.env.SITE_ENV || '/tmp/fc-pro-env/env');
const OUT = path.join(root, 'proofs/ui-v2/fresh-clone');
fs.mkdirSync(OUT, { recursive: true });
const { check, finish } = reporter(S, OUT, 'fresh clone: pair + teach + go-live tile (/act) end to end');
const { api, sh } = daemon(S);
const X = { ...process.env, DISPLAY: S.D };
const sink = () => fs.readFileSync(S.SINK, 'utf8');

const clip = (n) => JSON.parse(fs.readFileSync(
  CLIPS.map(d => path.join(d, n + '.json')).find(fs.existsSync), 'utf8')).frames;
const KP = async (frames) => {
  for (const f of frames) {
    await api('POST', '/live/kp', { t_ms: Math.round(performance.timeOrigin + f.t_ms - 1.78e12), points: f.points });
    await sleep(30);                    // ~clip pace; the endpointer needs gaps to segment
  }
};
const idle = (n, score = 0) => Array.from({ length: n }, () => ({
  t_ms: 0, points: Array.from({ length: 42 }, () => ({ x: 0, y: 0, z: 0, score })) }));
const state = async () => (await api('GET', '/live/state')).json || {};
const s0 = sink().length;

// pairing already proven by the composer page (m390-paired.png) — assert the API agrees
let r = await api('GET', '/pair-info');
check('pairing: /pair-info answers on the clone daemon', r.status === 200 && !!r.json.ip, `ip=${r.json && r.json.ip}`);

// teach: 3 takes of the same motion, bound to the type.text tile action
r = await api('POST', '/teach/begin', {
  name: 'FC-TILE', takes: 3, label: 'type fc tile', session: 'fc',
  action: { kind: 'builtin', tool: 'type.text', args: { text: 'FC-PRO-TILE' }, tier: 1 },
});
check('teach begin accepted (tile action bound)', r.status === 200 && r.json.ok === true, JSON.stringify(r.json).slice(0, 80));

const take = clip('teach-TYPE-HELLO');
for (let i = 0; i < 3; i++) { await KP(take); await KP(idle(25)); await sleep(400); }
await sleep(800);
const st1 = await state();
check('3 takes captured by the endpointer', (st1.teach || {}).got === 3, `got=${(st1.teach || {}).got}`);

r = await api('POST', '/teach/end', {});
check('teach end trains the sign', r.status === 200 && r.json.train && /trained|ready/.test(JSON.stringify(r.json.train)), JSON.stringify(r.json.train).slice(0, 90));

// go live: control mode + palm (listening) + perform the new sign
await api('POST', '/live/mode', { mode: 'control' });
await api('POST', '/live/event', { type: 'palm_open' });
try { sh('wmctrl', ['-a', 'wispr-wrap-merge-sink'], { env: X }); } catch (e) {}
await sleep(700);
await KP(clip('palm-hold')); await KP(idle(20)); await sleep(300);
await KP(clip('perform-TYPE-HELLO')); await KP(idle(30));
await sleep(2500);
const st2 = await state();
check('the sign matched live', st2.last_match && /FC.TILE/i.test(st2.last_match.gloss || st2.last_match.name || ''), JSON.stringify(st2.last_match || {}).slice(0, 120));
check('/act tile fired: text typed into the real editor', sink().slice(s0).includes('FC-PRO-TILE'), `sink+${sink().length - s0}b`);

finish();
