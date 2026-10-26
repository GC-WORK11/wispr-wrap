#!/usr/bin/env python3
"""nav.py — same-document navigation for the chromeless (--app) demo window.

chrome_nav via ctrl+l needs a URL bar; --app windows have none, so the page
is driven through CDP: setting location.hash == same-doc nav (no reload — the
pairing key and replay flag survive in localStorage).

  nav.py '#/live'           -> location.hash = '#/live'  (hash router)
  nav.py 'http://…/#/live'  -> location.href = …         (full reload)
  nav.py --eval 'expr'      -> evaluate expr, print the value
"""
import json
import os
import sys
import urllib.request

import websocket

CDP = os.environ.get("DEMO_CDP", "http://127.0.0.1:9222")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    eval_expr = sys.argv[2] if arg == "--eval" and len(sys.argv) > 2 else None
    if not arg:
        sys.exit("usage: nav.py <#/frag|url|--eval expr>")

    targets = json.load(urllib.request.urlopen(f"{CDP}/json/list", timeout=5))
    page = next((t for t in targets
                 if t.get("type") == "page" and "127.0.0.1" in t.get("url", "")),
                None)
    if not page:
        sys.exit(f"nav: no app page on {CDP}")

    if eval_expr is not None:
        expr = eval_expr
    elif arg.startswith("#"):
        expr = f"location.hash = {json.dumps(arg)}"
    else:
        expr = f"location.href = {json.dumps(arg)}"

    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=10)
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            break
    ws.close()
    res = msg.get("result", {})
    if "exceptionDetails" in res:
        sys.exit("nav eval failed: " + json.dumps(res["exceptionDetails"])[:300])
    if eval_expr is not None:
        val = res.get("result", {}).get("value")
        print("" if val is None else val)


if __name__ == "__main__":
    main()
