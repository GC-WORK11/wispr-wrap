#!/usr/bin/env python3
"""rate_probe — real daemon, concurrent /live/kp streams, honest 429 count.

Repro for the global-deque rate limiter bug: two 40 fps consumers used to
429 each other (115/160 frames in the old code). Now each identity has its
own budget (2x the 45 fps stream cap, env WF_LIVE_BURST) plus a global
hard cap, so two coalesced consumers pass while a single abusive stream
is still rejected.

  python3 tools/live-eval/rate_probe.py --clients 2 --fps 40 --seconds 2
  python3 tools/live-eval/rate_probe.py --clients 2 --session distinct
  python3 tools/live-eval/rate_probe.py --clients 1 --fps 200 --seconds 1

Spawns wfd.py on a free 948x port with a throwaway HOME; kills it after.
Exit 0 always (the printed counts are the evidence).
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def free_port(start=9481):
    for p in range(start, start + 40):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            pass
        finally:
            s.close()
    raise SystemExit("no free port")


def frame(i):
    pts = [{"x": 0.4 + (i % 5) * 0.001, "y": 0.4, "score": 1.0}
           for _ in range(42)]
    return {"t_ms": int(time.time() * 1000), "points": pts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--fps", type=float, default=40)
    ap.add_argument("--seconds", type=float, default=2)
    ap.add_argument("--session", choices=("none", "distinct", "same"),
                    default="none")
    a = ap.parse_args()

    home = tempfile.mkdtemp(prefix="wf-rate-")
    port = free_port()
    env = dict(os.environ, HOME=home, WF_PORT=str(port),
               WF_NO_LOG_TEXT="1", XDG_SESSION_TYPE="x11")
    env.pop("DISPLAY", None)
    log = open(os.path.join(home, "wfd.log"), "w")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server/wfd.py")],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/health", timeout=0.5).read()
                break
            except Exception:
                if proc.poll() is not None:
                    raise SystemExit("wfd exited: " + log.name)
                time.sleep(0.1)
        key = open(os.path.join(home, ".config/wf/key")).read().strip()
        results = [[] for _ in range(a.clients)]
        lock = threading.Lock()

        def run(idx):
            n = int(a.fps * a.seconds)
            sid = None
            if a.session == "distinct":
                sid = "probe-%d" % idx
            elif a.session == "same":
                sid = "probe-shared"
            for i in range(n):
                body = frame(i)
                if sid:
                    body["session"] = sid
                req = urllib.request.Request(
                    base + "/live/kp", data=json.dumps(body).encode(),
                    method="POST",
                    headers={"X-WF-Key": key,
                             "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=3) as r:
                        st = r.status
                except urllib.error.HTTPError as e:
                    st = e.code
                except Exception:
                    st = -1
                with lock:
                    results[idx].append(st)
                time.sleep(1.0 / a.fps)

        ts = [threading.Thread(target=run, args=(i,))
              for i in range(a.clients)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        total429 = 0
        for i, r in enumerate(results):
            c429 = r.count(429)
            total429 += c429
            print("client %d: sent=%d 200=%d 429=%d other=%s" %
                  (i, len(r), r.count(200), c429,
                   sorted(set(x for x in r if x not in (200, 429)))))
        print("session=%s clients=%d fps=%g: total 429=%d" %
              (a.session, a.clients, a.fps, total429))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
