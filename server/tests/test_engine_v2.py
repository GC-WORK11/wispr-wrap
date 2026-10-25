#!/usr/bin/env python3
"""Daemon-level end-to-end test of the v2 sign engine: a real wfd.py on a throwaway HOME and port, driven only through
its HTTP API with kp42 frames in normalized coordinates - teach two gestures with the Teach flow, switch to listening,
perform held-out takes, read the match from /live/state. No mocks between the HTTP socket and the bank.

Covers: engine selection (v2 for a new bank, the v1 file keeps v1 and is not clobbered), teach -> match through the
endpointer, an unrelated movement is not accepted, the look-alike warning when the same gesture is taught twice, and
the laptop-camera wire (`body` + `w`/`h`) reaching the bank intact.

Run:  python3 server/tests/test_engine_v2.py     (needs numpy; skipped without it)
"""
import http.client
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                      # server/
sys.path.insert(0, HERE)
sys.path.insert(0, BASE)
import test_signbank2 as g  # noqa: E402  (raises SkipTest without numpy)

WFD = os.path.join(BASE, "wfd.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Daemon:
    def __init__(self, env_extra=None, bank_file=None):
        self.tmp = tempfile.mkdtemp(prefix="wf-v2-")
        self.port = free_port()
        cfg = os.path.join(self.tmp, ".config", "wf")
        os.makedirs(cfg)
        self.bank_path = os.path.join(cfg, "signbank.json")
        if bank_file is not None:
            with open(self.bank_path, "w") as f:
                json.dump(bank_file, f)
        env = dict(os.environ, HOME=self.tmp, WF_PORT=str(self.port), WF_NO_LOG_TEXT="1", WF_LIVE_MAX_FPS="5000",
                   **(env_extra or {}))
        self.proc = subprocess.Popen([sys.executable, WFD], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if self.raw("GET", "/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            self.proc.kill()
            raise RuntimeError("wfd.py did not come up")
        with open(os.path.join(cfg, "key")) as f:
            self.key = f.read().strip()
        self.t = 1000                              # the stream clock (ms), independent of wall time
        # the app streams frames from the moment the camera opens: a session id first seen AFTER /teach/begin would
        # (by design) reset a dangling teach, so establish the session before any test teaches
        self.frames([{"points": g.absent() + g.absent()}])

    def raw(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def req(self, method, path, obj=None):
        h = {"X-WF-Key": self.key}
        body = None
        if obj is not None:
            body = json.dumps(obj).encode()
            h["Content-Type"] = "application/json"
        st, data = self.raw(method, path, body, h)
        try:
            return st, json.loads(data)
        except ValueError:
            return st, {"raw": data}

    def frames(self, frames, extra=None):
        """POST frames one by one with a continuous clock; returns every reply that carries news."""
        news = []
        for fr in frames:
            self.t += 67
            payload = {"t_ms": self.t, "points": fr["points"], "session": "v2test"}
            payload.update(extra or {})
            payload.update({k: fr[k] for k in ("body",) if k in fr})
            st, r = self.req("POST", "/live/kp", payload)
            assert st == 200, (st, r)
            if any(k in r for k in ("teach", "match", "mode", "dropped")):
                news.append(r)
        return news

    def perform(self, kind, seed, extra=None, **kw):
        """A gesture, then a second of rest so the endpointer closes the segment."""
        rng = random.Random(seed)
        fr = g.gesture(kind, rng, n=rng.randint(19, 26), **kw)
        rest = [{"points": fr[-1]["points"], **({"body": fr[-1]["body"]} if "body" in fr[-1] else {})}
                for _ in range(16)]
        return self.frames(fr + rest, extra)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def teach(d, gloss, kind, seed0, takes=3, **kw):
    st, r = d.req("POST", "/teach/begin", {"name": gloss, "takes": takes})
    assert st == 200 and r["ok"], (st, r)
    done = None
    for i in range(takes):
        for n in d.perform(kind, seed0 + i, **kw):
            if "teach" in n and n["teach"].get("done"):
                done = n["teach"]
    d.req("POST", "/teach/end", {})
    return done


class TestV2Daemon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Daemon({"WF_BANK_ENGINE": "v2"})

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_1_engine_is_v2(self):
        st, r = self.d.req("GET", "/engine")
        self.assertEqual(st, 200)
        self.assertEqual(r.get("engine"), "python-dtw2", r)

    def test_2_teach_then_perform_through_the_endpointer(self):
        d = self.d
        self.assertTrue(teach(d, "SWIPE-R", "swipe-r", 10), "teach did not finish")
        self.assertTrue(teach(d, "SWIPE-L", "swipe-l", 20), "teach did not finish")
        st, r = d.req("GET", "/bank")
        glosses = sorted(s["gloss"] for s in r.get("signs", r.get("bank", {}).get("signs", [])))
        self.assertEqual(glosses, ["SWIPE-L", "SWIPE-R"], r)
        d.req("POST", "/live/listen", {"on": True})
        got = {}
        for kind, seed in (("swipe-r", 90), ("swipe-l", 91), ("down", 92)):
            news = [n for n in d.perform(kind, seed) if "match" in n]
            self.assertTrue(news, f"no match event for {kind}")
            got[kind] = news[-1]["match"]
        self.assertEqual((got["swipe-r"]["gloss"], got["swipe-r"]["accepted"]), ("SWIPE-R", True), got)
        self.assertEqual((got["swipe-l"]["gloss"], got["swipe-l"]["accepted"]), ("SWIPE-L", True), got)
        self.assertFalse(got["down"]["accepted"], got)
        self.assertIn(got["down"]["reason"], ("threshold", "too-close", "everyday-movement", "held-pose"), got)

    def test_3_teaching_the_same_gesture_twice_warns(self):
        d = self.d
        done = teach(d, "ALSO-SWIPE-R", "swipe-r", 10)        # the very same performances under another name
        self.assertTrue(done)
        c = done.get("conflict")
        self.assertTrue(c, f"no look-alike warning in {done}")
        self.assertEqual(c["other"], "SWIPE-R")
        self.assertEqual(c["level"], "conflict")


class TestV2LaptopWire(unittest.TestCase):
    """The laptop camera sends `body` (11 x [x,y,score]) and w/h next to the hand points."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon({"WF_BANK_ENGINE": "v2"})

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    @staticmethod
    def body_for(cx, cy):
        # shoulders at indices 5 and 6, a face above them; wrists at 9 and 10
        b = [[0.5, 0.25, 0.9]] * 5 + [[0.58, 0.45, 0.9], [0.42, 0.45, 0.9]] + [[0.6, 0.6, 0.9], [0.4, 0.6, 0.9],
                                                                              [0.62, 0.75, 0.9], [cx, cy, 0.9]]
        return b

    def wire(self, kind, seed):
        rng = random.Random(seed)
        fr = g.gesture(kind, rng, n=rng.randint(19, 26))
        for f in fr:
            p = f["points"][21]                        # right wrist
            f["body"] = self.body_for(p["x"], p["y"])
        return fr

    def run_gesture(self, kind, seed, with_body=True):
        d = self.d
        fr = self.wire(kind, seed)
        last = fr[-1]
        rest = [dict(points=last["points"], body=last["body"]) for _ in range(16)]
        frames = fr + rest
        if not with_body:
            frames = [{"points": f["points"]} for f in frames]
        return d.frames(frames, {"w": 1280, "h": 720})

    def test_body_frames_teach_and_match_in_body_mode(self):
        d = self.d
        d.req("POST", "/teach/begin", {"name": "UP", "takes": 3})
        done = None
        for i in range(3):
            for n in self.run_gesture("up", 30 + i):
                if "teach" in n and n["teach"].get("done"):
                    done = n["teach"]
        self.assertTrue(done, "teach did not finish with body frames")
        d.req("POST", "/teach/begin", {"name": "DOWN", "takes": 3})
        for i in range(3):
            self.run_gesture("down", 40 + i)
        d.req("POST", "/teach/end", {})
        st, r = d.req("GET", "/bank")
        self.assertEqual(st, 200)
        d.req("POST", "/live/listen", {"on": True})
        news = [n for n in self.run_gesture("up", 77) if "match" in n]
        self.assertTrue(news)
        m = news[-1]["match"]
        self.assertEqual((m["gloss"], m["accepted"]), ("UP", True), m)
        # the same gesture without the body block cannot be read by a body-mode bank: refused, not guessed
        news = [n for n in self.run_gesture("up", 78, with_body=False) if "match" in n]
        self.assertTrue(not news or not news[-1]["match"]["accepted"], news)


class TestEngineSelection(unittest.TestCase):
    def test_a_v1_bank_file_keeps_serving_with_v1_and_is_not_clobbered(self):
        v1 = {"format": "wf-signbank/1", "engine": "python-dtw", "space": "pixel", "reject_threshold": 0.6,
              "templates": [{"gloss": "OLD", "label": "old", "tier": 0, "action": None, "user_samples": 1,
                             "samples": [{"traj": [[0.0, 0.0]] * 32, "shape": None}]}]}
        d = Daemon({}, bank_file=v1)
        try:
            st, r = d.req("GET", "/engine")
            self.assertEqual(r.get("engine"), "python-dtw", r)
            with open(d.bank_path) as f:
                self.assertEqual(json.load(f)["format"], "wf-signbank/1")
        finally:
            d.stop()

    def test_forcing_v2_over_a_v1_file_keeps_a_backup(self):
        v1 = {"format": "wf-signbank/1", "templates": [{"gloss": "OLD", "samples": []}]}
        d = Daemon({"WF_BANK_ENGINE": "v2"}, bank_file=v1)
        try:
            st, r = d.req("GET", "/engine")
            self.assertEqual(r.get("engine"), "python-dtw2", r)
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(d.bank_path), "signbank.v1.json")))
        finally:
            d.stop()

    def test_v1_can_be_forced(self):
        d = Daemon({"WF_BANK_ENGINE": "v1"})
        try:
            st, r = d.req("GET", "/engine")
            self.assertEqual(r.get("engine"), "python-dtw", r)
        finally:
            d.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
