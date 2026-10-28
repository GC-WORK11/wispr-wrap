import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wfd
import act_platform as ap
import system_ctl as sc


class _Darwin(unittest.TestCase):
    """Pin sys.platform to darwin so the macOS branches run on any host;
    every OS command behind them is mocked."""

    def setUp(self):
        self._plat = sys.platform
        sys.platform = "darwin"

    def tearDown(self):
        sys.platform = self._plat


FAKE_IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
	inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
	inet 192.168.1.44 netmask 0xffffff00 broadcast 192.168.1.255
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
	inet 10.8.0.2 --> 10.8.0.1 netmask 0xffffffff
awdl0: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST> mtu 1484
	inet6 fe80::abcd%awdl0 prefixlen 64 scopeid 0xe
bridge100: flags=8a63<UP,BROADCAST,RUNNING> mtu 1500
	inet 10.211.55.2 netmask 0xffffff00 broadcast 10.211.55.255
en7: flags=8863<UP,BROADCAST,SMART,RUNNING> mtu 1500
	inet 169.254.10.1 netmask 0xffff0000
"""


class TestMacAllIps(_Darwin):
    """all_ips() on darwin parses ifconfig, skips tunnels/bridges/link-local,
    en* interfaces first."""

    def test_filters_and_orders(self):
        with mock.patch.object(wfd, "sh",
                               return_value=mock.Mock(stdout=FAKE_IFCONFIG)):
            ips = wfd.all_ips()
        self.assertEqual(ips, ["192.168.1.44"])

    def test_empty_ifconfig_gives_empty(self):
        with mock.patch.object(wfd, "sh", return_value=mock.Mock(stdout="")):
            self.assertEqual(wfd.all_ips(), [])


FAKE_AX_OUT = "664\t1\tSimulator\tiPhone 17 – iOS 26.5\n" \
              "664\t2\tSimulator\tiPhone 17 – iOS 27.0\n" \
              "812\t1\tTextEdit\tUntitled\n"


class TestMacWindows(_Darwin):
    def test_ax_rows_become_pid_index_ids(self):
        with mock.patch.object(wfd, "_osa",
                               return_value=(True, FAKE_AX_OUT)):
            rows = wfd._mac_windows_ax()
        self.assertEqual(
            rows,
            [{"id": "664:1", "app": "Simulator",
              "title": "iPhone 17 – iOS 26.5"},
             {"id": "664:2", "app": "Simulator",
              "title": "iPhone 17 – iOS 27.0"},
             {"id": "812:1", "app": "TextEdit", "title": "Untitled"}])

    def test_blank_title_falls_back_to_app(self):
        with mock.patch.object(wfd, "_osa",
                               return_value=(True, "9\t1\tMail\t\n")):
            rows = wfd._mac_windows_ax()
        self.assertEqual(rows[0]["title"], "Mail")

    def test_list_windows_falls_back_to_app_names(self):
        calls = []

        def fake_osa(script, timeout=15):
            calls.append(script)
            if "windows" in script:
                return False, "not authorized"
            return True, "Simulator, Finder"

        with mock.patch.object(wfd, "_osa", side_effect=fake_osa):
            rows = wfd.list_windows()
        self.assertEqual(
            rows,
            [{"id": "Simulator", "title": "Simulator", "app": "Simulator"},
             {"id": "Finder", "title": "Finder", "app": "Finder"}])
        self.assertEqual(len(calls), 2)

    def test_focus_pid_index_uses_frontmost_and_axraise(self):
        calls = []

        def fake_osa(script, timeout=15):
            calls.append(script)
            return True, ""

        with mock.patch.object(wfd, "_osa", side_effect=fake_osa):
            self.assertTrue(wfd._mac_focus_window("664:2"))
        joined = "\n".join(calls)
        self.assertIn("frontmost to true", joined)
        self.assertIn('AXRaise" of window 2', joined)
        self.assertIn("unix id is 664", joined)

    def test_focus_app_name_prefers_open_dash_a(self):
        with mock.patch.object(wfd, "_run",
                               return_value=(True, "")) as r:
            self.assertTrue(wfd._mac_focus_window("TextEdit"))
        self.assertEqual(r.call_args[0][0][:3], ["open", "-a", "TextEdit"])

    def test_active_window_class_uses_frontmost(self):
        with mock.patch.object(wfd, "_osa",
                               return_value=(True, "TextEdit")):
            self.assertEqual(wfd.active_window_class(), "textedit")


class TestMacClipboard(_Darwin):
    def test_clip_get_uses_pbpaste(self):
        with mock.patch.object(wfd, "sh",
                               return_value=mock.Mock(stdout="old")) as s:
            self.assertEqual(wfd._clip_get(), "old")
        self.assertEqual(s.call_args[0], ("pbpaste",))

    def test_clip_set_uses_pbcopy(self):
        with mock.patch.object(wfd.subprocess, "run") as r:
            wfd._clip_set("new")
        argv = r.call_args[0][0]
        self.assertEqual(argv, ["pbcopy"])
        self.assertEqual(r.call_args[1]["input"], "new")

    def test_file_clipboard_writes_url_for_nonimage(self):
        with mock.patch.object(wfd, "_run",
                               return_value=(True, "")) as r:
            wfd._mac_set_file_clipboard("/tmp/a.txt", "text/plain")
        argv = r.call_args[0][0]
        self.assertEqual(argv[:4], ["osascript", "-l", "JavaScript", "-e"])
        js = argv[4]
        self.assertIn("fileURLWithPath", js)
        self.assertIn("writeObjects", js)
        self.assertNotIn("NSImage", js)

    def test_file_clipboard_adds_nsimage_for_image(self):
        with mock.patch.object(wfd, "_run",
                               return_value=(True, "")) as r:
            wfd._mac_set_file_clipboard("/tmp/a.png", "image/png")
        self.assertIn("NSImage", r.call_args[0][0][4])

    def test_to_png_prefers_sips(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[0] == "sips":
                open(argv[-1], "w").write("png")
                return mock.Mock(returncode=0)
            return mock.Mock(returncode=1)

        with mock.patch.object(wfd.subprocess, "run",
                               side_effect=fake_run), \
             mock.patch.object(wfd.os.path, "isfile", return_value=True):
            out = wfd._to_png("/tmp/in.jpg")
            self.assertTrue(out)
        self.assertEqual(calls[0][:4], ["sips", "-s", "format", "png"])


class TestMacSystemCtl(_Darwin):
    def test_media_posts_nx_key(self):
        with mock.patch.object(sc, "_which",
                               side_effect=lambda n: "/usr/bin/%s" % n), \
             mock.patch.object(sc, "_run",
                               return_value=(True, "")) as r:
            ok, det = sc._media("playpause")
        self.assertTrue(ok)
        argv = r.call_args[0][0]
        self.assertTrue(argv[0].endswith("osascript"))
        self.assertEqual(argv[1:4], ["-l", "JavaScript", "-e"])
        self.assertIn("NX media key", det)

    def test_media_falls_back_to_music(self):
        results = [(False, "no event tap"), (True, "")]

        def fake_run(argv, **kw):
            return results.pop(0)

        with mock.patch.object(sc, "_which",
                               side_effect=lambda n: "/usr/bin/%s" % n), \
             mock.patch.object(sc, "_run", side_effect=fake_run):
            ok, det = sc._media("next")
        self.assertTrue(ok)
        self.assertIn("Music", det)

    def test_wifi_uses_networksetup(self):
        ports = "Hardware Port: Wi-Fi\nDevice: en0\n"
        with mock.patch.object(sc, "_which",
                               side_effect=lambda n: "/usr/sbin/%s" % n), \
             mock.patch.object(sc, "_out",
                               return_value=(True, ports, "")), \
             mock.patch.object(sc, "_run",
                               return_value=(True, "")) as r:
            ok, det = sc._wifi("off")
        self.assertTrue(ok)
        self.assertEqual(r.call_args[0][0][-3:],
                         ["-setairportpower", "en0", "off"])

    def test_bluetooth_refuses_without_blueutil(self):
        with mock.patch.object(sc, "_which", return_value=None):
            ok, det = sc._bluetooth("off")
        self.assertFalse(ok)
        self.assertIn("blueutil", det)

    def test_clip_index_zero_reads_pbpaste(self):
        with mock.patch.object(sc, "_which",
                               return_value="/usr/bin/pbpaste"), \
             mock.patch.object(sc, "_out",
                               return_value=(True, "clip body", "")):
            ok, det = sc._clip_read_index(0)
        self.assertTrue(ok)
        self.assertIn("index 0", det)

    def test_clip_nonzero_index_refuses_honestly(self):
        with mock.patch.object(sc, "_which",
                               return_value="/usr/bin/pbpaste"):
            ok, det = sc._clip_read_index(2)
        self.assertFalse(ok)
        self.assertIn("only the live", det)

    def test_clip_in_uses_pbcopy(self):
        with mock.patch.object(sc, "_which",
                               return_value="/usr/bin/pbcopy"), \
             mock.patch.object(sc.subprocess, "run") as r:
            ok, det = sc._clip_in("hi", "url")
        self.assertTrue(ok)
        self.assertEqual(r.call_args[0][0], ["/usr/bin/pbcopy"])
        self.assertIn("cmd+v", det)

    def test_dnd_and_brightness_refuse_honestly(self):
        self.assertFalse(sc._dnd("on")[0])
        self.assertFalse(sc._brightness("up")[0])


class TestMacProviderPaths(unittest.TestCase):
    """act_platform macos: real window rows + `open -a` focus preference."""

    def test_list_windows_uses_ax_rows(self):
        p = ap.CrossPlatformProvider(platform="macos")
        with mock.patch.object(ap, "_mac_windows_ax",
                               return_value=[{"id": "5:1", "app": "Notes",
                                              "title": "n"}]):
            self.assertEqual(p.list_windows(),
                             [{"id": "5:1", "app": "Notes", "title": "n"}])

    def test_focus_app_prefers_open_before_osascript(self):
        p = ap.CrossPlatformProvider(platform="macos")
        calls = []

        def fake_run(argv, timeout=10):
            calls.append(argv)
            return True, ""

        with mock.patch.object(ap, "_run", side_effect=fake_run):
            r = p.run({"type": "focus_app", "app": "TextEdit"})
        self.assertTrue(r.ok, r.detail)
        self.assertEqual(calls[0], ["open", "-a", "TextEdit"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
