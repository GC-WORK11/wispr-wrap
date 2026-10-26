"""Regression tests for the live-session bugs found on the real stack
(Devin PRO 06:4x IST):

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
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wfd  # noqa: E402
import signbank  # noqa: E402


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
                    seen=0.0, last_accept=None, dup_fires=0)
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


if __name__ == "__main__":
    unittest.main()
