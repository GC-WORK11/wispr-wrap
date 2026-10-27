// Payload schema for POST /v1/samples. Hand landmarks only.
//
// {
//   "v": 1,
//   "install": "<32 hex chars, random per install>",
//   "gloss": "HELLO",
//   "frames": [ { "t": 0, "p": [[x, y, z], ... 42 points] }, ... ]
// }
//
// Any other key, at any level, is rejected.

export const MAX_BODY_BYTES = 128 * 1024;
export const MAX_FRAMES = 64;
export const POINTS = 42;
export const MAX_T_MS = 30_000;

export interface Sample {
  v: 1;
  install: string;
  gloss: string;
  frames: { t: number; p: [number, number, number][] }[];
}

export type Result = { ok: true; sample: Sample } | { ok: false; error: string };

const INSTALL_RE = /^[0-9a-f]{32}$/;
const GLOSS_RE = /^[A-Z0-9_.\-]{1,48}$/;

function isObj(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}
function onlyKeys(o: Record<string, unknown>, keys: string[]): boolean {
  const k = Object.keys(o);
  return k.length === keys.length && keys.every((x) => k.includes(x));
}
function coord(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n) && n >= -10 && n <= 10;
}

export function validate(body: unknown): Result {
  if (!isObj(body) || !onlyKeys(body, ["v", "install", "gloss", "frames"]))
    return { ok: false, error: "unexpected fields" };
  if (body.v !== 1) return { ok: false, error: "unsupported version" };
  if (typeof body.install !== "string" || !INSTALL_RE.test(body.install))
    return { ok: false, error: "bad install id" };
  if (typeof body.gloss !== "string" || !GLOSS_RE.test(body.gloss))
    return { ok: false, error: "bad gloss" };
  const frames = body.frames;
  if (!Array.isArray(frames) || frames.length < 2 || frames.length > MAX_FRAMES)
    return { ok: false, error: "bad frame count" };
  let last = -1;
  for (const f of frames) {
    if (!isObj(f) || !onlyKeys(f, ["t", "p"])) return { ok: false, error: "bad frame" };
    if (typeof f.t !== "number" || !Number.isInteger(f.t) || f.t < last || f.t > MAX_T_MS)
      return { ok: false, error: "bad time" };
    last = f.t;
    if (!Array.isArray(f.p) || f.p.length !== POINTS) return { ok: false, error: "bad points" };
    for (const pt of f.p) {
      if (!Array.isArray(pt) || pt.length !== 3 || !pt.every(coord))
        return { ok: false, error: "bad point" };
    }
  }
  return { ok: true, sample: body as unknown as Sample };
}
