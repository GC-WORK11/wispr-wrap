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

    def test_quick_list_has_icon_label_tier(self):
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


class TestMediaFallback(unittest.TestCase):
    """Volume/mute/media fall back to XF86 media keys via xdotool when
    pactl/amixer/playerctl/MPRIS are absent; they refuse honestly only
    when NO backend exists at all."""

    def setUp(self):
        import system_ctl as sc
        self.sc = sc
        self._w, self._r = sc._which, sc._run
        self._xd = os.environ.get("DISPLAY")

    def tearDown(self):
        self.sc._which, self.sc._run = self._w, self._r
        if self._xd is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._xd

    def _fake(self, have_xdotool=True):
        tools = {"xdotool": "/usr/bin/xdotool"} if have_xdotool else {}
        self.sc._which = lambda n: tools.get(n)
        self.sc._run = lambda argv, **kw: (True, "ok")
        os.environ.setdefault("DISPLAY", ":99")

    def test_volume_falls_back_to_xf86(self):
        self._fake()
        ok, det = self.sc._volume("up")
        self.assertTrue(ok)
        self.assertIn("XF86AudioRaiseVolume", det)
        ok, det = self.sc._volume("down")
        self.assertTrue(ok)
        self.assertIn("XF86AudioLowerVolume", det)

    def test_mute_falls_back_to_xf86(self):
        self._fake()
        ok, det = self.sc._mute()
        self.assertTrue(ok)
        self.assertIn("XF86AudioMute", det)

    def test_media_falls_back_to_xf86(self):
        self._fake()
        ok, det = self.sc._media("playpause")
        self.assertTrue(ok)
        self.assertIn("XF86AudioPlay", det)
        ok, det = self.sc._media("next")
        self.assertTrue(ok)
        self.assertIn("XF86AudioNext", det)

    def test_honest_refusal_with_no_backend(self):
        self._fake(have_xdotool=False)
        self.assertFalse(self.sc._volume("up")[0])
        self.assertFalse(self.sc._mute()[0])
        self.assertFalse(self.sc._media("playpause")[0])


class _FocusStub(ai.ActionProvider):
    """A provider whose focused window is whatever the test sets."""

    def __init__(self, wm_class="Xfce4-terminal", instance="xfce4-terminal",
                 title="agent terminal"):
        self._f = ai.FocusInfo(wid=1, wm_class=wm_class,
                               instance=instance, title=title, pid=1)

    def focused_app(self):
        return self._f


class TestTypeAllowGuard(unittest.TestCase):
    """WF_TYPE_ALLOW pins guarded actions to a scratch window: guarded
    ops can never land in a foreign window."""

    def setUp(self):
        self._old = os.environ.get("WF_TYPE_ALLOW")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("WF_TYPE_ALLOW", None)
        else:
            os.environ["WF_TYPE_ALLOW"] = self._old

    def test_allowlist_refuses_agent_terminal(self):
        os.environ["WF_TYPE_ALLOW"] = r"signflow-(sink|term)"
        p = _FocusStub(title="agent terminal")
        self.assertFalse(p.verify_focus())

    def test_allowlist_passes_scratch_sink_title(self):
        os.environ["WF_TYPE_ALLOW"] = r"signflow-(sink|term)"
        p = _FocusStub(title="signflow-sink")
        self.assertTrue(p.verify_focus())

    def test_allowlist_matches_class_or_instance_too(self):
        os.environ["WF_TYPE_ALLOW"] = r"sink-app"
        p = _FocusStub(wm_class="Sink-app", title="untitled")
        self.assertTrue(p.verify_focus())

    def test_unset_allow_behaves_as_before(self):
        os.environ.pop("WF_TYPE_ALLOW", None)
        p = _FocusStub(title="agent terminal")
        self.assertTrue(p.verify_focus())

    def test_title_only_window_matches_allowlist(self):
        # Chrome headless and some toolkits report no WM_CLASS — the
        # allowlist still matches on the title.
        os.environ["WF_TYPE_ALLOW"] = r"chrome"
        p = _FocusStub(wm_class="", instance="",
                       title="Google Chrome and ChromeOS Terms")
        self.assertTrue(p.verify_focus())

    def test_bad_pattern_refuses_safe(self):
        os.environ["WF_TYPE_ALLOW"] = r"(["
        p = _FocusStub(title="signflow-sink")
        self.assertFalse(p.verify_focus())
