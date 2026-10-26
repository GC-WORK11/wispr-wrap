# Backend notes — PEAK pass (Oct 26)

Scope: `server/wfd.py` handlers for `/send`, `/send-image`, `/send-file`,
`/status`, `/health` (+HEAD), `/pair`, `/pair-info`, `/act` + `/act/icons`,
and `server/wfclip.py`. Goal: every phone→laptop button works reliably on a
real run; phone videos (20–300 MB) land without blowing up RAM.

## What changed

**Large uploads (`/send-file`)**
- Now **streams** the request body to a temp file in `~/wf-uploads` in 1 MiB
  chunks (`stream_upload`), instead of reading the whole body into RAM via
  `_read_body`. Peak RSS stays flat across a 300 MB upload (measured: VmHWM
  unchanged).
- Cap enforced **before** reading a byte: `Content-Length > WF_MAX_UPLOAD`
  → `413` with a human `error` string, connection closed.
- Free-disk check up front: not enough space → `507` with a human message.
- Filenames sanitised (`sanitize_upload_name`): basename only (no `../`,
  `/`, `\`), control chars stripped, extension kept, leading dots collapsed,
  collision-safe (`<epoch>_<name>`, random suffix on clash).
- `fsync` + atomic `rename` on completion; **partials deleted** if the client
  disconnects mid-upload (a short read raises and the temp file is unlinked).
- Response contract unchanged: `{ok, into, path, mb}`.
- Auth is checked first with constant-time `hmac.compare_digest`.

**Images (`/send-image`)** — now capped by `BODY_CAPS["/send-image"]`
(`WF_MAX_IMAGE`, default 64 MiB) so one request can't balloon RSS. The
resident clipboard owner (`wfclip.py`) is unchanged and keeps the paste alive.

**Text (`/send`)**
- `mode` validated: anything but `type`/`paste` → `400` with a human error.
- Optional focus-verify: if the client sends `expect` (the wm_class it
  expected) and focus has moved, the daemon refuses with `409` + a human
  "focus moved to '…'" message instead of typing into the wrong window.
- Shell metacharacters, leading `-`, unicode, tabs/newlines all type
  literally — injection is argv-only via `xdotool type --`, never a shell.

**/status + /health**
- `laptop.can_act` is now `false` when there is no `DISPLAY` (was: provider
  presence only).
- `detect_engine()` result cached ~10 s (status polls every 4 s).
- HEAD for `/health` and `/status` already answered headers-only; unchanged.
- Unauthenticated `/status` returns only `{"ok":false,"error":"unpaired"}`.

**/act + icons**
- The `.desktop`-scan-backed portion of `/act` is cached ~60 s
  (`act_catalog_static`); only `pending` is recomputed per call. Warm calls
  measured ~4 ms.
- `/act/icons/<id>` now sends `Cache-Control: private, max-age=86400` and an
  `ETag`; `If-None-Match` returns `304`. SVG served as `image/svg+xml`; XPM
  and non-image bytes refused (`404`); over-size icons `413`. Traversal guard
  (charset regex) unchanged.

**/pair**
- `429` now carries `Retry-After: 1`.
- Wrong code → `403` JSON with a human `error`.

## Env vars
| var | default | meaning |
|---|---|---|
| `WF_MAX_UPLOAD` | `1073741824` (1 GiB) | hard cap for `/send-file` |
| `WF_MAX_IMAGE` | `67108864` (64 MiB) | cap for `/send-image` body |
| `WF_DISK_HEADROOM` | `67108864` (64 MiB) | free space to keep after an upload |
| `WF_ACT_CACHE_S` | `60` | `/act` catalog cache TTL |
| `WF_NO_LOG_TEXT` | unset | when set, dictated text is not logged |

Limits: uploads stream at 1 MiB/chunk → bounded memory regardless of file
size; a `>cap` request is rejected in ~10 ms without reading the body.

## Proof
- `server/tests/test_send_media.py`, `test_status_act.py`, `test_pair_edge.py`
  (+23 tests; suite 152→175, green).
- `tools/realrun/verify_large_upload.sh` — streaming / cap / RSS / traversal /
  abort, all PASS on the realrun stack.
- `tools/peak/flow_bigvideo.mjs` — a ~55 MB video through the REAL phone UI
  lands byte-identical (sha256) and its path is pasted into SinkEdit.
- `tools/peak/flows.mjs send,teach,live` — send flow PASS end to end.
