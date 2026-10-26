#!/usr/bin/env python3
"""Endpointer regression tests — palm listen-mode must not fire on a
resting open hand that was already in view when the stream started.

Measured on real held-out benchmark keypoints (one signer, 320 clips): the old gate fired
PALM-OPEN on 155/320 clips, 125 of them in the leading rest pad before the
sign. A palm now has to be PRESENTED: the hand enters view after the
stream began, or closes past rearm and opens again while idle (see
Endpointer._mode_events).

Run: python3 tests/test_endpointer.py (from server/) or python3 -m unittest
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import endpointer as ep  # noqa: E402

DT = 66.7            # ms per frame, the real 15 Hz wire cadence


def hand(tip=0.18, curl=1.0, cx=0.5, cy=0.5):
    """Synthetic 21-pt hand. tip 0.18 + curl 1.0 = flat open palm
    (openness ~2.4 > open_ratio); curl 0.2 = fist (~0.9 < rearm)."""
    p = [[cx, cy, 0.9] for _ in range(21)]
    p[ep.MID_MCP] = [cx, cy + 0.08, 0.9]
    for i, t in enumerate(ep.TIPS):
        p[t] = [cx + (i - 2) * 0.05, cy - tip * curl, 0.9]
    return p


def frame(t_ms, pts):
    return {"t_ms": int(t_ms), "points": pts}


class PalmPresentation(unittest.TestCase):
    def setUp(self):
        self.ep = ep.Endpointer(palm_sign="PALM-OPEN", namaste_sign="NAMASTE")
        self.t = 0.0

    def feed(self, pts, n):
        out = []
        for _ in range(n):
            self.t += DT
            out.extend(self.ep.feed(frame(self.t, pts)))
        return out

    def palms(self, events):
        return [e for e in events
                if e.get("type") == "mode" and e.get("name") == "PALM-OPEN"]

    def test_resting_open_hand_at_stream_start_does_not_fire(self):
        # the exact false positive: open palm already present at t=0
        ev = self.feed(hand(), 40)
        self.assertEqual(self.palms(ev), [])

    def test_hand_entering_view_fires_once(self):
        self.feed([], 5)                       # no hand: stream began
        ev = self.feed(hand(), 20)             # enters and holds
        self.assertEqual(len(self.palms(ev)), 1)

    def test_absent_then_present_rearms(self):
        self.feed([], 5)
        first = self.feed(hand(), 20)
        self.assertEqual(len(self.palms(first)), 1)
        self.feed([], 5)                       # leaves view
        second = self.feed(hand(), 20)         # re-enters
        self.assertEqual(len(self.palms(second)), 1)

    def test_close_then_open_rearms_while_idle(self):
        self.feed([], 5)
        self.feed(hand(), 20)                  # first presentation
        self.feed(hand(curl=0.2), 5)           # closed -> re-arm
        ev = self.feed(hand(), 20)
        self.assertEqual(len(self.palms(ev)), 1)

    def test_no_repeat_while_held(self):
        self.feed([], 5)
        ev = self.feed(hand(), 60)             # held open a long time
        self.assertEqual(len(self.palms(ev)), 1)

    def test_motion_then_quiet_still_emits_one_segment(self):
        # segmenter unchanged by the mode gate: move, then hold still
        self.feed([], 2)
        out = []
        for k in range(15):
            out.extend(self.feed(hand(cx=0.30 + k * 0.02), 1))
        out.extend(self.feed(hand(cx=0.58), 15))
        segs = [e for e in out if e.get("type") == "segment"]
        self.assertEqual(len(segs), 1, out)
        self.assertGreaterEqual(len(segs[0]["frames"]), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
