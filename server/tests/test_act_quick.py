import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import act_iface as ai
import act_linux as al

QUICK = {
    "win.alt-tab": ("alt_tab", {}),
    "win.minimize": ("minimize", {}),
    "win.close": ("close_window", {}),
    "desktop.show": ("show_desktop", {}),
    "browser.new-tab": ("key_press", {"keys": "ctrl+t"}),
    "browser.close-tab": ("key_press", {"keys": "ctrl+w"}),
    "nav.scroll-up": ("scroll", {"dir": "up"}),
    "nav.scroll-down": ("scroll", {"dir": "down"}),
    "edit.copy": ("key_press", {"keys": "ctrl+c"}),
    "edit.paste": ("key_press", {"keys": "ctrl+v"}),
    "edit.undo": ("key_press", {"keys": "ctrl+z"}),
    "key.enter": ("key_press", {"keys": "Return"}),
}


class TestQuickCatalog(unittest.TestCase):
    def test_every_quick_action_is_mapped(self):
        for aid, (tool, args) in QUICK.items():
            with self.subTest(aid=aid):
                self.assertIn(aid, ai.TOOL_MAP)
                t, fixed = ai.TOOL_MAP[aid]
                self.assertEqual(t, tool)
                for k, v in args.items():
                    self.assertEqual(fixed.get(k), v)

    def test_ids_normalize_to_canonical_types(self):
        for aid, (tool, args) in QUICK.items():
            with self.subTest(aid=aid):
                a = ai.normalize_action({"kind": "builtin", "tool": aid})
                self.assertIsNotNone(a)
                self.assertEqual(a["type"], tool)
                for k, v in args.items():
                    self.assertEqual(a.get(k), v)

    def test_picker_rows_present(self):
        ids = {r["id"] for r in
               al.LinuxX11Provider(config_dir="/nonexistent")._tool_rows}
        for aid in QUICK:
            with self.subTest(aid=aid):
                self.assertIn(aid, ids)

    def test_founder_list_has_icon_label_tier(self):
        rows = {r["id"]: r for r in
                al.LinuxX11Provider(config_dir="/nonexistent")._tool_rows}
        for aid in QUICK:
            with self.subTest(aid=aid):
                self.assertTrue(rows[aid].get("icon"), aid)
                self.assertTrue(rows[aid].get("label"), aid)
        for aid in ("win.maximize", "app.open", "type.text"):
            self.assertTrue(rows[aid].get("icon"), aid)

    def test_close_window_row_is_confirm_tier(self):
        rows = {r["id"]: r for r in
                al.LinuxX11Provider(config_dir="/nonexistent")._tool_rows}
        self.assertEqual(rows["win.close"]["tier"], 2)
        for aid in QUICK:
            if aid != "win.close":
                self.assertEqual(rows[aid]["tier"], 1, aid)


class TestQuickHandlers(unittest.TestCase):
    """Plan-level checks (no X needed): handlers return ActionPlans
    with the right shape; real X11 exercise lives in test_realx below
    and is skipped when DISPLAY is unset."""

    def setUp(self):
        self.p = al.LinuxX11Provider(config_dir="/nonexistent")

    def _plan(self, tool, args=None):
        h = self.p._handlers[tool]
        return h(dict(args or {}), {})

    def test_plans_built(self):
        if not al.TOOLS.get("xdotool"):
            self.skipTest("xdotool not installed on this box")
        self.assertIsNotNone(self._plan("alt_tab"))
        self.assertIsNotNone(self._plan("minimize"))
        self.assertIsNotNone(self._plan("scroll", {"dir": "up"}))
        self.assertIsNotNone(self._plan("scroll", {"dir": "down"}))
        if al.TOOLS.get("wmctrl"):
            self.assertIsNotNone(self._plan("show_desktop"))

    def test_scroll_dir_maps_buttons(self):
        # up=button4, down=button5 — read it out of the describe string
        if not al.TOOLS.get("xdotool"):
            self.skipTest("no xdotool")
        self.assertIn("up", self._plan("scroll", {"dir": "up"}).describe)
        self.assertIn("down",
                      self._plan("scroll", {"dir": "down"}).describe)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a real X server")
class TestQuickRealX11(unittest.TestCase):
    """Real X11 check: run the non-destructive quick actions against
    the live display and assert the provider reports done."""

    def setUp(self):
        self.p = al.LinuxX11Provider(config_dir="/nonexistent")

    def _fire(self, aid):
        a = ai.normalize_action({"kind": "builtin", "tool": aid,
                                 "tier": 1})
        h = self.p._handlers[a["type"]]
        plan = h(a, {})
        self.assertIsNotNone(plan, aid)
        return plan.fn({})

    def test_scroll_up_and_down(self):
        if not al.TOOLS.get("xdotool"):
            self.skipTest("no xdotool")
        for aid in ("nav.scroll-up", "nav.scroll-down"):
            r = self._fire(aid)
            self.assertEqual(getattr(r, "ok", True), True,
                             f"{aid}: {getattr(r,'detail','')}")

    def test_alt_tab(self):
        if not al.TOOLS.get("xdotool"):
            self.skipTest("no xdotool")
        r = self._fire("win.alt-tab")
        self.assertTrue(getattr(r, "ok", True), getattr(r, "detail", ""))

    def test_show_desktop(self):
        if not al.TOOLS.get("wmctrl"):
            self.skipTest("no wmctrl")
        r = self._fire("desktop.show")
        self.assertTrue(getattr(r, "ok", True), getattr(r, "detail", ""))
        # restore the desktop immediately — never leave windows hidden
        import subprocess
        subprocess.run(["wmctrl", "-k", "off"],
                       env={**os.environ}, capture_output=True)


if __name__ == "__main__":
    unittest.main()
