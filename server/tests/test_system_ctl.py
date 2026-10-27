#!/usr/bin/env python3
"""Tests for system_ctl.py — capability detection + real argv shapes.

Every spawn is stubbed: a temp dir of fake executables (sh scripts that log
their argv to a file and emit canned stdout) is put on PATH. Tests prove
the right tool is picked, fallbacks engage when tools are missing, stderr
lands in detail, timeouts fire, and "not available" is honest when nothing
on the box can serve the action.

Run: python3 tests/test_system_ctl.py  (from server/) or python3 -m unittest
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import system_ctl as sc


class StubEnv(unittest.TestCase):
    """setUp: fresh temp bin dir on PATH, stub-arg log, temp HOME."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wf-sysctl-")
        self.bin = os.path.join(self.tmp, "bin")
        self.home = os.path.join(self.tmp, "home")
        self.backlight = os.path.join(self.tmp, "backlight")
        for d in (self.bin, self.home, self.backlight):
            os.makedirs(d)
        self.log = os.path.join(self.tmp, "argv.log")
        self._env = dict(os.environ)
        os.environ["PATH"] = self.bin
        os.environ["HOME"] = self.home
        os.environ.pop("XDG_SESSION_TYPE", None)
        os.environ.pop("XDG_SESSION_ID", None)
        self._old_bl = sc.BACKLIGHT_DIR
        sc.BACKLIGHT_DIR = self.backlight
        # These tests exercise the Linux backend table; the darwin
        # branches in system_ctl must not preempt the stubs on a Mac.
        self._old_platform = sys.platform
        sys.platform = "linux"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        sys.platform = self._old_platform
        sc.BACKLIGHT_DIR = self._old_bl
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------
    def stub(self, name, body="exit 0", log=True):
        """Create a fake executable; argv is appended to self.log."""
        p = os.path.join(self.bin, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n")
            if log:
                f.write('echo "%s|$@" >> "%s"\n' % (name, self.log))
            f.write(body + "\n")
        os.chmod(p, 0o755)
        return p

    def calls(self):
        try:
            with open(self.log) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def backlight_dev(self, name="acpi0", maxb=100, cur=50):
        d = os.path.join(self.backlight, name)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "max_brightness"), "w").write(str(maxb))
        open(os.path.join(d, "brightness"), "w").write(str(cur))
        return d


# ------------------------------------------------------------------
# capability table
# ------------------------------------------------------------------

class CapsCase(StubEnv):
    def test_empty_box_reports_unavailable(self):
        caps = sc.capabilities()
        self.assertEqual(sc.available(), [])
        for tool, c in caps.items():
            self.assertFalse(c["available"], tool)
            self.assertEqual(c["via"], [], tool)
            self.assertIsInstance(c["tier"], int)

    def test_full_box_reports_via(self):
        for t in ("pactl", "amixer", "brightnessctl", "playerctl",
                  "import", "gnome-screenshot", "spectacle", "scrot",
                  "loginctl", "xdg-screensaver", "gsettings", "nmcli",
                  "bluetoothctl", "rfkill", "copyq", "xclip"):
            self.stub(t)
        self.backlight_dev()
        caps = sc.capabilities()
        self.assertTrue(caps["sys.mute"]["available"])
        self.assertEqual(caps["sys.mute"]["via"], ["pactl", "amixer"])
        self.assertTrue(caps["sys.brightness-up"]["available"])
        self.assertIn("brightnessctl", caps["sys.brightness-up"]["via"])
        self.assertIn("/sys/class/backlight" , caps["sys.brightness-up"]["via"])
        self.assertTrue(caps["media.next"]["available"])
        self.assertEqual(caps["sys.lock"]["tier"], 2)
        self.assertEqual(caps["net.wifi"]["tier"], 2)
        self.assertIn("DE-dependent", caps["sys.dnd"]["note"])
        self.assertEqual(sc.available(), sorted(caps))

    def test_live_clipboard_only_note(self):
        self.stub("xclip")
        caps = sc.capabilities()
        self.assertTrue(caps["clipboard.history"]["available"])
        self.assertIn("no clipboard manager", caps["clipboard.history"]["note"])

    def test_wayland_note_in_caps(self):
        os.environ["XDG_SESSION_TYPE"] = "wayland"
        self.stub("import")
        caps = sc.capabilities()
        self.assertIn("wayland", caps["sys.screenshot"]["note"].lower())


# ------------------------------------------------------------------
# volume / mute
# ------------------------------------------------------------------

class VolumeCase(StubEnv):
    def test_pactl_argv(self):
        self.stub("pactl")
        ok, det = sc.dispatch("sys.volume-up")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls(),
                         ["pactl|set-sink-volume @DEFAULT_SINK@ +5%"])
        ok, det = sc.dispatch("sys.volume-down", {"step": 3})
        self.assertEqual(self.calls()[-1],
                         "pactl|set-sink-volume @DEFAULT_SINK@ -3%")

    def test_mute_pactl(self):
        self.stub("pactl")
        ok, _ = sc.dispatch("sys.mute")
        self.assertTrue(ok)
        self.assertEqual(self.calls(),
                         ["pactl|set-sink-mute @DEFAULT_SINK@ toggle"])

    def test_amixer_fallback(self):
        self.stub("amixer")
        ok, det = sc.dispatch("sys.mute")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls(), ["amixer|-q set Master toggle"])

    def test_unavailable(self):
        ok, det = sc.dispatch("sys.volume-up")
        self.assertFalse(ok)
        self.assertIn("not available", det)
        self.assertIn("pactl", det)

    def test_stderr_in_detail(self):
        self.stub("pactl", 'echo "pa refused" >&2\nexit 3')
        ok, det = sc.dispatch("sys.mute")
        self.assertFalse(ok)
        self.assertIn("pa refused", det)

    def test_timeout_in_detail(self):
        old = sc.SPAWN_TIMEOUT
        sc.SPAWN_TIMEOUT = 0.2
        try:
            # no `sleep` binary on the stub-only PATH — sh builtin busy loop
            self.stub("pactl", "while :; do :; done\nexit 0")
            ok, det = sc.dispatch("sys.mute")
            self.assertFalse(ok)
            self.assertIn("timed out", det)
        finally:
            sc.SPAWN_TIMEOUT = old


# ------------------------------------------------------------------
# brightness
# ------------------------------------------------------------------

class BrightnessCase(StubEnv):
    def test_brightnessctl_argv(self):
        self.stub("brightnessctl")
        ok, det = sc.dispatch("sys.brightness-up")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls(), ["brightnessctl|set +10%"])
        ok, det = sc.dispatch("sys.brightness-down", {"step": 20})
        self.assertEqual(self.calls()[-1], "brightnessctl|set 20%-")

    def test_sysfs_fallback_writes(self):
        dev = self.backlight_dev(maxb=100, cur=50)
        ok, det = sc.dispatch("sys.brightness-up")
        self.assertTrue(ok, det)
        self.assertEqual(open(os.path.join(dev, "brightness")).read(), "60")
        self.assertIn("acpi0", det)

    def test_sysfs_clamps(self):
        dev = self.backlight_dev(maxb=100, cur=95)
        ok, det = sc.dispatch("sys.brightness-up")
        self.assertTrue(ok, det)
        self.assertEqual(open(os.path.join(dev, "brightness")).read(), "100")
        dev2 = self.backlight_dev("other", maxb=100, cur=5)
        # sorted() -> acpi0 first; only one device is touched
        ok, det = sc.dispatch("sys.brightness-down")
        self.assertTrue(ok, det)

    def test_sysfs_unwritable_honest(self):
        dev = self.backlight_dev(maxb=100, cur=50)
        os.chmod(os.path.join(dev, "brightness"), 0o444)
        if os.geteuid() == 0:
            self.skipTest("root ignores file perms")
        ok, det = sc.dispatch("sys.brightness-up")
        self.assertFalse(ok)
        self.assertIn("write failed", det)

    def test_unavailable(self):
        ok, det = sc.dispatch("sys.brightness-down")
        self.assertFalse(ok)
        self.assertIn("not available", det)


# ------------------------------------------------------------------
# media via MPRIS
# ------------------------------------------------------------------

class MediaCase(StubEnv):
    def test_playerctl_argv(self):
        self.stub("playerctl")
        for sub, arg in (("media.playpause", "play-pause"),
                         ("media.next", "next"), ("media.prev", "previous")):
            ok, det = sc.dispatch(sub)
            self.assertTrue(ok, det)
            self.assertEqual(self.calls()[-1], "playerctl|" + arg)

    def test_dbus_fallback_finds_player(self):
        self.stub("dbus-send", """case "$*" in
  *ListNames*) echo 'array [
      string "org.freedesktop.DBus"
      string "org.mpris.MediaPlayer2.stubtune"
   ]' ;;
  *) echo 'method return time=1' ;;
esac
exit 0""")
        ok, det = sc.dispatch("media.next")
        self.assertTrue(ok, det)
        joined = "\n".join(self.calls())
        self.assertIn("--dest=org.mpris.MediaPlayer2.stubtune", joined)
        self.assertIn("org.mpris.MediaPlayer2.Player.Next", joined)

    def test_dbus_no_player(self):
        self.stub("dbus-send", """case "$*" in
  *ListNames*) echo 'array [
      string "org.freedesktop.DBus"
   ]' ;;
esac
exit 0""")
        ok, det = sc.dispatch("media.playpause")
        self.assertFalse(ok)
        self.assertIn("no org.mpris.MediaPlayer2", det)

    def test_unavailable(self):
        ok, det = sc.dispatch("media.next")
        self.assertFalse(ok)
        self.assertIn("not available", det)


# ------------------------------------------------------------------
# screenshot
# ------------------------------------------------------------------

class ShotCase(StubEnv):
    def test_import_full_argv(self):
        self.stub("import")
        ok, det = sc.dispatch("sys.screenshot")
        self.assertTrue(ok, det)
        call = self.calls()[-1]
        self.assertTrue(call.startswith("import|-window root "), call)
        self.assertIn(".png", call)
        self.assertIn("screenshot-", det)

    def test_gnome_region_argv(self):
        self.stub("gnome-screenshot")
        ok, det = sc.dispatch("sys.screenshot-region")
        self.assertTrue(ok, det)
        self.assertTrue(self.calls()[-1].startswith("gnome-screenshot|-a -f "))

    def test_spectacle_preferred_over_scrot(self):
        self.stub("spectacle")
        self.stub("scrot")
        ok, det = sc.dispatch("sys.screenshot")
        self.assertTrue(ok, det)
        # import and gnome-screenshot absent -> spectacle beats scrot
        self.assertTrue(self.calls()[-1].startswith("spectacle|"))

    def test_unavailable(self):
        ok, det = sc.dispatch("sys.screenshot")
        self.assertFalse(ok)
        self.assertIn("not available", det)


# ------------------------------------------------------------------
# lock / dnd
# ------------------------------------------------------------------

class LockCase(StubEnv):
    def test_loginctl(self):
        self.stub("loginctl")
        os.environ["XDG_SESSION_ID"] = "7"
        ok, det = sc.dispatch("sys.lock")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls(), ["loginctl|lock-session 7"])

    def test_xdg_screensaver_fallback(self):
        self.stub("loginctl", "exit 1")
        self.stub("xdotool", "echo 12345")
        self.stub("xdg-screensaver")
        ok, det = sc.dispatch("sys.lock")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls()[-1], "xdg-screensaver|lock 12345")

    def test_unavailable(self):
        ok, det = sc.dispatch("sys.lock")
        self.assertFalse(ok)
        self.assertIn("not available", det)


class DndCase(StubEnv):
    def test_gsettings_toggle(self):
        self.stub("gsettings", """case "$*" in
  *show-banners) echo true ;;
esac
exit 0""")
        ok, det = sc.dispatch("sys.dnd")
        self.assertTrue(ok, det)
        joined = "\n".join(self.calls())
        self.assertIn("get org.gnome.desktop.notifications show-banners",
                      joined)
        self.assertIn("set org.gnome.desktop.notifications show-banners false",
                      joined)
        self.assertIn("dnd on", det)

    def test_de_dependent_honest(self):
        ok, det = sc.dispatch("sys.dnd")
        self.assertFalse(ok)
        self.assertIn("DE-dependent", det)


# ------------------------------------------------------------------
# wifi / bluetooth
# ------------------------------------------------------------------

class NetCase(StubEnv):
    def test_wifi_nmcli_toggle(self):
        self.stub("nmcli", """case "$*" in
  'radio wifi') echo enabled ;;
esac
exit 0""")
        ok, det = sc.dispatch("net.wifi")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls()[-1], "nmcli|radio wifi off")

    def test_wifi_explicit_on(self):
        self.stub("nmcli")
        ok, det = sc.dispatch("net.wifi", {"mode": "on"})
        self.assertTrue(ok, det)
        self.assertEqual(self.calls(), ["nmcli|radio wifi on"])

    def test_wifi_rfkill_fallback(self):
        self.stub("rfkill", """case "$*" in
  'list wifi') echo 'Soft blocked: yes' ;;
esac
exit 0""")
        ok, det = sc.dispatch("net.wifi")
        self.assertTrue(ok, det)
        self.assertIn("rfkill|unblock wifi", self.calls())

    def test_bt_bluetoothctl(self):
        self.stub("bluetoothctl", """case "$*" in
  show) echo 'Powered: no' ;;
esac
exit 0""")
        ok, det = sc.dispatch("net.bluetooth")
        self.assertTrue(ok, det)
        self.assertEqual(self.calls()[-1], "bluetoothctl|power on")

    def test_unavailable(self):
        ok, det = sc.dispatch("net.wifi")
        self.assertFalse(ok)
        self.assertIn("not available", det)


# ------------------------------------------------------------------
# clipboard history
# ------------------------------------------------------------------

class ClipCase(StubEnv):
    def test_no_manager_live_clipboard_only(self):
        self.stub("xclip", r"""case "$*" in
  *-t\ TARGETS*) printf 'TARGETS\nUTF8_STRING\n' ;;
  *-o*) echo 'clip text' ;;
esac
exit 0""")
        ok, det = sc.dispatch("clipboard.history", {"index": 0})
        self.assertTrue(ok, det)
        self.assertIn("no manager", det)
        ok, det = sc.dispatch("clipboard.history", {"index": 3})
        self.assertFalse(ok)
        self.assertIn("not available", det)
        self.assertIn("clipboard manager", det)

    def test_copyq_loads_via_xclip(self):
        self.stub("copyq", "echo 'item two'\nexit 0")
        # read piped stdin with a sh builtin (`cat` isn't on the stub PATH)
        self.stub("xclip", "while read l; do :; done\nexit 0")
        ok, det = sc.dispatch("clipboard.history", {"index": 2})
        self.assertTrue(ok, det)
        self.assertIn("copyq item 2", det)
        self.assertEqual(self.calls()[-1],
                         "xclip|-selection clipboard -i")

    def test_targets(self):
        self.stub("xclip", r"printf 'TARGETS\nimage/png\n'")
        ok, det = sc.dispatch("clipboard.targets")
        self.assertTrue(ok, det)
        self.assertIn("image/png", det)


# ------------------------------------------------------------------
# provider contract
# ------------------------------------------------------------------

class ProviderCase(StubEnv):
    def test_run_result_shape(self):
        self.stub("pactl")
        r = sc.SystemCtlProvider().run({"tool": "sys.mute"})
        self.assertTrue(r.ok)
        self.assertEqual(r.tier, 1)
        self.assertIsNone(r.confirm_id)
        self.assertTrue(r.detail)

    def test_run_tier_from_catalog(self):
        self.stub("loginctl")
        r = sc.SystemCtlProvider().run({"tool": "sys.lock"})
        self.assertEqual(r.tier, 2)
        r = sc.SystemCtlProvider().run({"tool": "net.wifi",
                                        "args": {"mode": "off"},
                                        "tier": 2})
        self.assertEqual(r.tier, 2)

    def test_run_unknown_tool(self):
        r = sc.SystemCtlProvider().run({"tool": "sys.launch-missiles"})
        self.assertFalse(r.ok)
        self.assertIn("unknown", r.detail)

    def test_verify_focus(self):
        self.stub("xdotool", """case "$*" in
  *classname*) echo 'firefox' ;;
  *windowname*) echo 'GitHub — Mozilla Firefox' ;;
  *getactivewindow) echo 12345 ;;
esac
exit 0""")
        p = sc.SystemCtlProvider()
        v = p.verify_focus()
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["class"], "firefox")
        v2 = p.verify_focus({"expected_class": "firefox"})
        self.assertTrue(v2["ok"])
        v3 = p.verify_focus({"expected_class": "terminal"})
        self.assertFalse(v3["ok"])

    def test_verify_focus_no_xdotool(self):
        v = sc.SystemCtlProvider().verify_focus()
        self.assertFalse(v["ok"])
        self.assertIn("xdotool", v["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
