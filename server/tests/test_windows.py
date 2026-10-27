"""Windows backend unit tests — every OS-touching call is mocked; these
run on POSIX CI exactly like the macOS tests do."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import act_iface as ai
import act_platform as ap
import system_ctl as sc


def prov(plat, **kw):
    return ap.CrossPlatformProvider(platform=plat, **kw)


# ------------------------------------------------------------------
# act_platform: SendKeys text + keystroke plumbing
# ------------------------------------------------------------------

class TestSendKeysText(unittest.TestCase):
    def test_newlines_become_enter(self):
        self.assertEqual(ap._sendkeys_text("a\nb"), "a{ENTER}b")
        self.assertEqual(ap._sendkeys_text("a\r\nb\rc"), "a{ENTER}b{ENTER}c")

    def test_specials_still_escaped(self):
        self.assertEqual(ap._sendkeys_text("a+b{c}"), "a{+}b{{}c{}}")

    def test_windows_type_sends_sendkeys(self):
        p = prov("windows")
        with mock.patch.object(ap, "_ps", return_value=(True, "ok")) as m:
            ok, det = p._type("hi\nthere")
        self.assertTrue(ok, det)
        script = m.call_args[0][0]
        self.assertIn("SendKeys", script)
        self.assertIn("hi{ENTER}there", script)


class TestWin32Plumbing(unittest.TestCase):
    def test_is_hwnd(self):
        self.assertTrue(ap._is_hwnd("a1b2c3"))
        self.assertTrue(ap._is_hwnd("0x000a01f2"))
        self.assertFalse(ap._is_hwnd("notepad"))
        self.assertFalse(ap._is_hwnd("12345678901234567"))
        self.assertFalse(ap._is_hwnd(""))

    def test_hwnd_script_validates_and_runs(self):
        s = ap._hwnd_script("a1b2", "echo hi")
        self.assertIn('ToInt64("a1b2", 16)', s)
        self.assertIn("[WFU]::IsWindow($h)", s)
        self.assertIn("echo hi", s)
        self.assertIn("EnumWindows", s)        # the C# block is embedded

    def test_hwnd_focus_sequence(self):
        self.assertIn("keybd_event(0x12", ap._HWND_FOCUS)      # alt tap
        self.assertIn("SetForegroundWindow($h)", ap._HWND_FOCUS)
        self.assertIn("ShowWindowAsync($h, 9)", ap._HWND_FOCUS)  # un-minimize

    def test_ps_argv_adds_sta(self):
        calls = []

        def fake_run(argv, timeout=None):
            calls.append(argv)
            return True, "ok"
        with mock.patch.object(ap, "_which",
                               return_value="/fake/powershell"), \
             mock.patch.object(ap, "_run", side_effect=fake_run):
            ap._ps("echo x")
            ap._ps("echo y", sta=True)
        self.assertNotIn("-Sta", calls[0])
        self.assertIn("-Sta", calls[1])
        self.assertIn("-Command", calls[0])


class TestWindowsListWindows(unittest.TestCase):
    def test_enum_windows_rows_parsed(self):
        p = prov("windows")
        out = ("a1b2c\tnotepad\ta.txt - Notepad\n"
               "d4e5f\texplorer\tFile Explorer\n"
               "bad-row-no-tabs\n")
        with mock.patch.object(ap, "_ps", return_value=(True, out)) as m:
            rows = p.list_windows()
        self.assertIn("EnumWindows", m.call_args[0][0])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0],
                         {"id": "a1b2c", "app": "notepad",
                          "title": "a.txt - Notepad"})
        self.assertEqual(rows[1]["id"], "d4e5f")

    def test_catalog_exposes_windows(self):
        p = prov("windows")
        with mock.patch.object(ap, "_ps", return_value=(True,
                                                        "aa\tnp\ttitle")):
            cat = p.catalog()
        self.assertEqual(cat["windows"][0]["id"], "aa")
        self.assertEqual(cat["capabilities"]["provider"], "windows")


class TestWindowsFocusClose(unittest.TestCase):
    def test_focus_by_hwnd_uses_win32(self):
        class P(ap.CrossPlatformProvider):
            def focused_app(self):
                return ai.FocusInfo(wm_class="x")
        p = P(platform="windows")
        with mock.patch.object(ap, "_ps", return_value=(True, "ok")) as m:
            r = p.run({"type": "focus_app", "app": "a1b2c"})
        self.assertTrue(r.ok, r.detail)
        self.assertIn("SetForegroundWindow", m.call_args[0][0])
        self.assertIn('ToInt64("a1b2c", 16)', m.call_args[0][0])

    def test_focus_by_name_uses_appactivate(self):
        class P(ap.CrossPlatformProvider):
            def focused_app(self):
                return ai.FocusInfo(wm_class="x")
        p = P(platform="windows")
        with mock.patch.object(ap, "_ps", return_value=(True, "ok")) as m:
            r = p.run({"type": "focus_app", "app": "Notepad"})
        self.assertTrue(r.ok, r.detail)
        self.assertIn("AppActivate(''Notepad'')".replace("''", "'"),
                      m.call_args[0][0])

    def test_close_by_hwnd_posts_wm_close(self):
        p = prov("windows")
        with mock.patch.object(ap, "_ps", return_value=(True, "sent")) as m:
            r = p.run({"type": "close_window", "app": "dead9"})
            r = p.confirm(r.confirm_id) if r.confirm_id else r
        self.assertTrue(r.ok, r.detail)
        self.assertIn("PostMessageW($h, 0x10", m.call_args[0][0])

    def test_close_by_name_uses_closemainwindow(self):
        p = prov("windows")
        with mock.patch.object(ap, "_ps", return_value=(True, "")) as m:
            r = p.run({"type": "close_window", "app": "Notepad"})
            r = p.confirm(r.confirm_id) if r.confirm_id else r
        self.assertIn("CloseMainWindow", m.call_args[0][0])


# ------------------------------------------------------------------
# act_platform: clipboard module functions
# ------------------------------------------------------------------

class TestClipboardHelpers(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(ap, "host_platform",
                                    return_value="windows")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clip_get_reads_raw(self):
        with mock.patch.object(ap, "_ps",
                               return_value=(True, "hello")) as m:
            self.assertEqual(ap.clip_get(), "hello")
        self.assertIn("Get-Clipboard -Raw", m.call_args[0][0])

    def test_clip_get_none_on_failure(self):
        with mock.patch.object(ap, "_ps", return_value=(False, "x")):
            self.assertIsNone(ap.clip_get())

    def test_clip_set_text_quotes(self):
        with mock.patch.object(ap, "_ps",
                               return_value=(True, "ok")) as m:
            ok, _ = ap.clip_set_text("it's a\nline")
        self.assertTrue(ok)
        self.assertIn("Set-Clipboard -Value 'it''s a\nline'",
                      m.call_args[0][0])

    def test_clip_set_image_uses_sta(self):
        with mock.patch.object(ap, "_ps",
                               return_value=(True, "ok")) as m:
            ok, _ = ap.clip_set_image("C:\\img\\p.png")
        self.assertTrue(ok)
        self.assertEqual(m.call_args[1].get("sta"), True)
        self.assertIn("SetImage", m.call_args[0][0])

    def test_clip_set_files_drop_list(self):
        with mock.patch.object(ap, "_ps",
                               return_value=(True, "ok")) as m:
            ok, _ = ap.clip_set_files(["C:\\a.txt"])
        self.assertTrue(ok)
        self.assertIn("SetFileDropList", m.call_args[0][0])
        self.assertEqual(m.call_args[1].get("sta"), True)


# ------------------------------------------------------------------
# wfd: platform switches in the composer paths
# ------------------------------------------------------------------

import wfd


class TestWfdPlatformSwitch(unittest.TestCase):
    def test_host_windows_when_provider_says_so(self):
        with mock.patch.object(ap, "host_platform",
                               return_value="windows"):
            self.assertEqual(wfd._host(), "windows")

    def test_node_name_falls_back_without_uname(self):
        real = getattr(os, "uname", None)
        try:
            del os.uname
            self.assertTrue(wfd.node_name())
        finally:
            if real is not None:
                os.uname = real

    def test_all_ips_windows_parses_netip(self):
        out = "192.168.1.5\tWi-Fi\n10.0.75.2\tvEthernet (WSL)\n"
        with mock.patch.object(ap, "host_platform",
                               return_value="windows"), \
             mock.patch.object(ap, "_ps",
                               return_value=(True, out)) as m:
            ips = wfd.all_ips()
        self.assertIn("Get-NetIPAddress", m.call_args[0][0])
        self.assertEqual(ips[0], "192.168.1.5")   # wifi first
        self.assertIn("10.0.75.2", ips)

    def test_active_window_class_via_provider(self):
        fake = mock.Mock()
        fake.focused_app.return_value = ai.FocusInfo(wm_class="Code",
                                                     title="f.py")
        with mock.patch.object(ap, "host_platform",
                               return_value="windows"), \
             mock.patch.object(wfd, "ACT_PROVIDER", fake):
            self.assertEqual(wfd.active_window_class(), "Code")

    def test_paste_keys_terminal_chord(self):
        with mock.patch.object(ap, "host_platform",
                               return_value="windows"):
            self.assertEqual(wfd._paste_keys("windowsterminal"),
                             "ctrl+shift+v")
            self.assertEqual(wfd._paste_keys("notepad"), "ctrl+v")

    def test_paste_keys_macos_chord(self):
        with mock.patch.object(ap, "host_platform",
                               return_value="macos"):
            self.assertEqual(wfd._paste_keys("x"), "cmd+v")

    def test_inject_keystrokes_uses_provider_off_linux(self):
        fake = mock.Mock()
        fake._type.return_value = (True, "typed")
        with mock.patch.object(ap, "host_platform",
                               return_value="windows"), \
             mock.patch.object(wfd, "ACT_PROVIDER", fake):
            ok, _ = wfd._inject_keystrokes("hello")
        self.assertTrue(ok)
        fake._type.assert_called_once_with("hello")

    def test_to_png_windows_powershell(self):
        with mock.patch.object(ap, "host_platform",
                               return_value="windows"), \
             mock.patch.object(ap, "_ps",
                               return_value=(True, "")) as m:
            path = wfd._to_png("C:\\tmp\\in.jpg")
        self.assertTrue(path)
        script = m.call_args[0][0]
        self.assertIn("System.Drawing", script)
        self.assertIn("ImageFormat]::Png", script)
        os.unlink(path)

    def test_spawn_detached_flags_on_windows(self):
        captured = {}

        class FakePopen:
            def __init__(self, argv, **kw):
                captured.update(kw)
                self.pid = 1

            def wait(self):
                return 0

        with mock.patch.object(os, "name", "nt"), \
             mock.patch.object(wfd.subprocess, "Popen", FakePopen):
            wfd._spawn(["x"], "t")
        self.assertIn("creationflags", captured)
        self.assertNotIn("start_new_session", captured)


# ------------------------------------------------------------------
# system_ctl: Windows backends
# ------------------------------------------------------------------

class TestSystemCtlWindows(unittest.TestCase):
    def setUp(self):
        self._plat = mock.patch.object(sc.sys, "platform", "win32")
        self._plat.start()
        self.addCleanup(self._plat.stop)
        self._ps = mock.patch.object(sc, "_ps",
                                     return_value=(True, "ok"))
        self.ps = self._ps.start()
        self.addCleanup(self._ps.stop)

    def test_volume_up_key(self):
        ok, det = sc._volume("up", step=10)
        self.assertTrue(ok, det)
        self.assertIn("0xaf", self.ps.call_args[0][0])

    def test_volume_down_key(self):
        ok, det = sc._volume("down")
        self.assertTrue(ok, det)
        self.assertIn("0xae", self.ps.call_args[0][0])

    def test_mute_key(self):
        ok, _ = sc._mute()
        self.assertTrue(ok)
        self.assertIn("0xad", self.ps.call_args[0][0])

    def test_media_keys(self):
        for sub, vk in (("playpause", "0xb3"), ("next", "0xb0"),
                        ("prev", "0xb1")):
            ok, _ = sc._media(sub)
            self.assertTrue(ok, sub)
            self.assertIn(vk, self.ps.call_args[0][0])

    def test_brightness_wmi(self):
        ok, det = sc._brightness("up")
        self.assertTrue(ok, det)
        script = self.ps.call_args[0][0]
        self.assertIn("WmiMonitorBrightnessMethods", script)
        self.assertIn("WmiSetBrightness", script)

    def test_brightness_down_negative(self):
        ok, _ = sc._brightness("down", step=15)
        self.assertTrue(ok)
        self.assertIn("- 15", self.ps.call_args[0][0])

    def test_dnd_registry(self):
        ok, det = sc._dnd("on")
        self.assertTrue(ok, det)
        script = self.ps.call_args[0][0]
        self.assertIn("NOC_GLOBAL_SETTING_TOASTS_ENABLED", script)
        self.assertIn("$new = 0", script)

    def test_dnd_toggle_flips(self):
        ok, _ = sc._dnd(None)
        self.assertTrue(ok)
        self.assertIn("$new = if ($cur -eq 0)", self.ps.call_args[0][0])

    def test_wifi_net_adapter(self):
        ok, det = sc._wifi("off")
        self.assertTrue(ok, det)
        script = self.ps.call_args[0][0]
        self.assertIn("Get-NetAdapter", script)
        self.assertIn("Disable-NetAdapter", script)

    def test_bluetooth_pnp(self):
        ok, det = sc._bluetooth("off")
        self.assertTrue(ok, det)
        script = self.ps.call_args[0][0]
        self.assertIn("Get-PnpDevice", script)
        self.assertIn("Disable-PnpDevice", script)

    def test_clip_read_index_zero(self):
        ok, det = sc._clip_read_index(0)
        self.assertTrue(ok, det)
        self.assertIn("Get-Clipboard -Raw", self.ps.call_args[0][0])

    def test_clip_read_index_nonzero_refused(self):
        ok, det = sc._clip_read_index(2)
        self.assertFalse(ok)
        self.assertIn("no clipboard history provider", det)

    def test_targets_via_forms(self):
        self.ps.return_value = (True, "Text,FileDrop")
        self.ps.reset_mock()
        tgts = sc._clip_targets()
        self.assertEqual(tgts, ["Text", "FileDrop"])
        self.assertIn("GetFormats", self.ps.call_args[0][0])
        self.assertTrue(self.ps.call_args[1].get("sta"))

    def test_lock_uses_rundll32(self):
        with mock.patch.object(sc, "_which",
                               side_effect=lambda n:
                               "/win/rundll32" if "rundll32" in n else None), \
             mock.patch.object(sc, "_run",
                               return_value=(True, "ok")) as m:
            ok, _ = sc._lock()
        self.assertTrue(ok)
        self.assertIn("LockWorkStation", m.call_args[0][0][1])

    def test_dispatch_routes_on_windows(self):
        ok, det = sc.dispatch("sys.mute")
        self.assertTrue(ok, det)


if __name__ == "__main__":
    unittest.main(verbosity=2)
