import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import act_iface as ai
import act_platform as ap


def prov(plat, **kw):
    return ap.CrossPlatformProvider(platform=plat, **kw)


class TestHostPlatform(unittest.TestCase):
    def test_returns_a_known_name(self):
        self.assertIn(ap.host_platform(),
                      ("linux", "macos", "windows", "other"))
        self.assertEqual(ap.host_platform(),
                         "linux" if sys.platform.startswith("linux")
                         else ap.host_platform())


class TestKeyTranslation(unittest.TestCase):
    def test_mac_keystroke_mods(self):
        self.assertEqual(ap._mac_keystroke("ctrl+t"),
                         'keystroke "t" using {control down}')
        self.assertEqual(ap._mac_keystroke("cmd+c"),
                         'keystroke "c" using {command down}')
        self.assertEqual(ap._mac_keystroke("ctrl+shift+z"),
                         'keystroke "z" using {control down, shift down}')

    def test_mac_keystroke_named_keys(self):
        self.assertEqual(ap._mac_keystroke("Return"), "key code 36")
        self.assertEqual(ap._mac_keystroke("alt+tab"),
                         "key code 48 using {option down}")

    def test_win_key_combo(self):
        self.assertEqual(ap._win_key_combo("ctrl+t"), "^t")
        self.assertEqual(ap._win_key_combo("alt+tab"), "%{TAB}")
        self.assertEqual(ap._win_key_combo("Return"), "{ENTER}")
        self.assertEqual(ap._win_key_combo("ctrl+shift+z"), "^+z")
        self.assertIsNone(ap._win_key_combo("super+l"))

    def test_sendkeys_escapes_specials(self):
        self.assertEqual(ap._sendkeys_escape("a+b%"), "a{+}b{%}")
        self.assertEqual(ap._sendkeys_escape("(x)"), "{(}x{)}")


class _FakeMac(ap.CrossPlatformProvider):
    def __init__(self, **kw):
        self.calls = []
        super().__init__(platform="macos", **kw)
    def _keys_supported(self):
        return True
    def _type(self, text):
        self.calls.append(("type", text))
        return True, "typed"
    def _press(self, keys):
        self.calls.append(("press", keys))
        return True, "keys"
    def focused_app(self):
        return ai.FocusInfo(wm_class="ScratchEdit", title="scratch")


class _FakeWin(_FakeMac):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.platform = "windows"
        self.name = "windows"


class TestGuardedKeystrokes(unittest.TestCase):
    def test_type_text_types_through_macos(self):
        p = _FakeMac()
        r = p.run({"type": "type_text", "text": "hello"})
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(p.calls, [("type", "hello")])

    def test_key_press_ctrl_t_macos(self):
        p = _FakeMac()
        r = p.run({"type": "key_press", "keys": "ctrl+t"})
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(p.calls, [("press", "ctrl+t")])

    def test_catalog_ids_normalize_to_keystrokes(self):
        p = _FakeMac()
        for tid, keys in (("browser.new-tab", "ctrl+t"),
                          ("edit.copy", "ctrl+c"),
                          ("edit.paste", "ctrl+v"),
                          ("key.enter", "Return")):
            p.calls = []
            r = p.run({"tool": tid})
            self.assertTrue(r.ok, "%s -> %s" % (tid, r.detail))
            self.assertEqual(p.calls, [("press", keys)])

    def test_alt_tab_fires_on_windows(self):
        p = _FakeWin()
        r = p.run({"type": "alt_tab"})
        self.assertTrue(r.ok, r.detail)

    def test_focus_guard_refuses_foreign_window(self):
        os.environ["WF_TYPE_ALLOW"] = r"scratch-(sink|term)"
        try:
            p = _FakeMac()
            r = p.run({"type": "type_text", "text": "LEAK"})
            self.assertFalse(r.ok)
            self.assertIn("focus-refused", r.detail)
        finally:
            os.environ.pop("WF_TYPE_ALLOW", None)

    def test_focus_guard_passes_allowlisted(self):
        os.environ["WF_TYPE_ALLOW"] = r"Scratch"
        try:
            p = _FakeMac()
            r = p.run({"type": "type_text", "text": "ok"})
            self.assertTrue(r.ok, r.detail)
        finally:
            os.environ.pop("WF_TYPE_ALLOW", None)

    def test_focus_none_refuses(self):
        class P(_FakeMac):
            def focused_app(self):
                return None
        p = P()
        r = p.run({"type": "type_text", "text": "x"})
        self.assertFalse(r.ok)
        self.assertIn("focus", r.detail)


class TestUnsupported(unittest.TestCase):
    def test_scroll_refused_cleanly(self):
        p = prov("macos")
        r = p.run({"type": "scroll", "dir": "up"})
        self.assertFalse(r.ok)
        self.assertEqual(r.detail, "scroll: not supported on this platform")

    def test_workspace_ops_refused(self):
        p = prov("windows")
        for t in ("workspace", "move_workspace", "tile", "move_monitor"):
            r = p.run({"type": t})
            self.assertFalse(r.ok, t)
            self.assertIn("not supported", r.detail)

    def test_macos_maximize_is_a_clean_refusal(self):
        class P(_FakeMac):
            def focused_app(self):
                return ai.FocusInfo(wm_class="x")
        p = P()
        r = p.run({"type": "maximize"})
        self.assertFalse(r.ok)
        self.assertIn("not supported", r.detail)

    def test_unknown_tool_no_keystroke_backend(self):
        class NoKeys(ap.CrossPlatformProvider):
            def _keys_supported(self):
                return False
        p = NoKeys(platform="macos")
        r = p.run({"type": "type_text", "text": "x"})
        self.assertFalse(r.ok)
        self.assertIn("not supported", r.detail)


class TestApps(unittest.TestCase):
    def test_macos_list_apps_scans_app_dirs(self):
        p = prov("macos")
        real = os.listdir
        def fake(d):
            if d == "/Applications":
                return ["Safari.app", "Notes.txt", "Utilities"]
            raise OSError("nope")
        with mock.patch.object(os, "listdir", side_effect=fake):
            apps = p.list_apps()
        names = [a.name for a in apps]
        self.assertEqual(names, ["Safari"])
        self.assertEqual(apps[0].argv, ["open", "-a", "Safari"])
        self.assertEqual(apps[0].id, "Safari")

    def test_macos_list_apps_absent_dirs_empty(self):
        p = prov("macos")
        with mock.patch.object(os, "listdir", side_effect=OSError):
            self.assertEqual(p.list_apps(), [])

    def test_windows_list_apps_parses_startapps(self):
        p = prov("windows")
        out = "Microsoft Edge\tMicrosoft.MicrosoftEdge_8wekyb3d8bbwe!MicrosoftEdge\nNotepad\tMicrosoft.WindowsNotepad_8wekyb3d8bbwe!App\n"
        with mock.patch.object(ap, "_ps", return_value=(True, out)):
            apps = p.list_apps()
        self.assertEqual([a.name for a in apps],
                         ["Microsoft Edge", "Notepad"])
        self.assertTrue(apps[0].argv[1].startswith("shell:AppsFolder\\"))

    def test_open_app_macos_builds_open_a(self):
        p = prov("macos")
        called = {}
        with mock.patch.object(ap, "_run",
                               return_value=(True, "")) as m:
            r = p.run({"type": "open_app", "id": "Safari"})
            # first use of `open` may pend for confirm (approval persists
            # in ~/.config/wf/approved.json) — confirm when it does
            r2 = p.confirm(r.confirm_id) if r.confirm_id else r
            called["argv"] = m.call_args[0][0]
        self.assertTrue(r2.ok, r2.detail)
        self.assertEqual(called["argv"], ["open", "-a", "Safari"])

    def test_open_url_macos(self):
        p = prov("macos")
        with mock.patch.object(ap, "_run",
                               return_value=(True, "")) as m:
            r = p.run({"type": "open_url", "url": "https://x.example"})
            r2 = p.confirm(r.confirm_id) if r.confirm_id else r
        self.assertTrue(r2.ok, r2.detail)
        self.assertEqual(m.call_args[0][0], ["open", "https://x.example"])


class TestFocusReport(unittest.TestCase):
    def test_macos_focus_parses_frontmost(self):
        p = prov("macos")
        with mock.patch.object(ap, "_osa",
                               return_value=(True, "Safari\n")):
            f = p.focused_app()
        self.assertIsNotNone(f)
        self.assertEqual(f.wm_class, "Safari")

    def test_macos_focus_none_on_probe_failure(self):
        p = prov("macos")
        with mock.patch.object(ap, "_osa", return_value=(False, "")):
            self.assertIsNone(p.focused_app())

    def test_windows_focus_parses_tab_row(self):
        p = prov("windows")
        with mock.patch.object(ap, "_ps",
                               return_value=(True, "notepad\ta.txt - Notepad")):
            f = p.focused_app()
        self.assertEqual(f.wm_class, "notepad")
        self.assertEqual(f.title, "a.txt - Notepad")


class TestCapabilities(unittest.TestCase):
    def test_macos_catalog_shape(self):
        p = prov("macos")
        c = p.catalog()
        self.assertEqual(c["provider"], "macos")
        self.assertEqual(c["capabilities"]["display"], "macos")
        self.assertFalse(c["capabilities"]["features"]["icons"])
        ids = [r["id"] for r in c["tools"]]
        for want in ("app.open", "win.alt-tab", "edit.copy",
                     "key.enter", "type.text"):
            self.assertIn(want, ids)


if __name__ == "__main__":
    unittest.main()
