// signflow-contribute — intake for opt-in training samples.
//
// POST /v1/samples   one Teach take as hand landmarks (see schema.ts)
//
// The Worker stores only the validated sample, under a random object key.
// It does not store the client IP, user agent or any request headers. The
// IP is used in memory as the rate-limit key and is not written anywhere.

import { MAX_BODY_BYTES, validate } from "./schema.js";

export interface Env {
  SAMPLES: R2Bucket;
  LIMITER: RateLimit;
}

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8" };

function reply(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

async function readBody(request: Request): Promise<string | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) return null;
  const buf = await request.arrayBuffer();
  if (buf.byteLength > MAX_BODY_BYTES) return null;
  return new TextDecoder().decode(buf);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/v1/samples") return reply(404, { ok: false, error: "not found" });
    if (request.method !== "POST") return reply(405, { ok: false, error: "method not allowed" });
    if (!(request.headers.get("content-type") ?? "").startsWith("application/json"))
      return reply(415, { ok: false, error: "json only" });

    const key = request.headers.get("cf-connecting-ip") ?? "unknown";
    const { success } = await env.LIMITER.limit({ key });
    if (!success) return reply(429, { ok: false, error: "rate limited" });

    const text = await readBody(request);
    if (text === null) return reply(413, { ok: false, error: "too large" });

    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      return reply(400, { ok: false, error: "invalid json" });
    }
    const r = validate(parsed);
    if (!r.ok) return reply(422, { ok: false, error: r.error });

    const day = new Date().toISOString().slice(0, 10);
    const objectKey = `v1/${day}/${r.sample.gloss}/${crypto.randomUUID()}.json`;
    await env.SAMPLES.put(objectKey, JSON.stringify(r.sample), {
      httpMetadata: { contentType: "application/json" },
    });
    return reply(201, { ok: true });
  },
} satisfies ExportedHandler<Env>;
