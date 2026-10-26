"""Regression tests for the live-session bugs found on the real stack:

  * a foreign /live/kp session used to live_reset() and silently cancel
    an in-progress teach — teach is now owned by a session and foreign
    frames get 409;
  * a still palm must never match a motion sign at the 0.45 bank
    threshold (stillness gate);
  * the /live/kp rate limiter must be per-identity, not a single global
    window (two nominal streams must not starve each other);
  * /live/state must serialize as strict JSON — one-sign banks carried
    margin=Infinity, which JSON.parse rejects (state poll froze).
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wfd  # noqa: E402
import signbank  # noqa: E402

WFD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "wfd.py")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _hand_frame(x, y, t_ms, n=21):
    pts = [{"x": x + (i % 5) * 0.02, "y": y + (i // 5) * 0.02,
            "score": 0.9} for i in range(n)]
    return {"t_ms": t_ms, "points": pts}


def _pts(x, y, n=42):
    return [{"x": x, "y": y, "score": 0.9} for _ in range(n)]


class _StubEP:
    current_len = 0
    active = False

    def feed(self, frame):
        return []


class _StubBank:
    def add_exemplar(self, *a, **k):
        return True


def _fresh():
    """Reset module state the way a daemon boot leaves it."""
    wfd.LIVE.update(session=None, listening=False, last_match=None,
                    seen=0.0, last_accept=None, dup_fires=0,
                    observe=False, text_mode=False)
    wfd.LIVE["rates"].clear()
    wfd.LIVE["rate_all"].clear()
    wfd.LIVE["feed"].clear()
    wfd.LIVE["ep"] = _StubEP()
    wfd.TEACH.update(active=False, name="", takes=0, got=0,
                     label="", action=None, session=None)


class TestTeachSessionOwnership(unittest.TestCase):
    def setUp(self):
        _fresh()
        self._bank = wfd.BANK
        wfd.BANK = _StubBank()

    def tearDown(self):
        wfd.BANK = self._bank
        _fresh()

    def test_foreign_kp_does_not_cancel_teach(self):
        code, _ = wfd.teach_begin({"name": "SWIPE-R", "takes": 2})
        self.assertEqual(code, 200)
        # the teacher's stream claims the session
        code, _ = wfd.live_kp({"t_ms": 1, "points": _pts(0.5, 0.5),
                               "session": "sess-A"})
        self.assertTrue(wfd.TEACH["active"])
        self.assertEqual(wfd.TEACH["session"], "sess-A")
        # a foreign stream must get 409 and the teach must survive
        code, body = wfd.live_kp({"t_ms": 2, "points": _pts(0.5, 0.5),
                                  "session": "sess-B"})
        self.assertEqual(code, 409)
        self.assertTrue(wfd.TEACH["active"])
        self.assertEqual(wfd.TEACH["name"], "SWIPE-R")
        self.assertEqual(wfd.TEACH["session"], "sess-A")

    def test_teach_begin_prebinds_session(self):
        code, _ = wfd.teach_begin({"name": "SWIPE-L", "takes": 2,
                                   "session": "sess-A"})
        self.assertEqual(code, 200)
        self.assertEqual(wfd.TEACH["session"], "sess-A")
        # foreign stream refused even before the owner sent a frame
        code, _ = wfd.live_kp({"t_ms": 2, "points": _pts(0.5, 0.5),
                               "session": "sess-B"})
        self.assertEqual(code, 409)
        # owner's own stream still flows
        code, _ = wfd.live_kp({"t_ms": 3, "points": _pts(0.5, 0.5),
                               "session": "sess-A"})
        self.assertEqual(code, 200)
        self.assertTrue(wfd.TEACH["active"])

    def test_idle_expiry_does_not_cancel_teach(self):
        code, _ = wfd.teach_begin({"name": "PUSH", "takes": 2})
        self.assertEqual(code, 200)
        # idle-gap that used to trigger live_reset() and wipe the teach
        wfd.LIVE["seen"] = 1.0
        code, _ = wfd.live_kp({"t_ms": 5, "points": _pts(0.5, 0.5)})
        self.assertTrue(wfd.TEACH["active"])
        self.assertEqual(wfd.TEACH["name"], "PUSH")


class TestStillPalmRejection(unittest.TestCase):
    """A still palm must not match a motion sign at the 0.45 threshold."""

    def test_still_palm_rejected(self):
        b = signbank.SignBank()
        swipe = [dict(_hand_frame(0.3 + i * 0.02, 0.5, i * 66),
                      t_ms=i * 66) for i in range(20)]
        self.assertTrue(b.add_exemplar("SWIPE-R", swipe))
        still = [dict(_hand_frame(0.5, 0.5, i * 66), t_ms=i * 66)
                 for i in range(20)]
        m = b.match(still)
        self.assertIsNotNone(m)
        self.assertFalse(m.accepted,
                         f"still palm matched {m.gloss} at {m.dist}")
        self.assertGreater(m.dist, 0.45)


class TestPerClientRateLimit(unittest.TestCase):
    def setUp(self):
        _fresh()

    def test_two_clients_do_not_starve(self):
        """Two nominal 40fps consumers used to sum past the global 45fps
        window and 429 each other; per-identity windows fix that."""
        now = 1000.0
        for i in range(40):
            now += 0.025
            self.assertFalse(wfd._rate_limited("sid:a", now))
            self.assertFalse(wfd._rate_limited("sid:b", now))

    def test_single_flooder_still_limited(self):
        now = 1000.0
        rejected = 0
        for _ in range(200):
            now += 0.005          # 200fps
            if wfd._rate_limited("sid:a", now):
                rejected += 1
        self.assertGreater(rejected, 0)


class TestStrictJsonState(unittest.TestCase):
    def setUp(self):
        _fresh()

    def tearDown(self):
        _fresh()

    def test_state_serializes_strictly(self):
        """margin=Infinity (one-sign bank) made /live/state unparseable."""
        wfd.LIVE["last_match"] = {"gloss": "X", "dist": 0.0,
                                  "accepted": True,
                                  "margin": float("inf")}
        st = wfd._finite(wfd.live_state())
        json.dumps(st, allow_nan=False)   # strict: raises on inf/nan
        self.assertIsNone(st["margin"])

    def test_finite_passes_through(self):
        o = {"a": [1.5, {"b": -2.0}], "c": "x"}
        self.assertEqual(wfd._finite(o), o)


class _Match:
    gloss = "HELLO"
    label = "hello"
    dist = 0.01
    accepted = True
    score = 0.9
    margin = 0.5
    second_gloss = None
    reason = ""
    action = None
    tier = 0


class _MatchBank:
    def match(self, frames):
        return _Match()


class TestTextMode(unittest.TestCase):
    """Go Live Text|Control: text mode types the sign's word into the
    focused window instead of dispatching its action."""

    def setUp(self):
        _fresh()
        self._bank, self._ops = wfd.BANK, wfd.OPS
        wfd.BANK = _MatchBank()
        wfd.LIVE["listening"] = True
        wfd.LIVE["session"] = "sess-A"

    def tearDown(self):
        wfd.BANK, wfd.OPS = self._bank, self._ops
        _fresh()

    def _seg(self):
        fr = [{"t_ms": i * 66, "points": _pts(0.4 + i * 0.01, 0.5, 21)}
              for i in range(8)]
        return wfd._on_segment(fr, fr[-1]["t_ms"])

    def test_text_mode_types_not_fires(self):
        wfd.LIVE["text_mode"] = True
        out = self._seg()
        act = (out.get("match") or {}).get("action") or {}
        self.assertEqual(act.get("kind"), "text")
        self.assertEqual(act.get("typed"), "hello")
        # the type job queued for the worker thread
        job = wfd._ACT_Q.get(timeout=1)
        self.assertTrue(callable(job))
        evs = [e.get("type") for e in wfd.LIVE["feed"]]
        self.assertIn("type", evs)

    def test_control_mode_does_not_type(self):
        wfd.LIVE["text_mode"] = False
        wfd.LIVE["observe"] = True      # observe: dispatch path, no OS fire
        out = self._seg()
        act = (out.get("match") or {}).get("action") or {}
        self.assertNotEqual(act.get("kind"), "text")

    def test_observe_dominates_text(self):
        wfd.LIVE["text_mode"] = True
        wfd.LIVE["observe"] = True
        out = self._seg()
        act = (out.get("match") or {}).get("action") or {}
        self.assertNotEqual(act.get("kind"), "text")


class TestAirEvents(unittest.TestCase):
    """Section-A Go Live events: air letters route to actions, and the
    pointer/scroll/click helpers validate and queue real input jobs."""

    def setUp(self):
        while not wfd._ACT_Q.empty():
            wfd._ACT_Q.get_nowait()
        while not wfd._INJECT_Q.empty():
            wfd._INJECT_Q.get_nowait()

    def test_air_letter_c_routes_builtin(self):
        rc, body = wfd._air_letter({"letter": "C"})
        self.assertEqual(rc, 202)
        self.assertEqual(body["gloss"], "AIR-C")
        self.assertIn("Chrome", body["action"]["label"])
        self.assertTrue(wfd._ACT_Q.get(timeout=1))  # real routing job queued

    def test_air_letter_v_routes_builtin(self):
        rc, body = wfd._air_letter({"letter": "V"})
        self.assertEqual(rc, 202)
        self.assertEqual(body["gloss"], "AIR-V")
        self.assertTrue(wfd._ACT_Q.get(timeout=1))

    def test_air_letter_unknown_404s(self):
        rc, body = wfd._air_letter({"letter": "Q"})
        self.assertEqual(rc, 404)
        self.assertIn("AIR-Q", body["error"])
        self.assertTrue(wfd._ACT_Q.empty())

    def test_air_letter_missing_400s(self):
        rc, _ = wfd._air_letter({})
        self.assertEqual(rc, 400)
        self.assertTrue(wfd._ACT_Q.empty())

    def test_scroll_direction_and_notches(self):
        rc, body = wfd._air_scroll({"dy": -3})
        self.assertEqual(rc, 202)
        self.assertEqual(body["scroll"]["direction"], "up")
        self.assertEqual(body["scroll"]["notches"], 3)
        self.assertTrue(wfd._INJECT_Q.get(timeout=1))
        rc, body = wfd._air_scroll({"dy": 0.2, "notches": 99})
        self.assertEqual(body["scroll"]["notches"], 24)  # capped
        self.assertTrue(wfd._INJECT_Q.get(timeout=1))

    def test_scroll_bad_input_400s(self):
        rc, _ = wfd._air_scroll({"dy": "abc"})
        self.assertEqual(rc, 400)

    def test_pointer_clamps_and_queues(self):
        rc, body = wfd._air_pointer({"x": 1.5, "y": -2})
        self.assertEqual(rc, 202)
        self.assertEqual((body["pointer"]["x"], body["pointer"]["y"]), (1.0, 0.0))
        self.assertTrue(wfd._INJECT_Q.get(timeout=1))
        rc, _ = wfd._air_pointer({"x": "z"})
        self.assertEqual(rc, 400)

    def test_click_default_and_button_safety(self):
        rc, body = wfd._air_click({})
        self.assertEqual(rc, 202)
        self.assertEqual(body["click"]["button"], 1)
        rc, body = wfd._air_click({"button": 9})
        self.assertEqual(body["click"]["button"], 1)
        self.assertTrue(wfd._INJECT_Q.get(timeout=1))
        self.assertTrue(wfd._INJECT_Q.get(timeout=1))


class TestDaemonHttpSurface(unittest.TestCase):
    """Real daemon: HEAD probes (the app's laptop-connected check must
    not 501), the text/control mode endpoint, and the /status laptop
    descriptor block."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-live-")
        os.makedirs(os.path.join(cls.tmp, ".config", "wf"))
        cls.port = _free_port()
        env = dict(os.environ, HOME=cls.tmp, WF_PORT=str(cls.port),
                   WF_NO_LOG_TEXT="1")
        cls.proc = subprocess.Popen([sys.executable, WFD], env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                if cls.raw("GET", "/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            cls.proc.kill()
            raise RuntimeError("wfd.py did not come up")
        cls.key = open(os.path.join(cls.tmp, ".config", "wf",
                                    "key")).read().strip()

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    @classmethod
    def raw(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    @classmethod
    def req(cls, method, path, body=None, auth=True):
        h = {"X-WF-Key": cls.key} if auth else {}
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        return cls.raw(method, path, body=body, headers=h)

    def test_head_probes(self):
        self.assertEqual(self.raw("HEAD", "/health")[0], 200)
        self.assertEqual(self.raw("HEAD", "/status",
                                  headers={"X-WF-Key": self.key})[0], 200)
        self.assertEqual(self.raw("HEAD", "/status")[0], 403)
        # daemon survived HEADs
        self.assertEqual(self.raw("GET", "/health")[0], 200)

    def test_mode_text_control_roundtrip(self):
        st, _, b = self.req("POST", "/live/mode", body={"mode": "text"})
        self.assertEqual(st, 200)
        j = json.loads(b)
        self.assertEqual(j.get("mode"), "text")
        self.assertTrue(j.get("text_mode"))
        st, _, b = self.req("POST", "/live/mode", body={"mode": "control"})
        self.assertEqual(st, 200)
        j = json.loads(b)
        self.assertEqual(j.get("mode"), "control")
        self.assertFalse(j.get("text_mode"))
        # live stays an accepted alias for control
        st, _, b = self.req("POST", "/live/mode", body={"mode": "live"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(b).get("mode"), "control")

    def test_status_laptop_block(self):
        st, _, b = self.req("GET", "/status")
        self.assertEqual(st, 200)
        lap = json.loads(b).get("laptop") or {}
        self.assertTrue(lap.get("connected"))
        self.assertTrue(lap.get("name"))
        self.assertIn("can_act", lap)
        self.assertIn("signs", lap)


if __name__ == "__main__":
    unittest.main()
