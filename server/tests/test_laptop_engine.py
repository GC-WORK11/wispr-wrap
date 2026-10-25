#!/usr/bin/env python3
"""Laptop-webcam power path glue (server/laptop_engine.py + the wfd.py routes).

Spawns a real wfd.py against a throwaway HOME on an ephemeral port. The engine binary is a
stand-in with sf-engine's wire behaviour (WF_SF_ENGINE points at it): it reads --url/--key-file/
--status, POSTs kp42 frames tagged source=laptop with its own session id, writes a status file,
and exits on 403/409 like the real one. The real sf-engine (sign-flow crates/sf-engine) is
exercised end to end by sign-flow tools/engine/e2e_engine.sh.

Contracts:
  * GET /engine says laptop_cam only when an engine binary exists, and perception=sf-engine
    only while it is attached (running AND its frames reached the daemon)
  * POST /live/source laptop starts the engine; phone frames are then refused (409) and the
    engine's frames feed the endpointer; /live/source phone stops it (process gone)
  * a teach begun right after the switch survives the engine's first frame
  * GET /live/frame returns the latest frame (needs the key)
  * an engine that dies hands the source back to the phone
  * no engine installed -> /live/source laptop is refused, the phone stays the source

Run:  python3 server/tests/test_laptop_engine.py
"""
import http.client
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # server/
WFD = os.path.join(BASE, "wfd.py")

FAKE_ENGINE = r'''#!/usr/bin/env python3
# stand-in for sign-flow sf-engine: same argv, same wire, same exits
import http.client, json, math, os, sys, time
a = sys.argv[1:]
def arg(k, d=None):
    return a[a.index(k) + 1] if k in a else d
url = arg("--url"); key = open(arg("--key-file")).read().strip(); status = arg("--status")
host, port = url[len("http://"):].split(":")
mode = os.environ.get("FAKE_ENGINE_MODE", "wave")
sess = "laptop-%d" % os.getpid()
def hand(cx, cy, s=0.06):
    return [{"x": cx + s * math.cos(2 * math.pi * k / 21) * (0.4 + 0.6 * (k % 5) / 4),
             "y": cy + s * math.sin(2 * math.pi * k / 21), "score": 0.9} for k in range(21)]
t0 = int(time.time() * 1000); i = 0
while True:
    i += 1
    if mode == "die" and i > 5:
        sys.exit(7)
    ph = (i % 45) / 45.0
    moving = ph < 0.5
    cx = 0.5 + (0.15 * math.sin(2 * math.pi * ph * 2) if moving else 0.0)
    pts = [{"x": 0, "y": 0, "score": 0}] * 21 + hand(cx, 0.5)
    body = json.dumps({"t_ms": t0 + i * 66, "seq": i, "session": sess, "source": "laptop",
                       "w": 640, "h": 480, "hands": 1, "points": pts}).encode()
    c = http.client.HTTPConnection(host, int(port), timeout=3)
    try:
        c.request("POST", "/live/kp", body, {"X-WF-Key": key, "Content-Type": "application/json"})
        r = c.getresponse(); r.read()
    except OSError:
        time.sleep(0.2); continue
    if r.status in (403, 409):
        sys.exit(0)
    with open(status + ".tmp", "w") as f:
        json.dump({"engine": "sf-engine", "vision": "fake-pose", "source": "fake camera",
                   "fps": {"camera": 15, "pose": 15, "posted": 15},
                   "ms": {"pose": {"p50": 40.0}}, "poses": i, "present": i}, f)
    os.replace(status + ".tmp", status)
    time.sleep(1 / 15)
'''


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def hand(cx, cy, s=0.06):
    import math
    return [{"x": cx + s * math.cos(2 * math.pi * k / 21), "y": cy + s * math.sin(2 * math.pi * k / 21),
             "score": 0.9} for k in range(21)]


class Daemon:
    def __init__(self, engine=True, mode="wave"):
        self.tmp = tempfile.mkdtemp(prefix="wf-laptop-")
        self.port = free_port()
        env = dict(os.environ, HOME=self.tmp, WF_PORT=str(self.port), WF_NO_LOG_TEXT="1",
                   FAKE_ENGINE_MODE=mode, DISPLAY="")
        env.pop("WF_SF_ENGINE_ARGS", None)
        if engine:
            fake = os.path.join(self.tmp, "sf-engine")
            with open(fake, "w") as f:
                f.write(FAKE_ENGINE)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
            env["WF_SF_ENGINE"] = fake
        else:
            env["WF_SF_ENGINE"] = os.path.join(self.tmp, "no-such-engine")
        self.out = open(os.path.join(self.tmp, "wfd.log"), "wb")
        self.proc = subprocess.Popen([sys.executable, WFD], cwd=BASE, env=env,
                                     stdout=self.out, stderr=subprocess.STDOUT)
        key_path = os.path.join(self.tmp, ".config", "wf", "key")
        for _ in range(100):
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                c.request("GET", "/health")
                if c.getresponse().status == 200 and os.path.exists(key_path):
                    break
            except OSError:
                pass
            time.sleep(0.1)
        with open(key_path) as f:
            self.key = f.read().strip()

    def req(self, method, path, body=None, key=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"}
        if key:
            h["X-WF-Key"] = self.key
        c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
        r = c.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def phone_frame(self, i=0, session="phone-1"):
        pts = [{"x": 0, "y": 0, "score": 0}] * 21 + hand(0.5, 0.5)
        return self.req("POST", "/live/kp", {"t_ms": 1000 + i * 66, "session": session, "points": pts})

    def close(self):
        try:
            self.req("POST", "/live/source", {"source": "phone"})
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        self.out.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def wait_for(fn, timeout=8.0):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return fn()


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie is not alive
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return False


class LaptopEngineTest(unittest.TestCase):
    def setUp(self):
        self.d = Daemon()

    def tearDown(self):
        self.d.close()

    def test_engine_report_is_honest(self):
        s, e = self.d.req("GET", "/engine")
        self.assertEqual(s, 200)
        self.assertTrue(e["laptop_cam"])                    # a binary exists
        self.assertEqual(e["camera"], "phone")
        self.assertEqual(e["perception"], "browser")        # nothing attached yet
        self.assertNotEqual(e["engine"], "rust-kp53")
        s, r = self.d.req("POST", "/live/source", {"source": "laptop"})
        self.assertEqual(s, 200, r)
        self.assertTrue(r["ok"])
        e = wait_for(lambda: (lambda x: x if x["laptop"]["attached"] else None)(self.d.req("GET", "/engine")[1]))
        self.assertTrue(e["laptop"]["attached"], e)
        self.assertEqual(e["camera"], "laptop")
        self.assertIn("sf-engine", e["perception"])
        self.assertEqual(e["laptop"]["vision"], "fake-pose")
        self.assertGreater(e["laptop"]["frames"], 0)

    def test_one_camera_at_a_time_and_stop_turns_it_off(self):
        s, r = self.d.phone_frame()
        self.assertEqual(s, 200, r)
        s, r = self.d.req("POST", "/live/source", {"source": "laptop"})
        self.assertEqual(s, 200, r)
        pid = r["laptop"]["pid"]
        self.assertTrue(pid and pid_alive(pid))
        wait_for(lambda: self.d.req("GET", "/engine")[1]["laptop"]["attached"])
        s, r = self.d.phone_frame(1)
        self.assertEqual(s, 409, r)                           # the phone is not the source now
        self.assertEqual(r["error"], "source-inactive")
        s, f = self.d.req("GET", "/live/frame")
        self.assertEqual(s, 200)
        self.assertEqual(f["frame"]["source"], "laptop")
        self.assertEqual(len(f["frame"]["points"]), 42)
        self.assertEqual(f["frame"]["w"], 640)
        self.assertLess(f["frame"]["age_ms"], 2000)
        s, r = self.d.req("POST", "/live/source", {"source": "phone"})
        self.assertEqual(s, 200, r)
        self.assertEqual(r["camera"], "phone")
        self.assertTrue(wait_for(lambda: not pid_alive(pid), 5))   # process gone = camera off
        s, r = self.d.phone_frame(2, session="phone-2")
        self.assertEqual(s, 200, r)

    def test_live_frame_needs_the_key(self):
        s, _ = self.d.req("GET", "/live/frame", key=False)
        self.assertEqual(s, 403)
        s, _ = self.d.req("POST", "/live/source", {"source": "laptop"}, key=False)
        self.assertEqual(s, 403)

    def test_teach_begun_right_after_the_switch_survives_the_first_engine_frame(self):
        s, r = self.d.req("POST", "/live/source", {"source": "laptop"})
        self.assertEqual(s, 200, r)
        s, r = self.d.req("POST", "/teach/begin", {"name": "wave", "takes": 3})
        self.assertEqual(s, 200, r)
        wait_for(lambda: self.d.req("GET", "/engine")[1]["laptop"]["frames"] > 5)
        s, st = self.d.req("GET", "/live/state")
        self.assertTrue(st["teach"]["active"], st["teach"])
        self.assertEqual(st["teach"]["name"], "WAVE")
        self.assertTrue(st["session"].startswith("laptop-"), st["session"])

    def test_teach_begun_before_the_switch_survives_it(self):
        # teach.js: POST /teach/begin, then the wizard starts the laptop camera
        s, r = self.d.req("POST", "/teach/begin", {"name": "nod", "takes": 3})
        self.assertEqual(s, 200, r)
        s, r = self.d.req("POST", "/live/source", {"source": "laptop"})
        self.assertEqual(s, 200, r)
        wait_for(lambda: self.d.req("GET", "/engine")[1]["laptop"]["frames"] > 5)
        s, st = self.d.req("GET", "/live/state")
        self.assertTrue(st["teach"]["active"], st["teach"])
        self.assertEqual(st["teach"]["name"], "NOD")

    def test_bad_source_is_refused(self):
        s, r = self.d.req("POST", "/live/source", {"source": "webcam9"})
        self.assertEqual(s, 409, r)
        self.assertFalse(r["ok"])
        self.assertEqual(r["camera"], "phone")


class DyingEngineTest(unittest.TestCase):
    def test_a_dead_engine_hands_the_source_back_to_the_phone(self):
        d = Daemon(mode="die")
        try:
            s, r = d.req("POST", "/live/source", {"source": "laptop"})
            self.assertEqual(s, 200, r)
            e = wait_for(lambda: (lambda x: x if x["camera"] == "phone" else None)(d.req("GET", "/engine")[1]))
            self.assertEqual(e["camera"], "phone", e)
            self.assertEqual(e["laptop"]["last_exit"]["code"], 7)
            s, r = d.phone_frame()
            self.assertEqual(s, 200, r)
        finally:
            d.close()


class NoEngineTest(unittest.TestCase):
    def test_no_engine_installed(self):
        d = Daemon(engine=False)
        try:
            s, e = d.req("GET", "/engine")
            self.assertFalse(e["laptop_cam"])
            s, r = d.req("POST", "/live/source", {"source": "laptop"})
            self.assertEqual(s, 409, r)
            self.assertIn("not installed", r["message"])
            self.assertEqual(r["camera"], "phone")
            s, r = d.phone_frame()
            self.assertEqual(s, 200, r)
        finally:
            d.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
