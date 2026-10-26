import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import packs
import signbank as sb
import teach_train


def _seg(amp=0.02, base=0.4, n=10):
    """Synthetic 21-point segment: one hand arcing right."""
    frames = []
    for i in range(n):
        pts = [{"x": base + amp * i + (j * 0.001),
                "y": 0.5 + 0.002 * j, "z": 0, "score": 1}
               for j in range(21)]
        frames.append({"t_ms": i * 66, "points": pts})
    return frames


class TestPacks(unittest.TestCase):
    def test_list_states(self):
        rows = [{"gloss": "HELLO", "n_exemplars": 3,
                 "action": {"kind": "builtin"}}, ]
        lst = {p["id"]: p for p in packs.list_packs(rows)}
        self.assertEqual(lst["hello"]["state"], "ready")
        self.assertEqual(lst["namaste"]["state"], "built-in")
        self.assertEqual(lst["i-am-govinda"]["state"], "needs_takes")

    def test_install_binds_and_refuses(self):
        bound = {}
        code, obj = packs.install_pack(
            "namaste", [], lambda d: (200, d), lambda *a: True)
        self.assertEqual(code, 400)
        code, obj = packs.install_pack(
            "nope", [], lambda d: (200, d), lambda *a: True)
        self.assertEqual(code, 400)

        def bind(data):
            bound.update(data)
            return 200, {"ok": True}
        code, obj = packs.install_pack("hello", [], bind, lambda *a: True)
        self.assertEqual(code, 200)
        self.assertEqual(bound["gloss"], "HELLO")
        self.assertEqual(bound["argv"][-1], "hello")
        self.assertEqual(bound["tier"], 1)
        self.assertIn(obj["state"], ("ready", "needs_takes"))

    def test_install_keeps_existing_binding(self):
        rows = [{"gloss": "I-AM-GOVINDA", "n_exemplars": 5,
                 "action": {"kind": "builtin", "tool": "type.text"}}]
        code, obj = packs.install_pack(
            "i-am-govinda", rows, lambda d: (200, d), lambda *a: True)
        self.assertEqual(code, 200)
        self.assertEqual(obj["source"], "existing")
        self.assertEqual(obj["state"], "ready")


class TestTeachTrain(unittest.TestCase):
    def test_needs_more_under_3(self):
        out = teach_train.verify("X", [_seg(), _seg()])
        self.assertEqual(out["state"], "needs_more")
        self.assertEqual(out["takes"], 2)

    def test_trained_on_consistent_takes(self):
        segs = [_seg(amp=0.02), _seg(amp=0.021), _seg(amp=0.019)]
        out = teach_train.verify("GO-LEFT", segs)
        self.assertEqual(out["checked"], 3)
        self.assertIn(out["state"], ("trained", "recheck"))
        self.assertIsNotNone(out["worst_dist"])
        # consistent takes should self-recognise under the live threshold
        self.assertEqual(out["ok"], 3)
        self.assertEqual(out["state"], "trained")

    def test_recheck_when_a_take_diverges(self):
        good = [_seg(amp=0.02), _seg(amp=0.021)]
        # a take that moves the wrong way entirely
        bad = [{"t_ms": i * 66,
                "points": [{"x": 0.9 - 0.02 * i + j * 0.001,
                            "y": 0.9, "z": 0, "score": 1}
                           for j in range(21)]} for i in range(10)]
        out = teach_train.verify("GO-RIGHT", good + [bad])
        self.assertEqual(out["checked"], 3)
        # at least the divergent take must fail the held-out match
        self.assertLess(out["ok"], out["checked"])
        self.assertEqual(out["state"], "recheck")


if __name__ == "__main__":
    unittest.main()
