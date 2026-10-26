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
        self.assertEqual(lst["intro"]["state"], "needs_takes")

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
        rows = [{"gloss": "INTRO", "n_exemplars": 5,
                 "action": {"kind": "builtin", "tool": "type.text"}}]
        code, obj = packs.install_pack(
            "intro", rows, lambda d: (200, d), lambda *a: True)
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


class TestPackTeachChain(unittest.TestCase):
    """The pack install -> user-takes -> bound-sign chain: install writes
    the argv into commands.tsv; teach_begin must pick that binding up so
    the user's recorded takes attach to it."""

    def test_teach_begin_picks_up_pack_binding(self):
        import wfd
        rows = {"HELLO": {"label": "Hello",
                          "argv": ["xdotool", "type", "--", "hello"],
                          "raw": "xdotool type -- hello", "tier": 1}}
        old = wfd.commands
        try:
            wfd.commands = lambda: rows
            code, obj = wfd.teach_begin({"name": "hello", "takes": 3})
            self.assertEqual(code, 200, obj)
            act = wfd.TEACH["action"]
            self.assertIsNotNone(act)
            self.assertEqual(act["argv"], ["xdotool", "type", "--", "hello"])
            self.assertEqual(act["tier"], 1)
        finally:
            wfd.commands = old
            wfd.TEACH.update(active=False, name="", takes=0, got=0,
                             label="", action=None, session=None,
                             frames=[])


class TestDictActionChain(unittest.TestCase):
    """SITE's quick-grid binds signs to {kind:"builtin", tool:"win.alt-tab"}
    style actions. The daemon must (a) store the dict verbatim through
    teach_begin -> add_exemplar and (b) resolve it to a real backend
    action at fire time via act_iface.normalize_action."""

    def test_dict_action_normalizes_to_backend_tool(self):
        import act_iface
        for tool, want in (("win.alt-tab", "alt_tab"),
                           ("win.maximize", "maximize"),
                           ("desktop.show", "show_desktop"),
                           ("nav.scroll-up", "scroll"),
                           ("media.playpause", "media")):
            a = act_iface.normalize_action(
                {"kind": "builtin", "tool": tool, "label": tool})
            self.assertIsNotNone(a, tool)
            self.assertEqual(a["type"], want, tool)

    def test_teach_begin_stores_dict_action(self):
        import wfd
        act = {"kind": "builtin", "tool": "win.alt-tab",
               "label": "Switch app"}
        try:
            code, obj = wfd.teach_begin({"name": "MY-SWITCH",
                                         "action": act, "takes": 3})
            self.assertEqual(code, 200, obj)
            self.assertEqual(wfd.TEACH["action"], act)
        finally:
            wfd.TEACH.update(active=False, name="", takes=0, got=0,
                             label="", action=None, session=None,
                             frames=[])

    def test_reserved_signs_refuse_teach(self):
        import wfd
        for name in ("PALM-OPEN", "NAMASTE", "palm open"):
            code, obj = wfd.teach_begin({"name": name})
            self.assertEqual(code, 400, (name, obj))
            self.assertIn("reserved", obj.get("error", ""))


class TestModeDispatch(unittest.TestCase):
    """Demo-critical split: a sign bound to a laptop action fires it in
    Control mode and types its label in Text mode. Same match object,
    only LIVE['text_mode']/['observe'] differ."""

    class _M:
        def __init__(self, gloss, label, action):
            self.gloss, self.label, self.action = gloss, label, action
            self.dist, self.accepted, self.tier = 0.01, True, 1
            self.score, self.margin = 1.0, 1.5
            self.second_gloss, self.reason = "OTHER", "ok"

    class _Bank:
        m = None
        @classmethod
        def match(cls, frames):
            return cls.m

    def _run(self, frames, **live):
        import wfd
        saved = {k: LIVEV for k, LIVEV in
                 ((k, getattr(wfd, k)) for k in ("BANK", "OPS"))}
        live_saved = dict(wfd.LIVE)
        try:
            wfd.BANK = self._Bank
            wfd.OPS = None
            wfd.LIVE.update(listening=True, observe=False,
                            text_mode=False, last_accept=None,
                            last_match=None, session="t")
            wfd.LIVE.update(live)
            return wfd._on_segment(frames, 12345)
        finally:
            for k, v in saved.items():
                setattr(wfd, k, v)
            wfd.LIVE.update(live_saved)

    def setUp(self):
        self._Bank.m = self._M("MY-SWITCH", "Switch app",
                               {"kind": "builtin", "tool": "win.alt-tab"})
        self.frames = [{"t_ms": 12000, "points": []},
                       {"t_ms": 12200, "points": []}]

    def test_text_mode_types_label_not_action(self):
        res = self._run(self.frames, text_mode=True)
        self.assertEqual(res["action"]["kind"], "text")
        self.assertEqual(res["action"]["typed"], "Switch app")
        self.assertTrue(res["action"]["dispatched"])

    def test_control_mode_routes_to_provider(self):
        res = self._run(self.frames, text_mode=False)
        a = res["action"]
        self.assertNotEqual(a.get("kind"), "text")
        # no provider in the test env: the refusal proves the dispatch
        # path was taken (routed attempt), not the text path.
        self.assertIn("routed", a)

    def test_observe_reports_never_fires(self):
        res = self._run(self.frames, observe=True)
        self.assertTrue(res["action"]["observe"])
        self.assertTrue(res["action"]["would_fire"])
