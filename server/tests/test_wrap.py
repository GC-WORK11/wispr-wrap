import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wfd

FAKE_IP_OUT = """\
1: lo    inet 127.0.0.1/8 scope host lo
2: eth0    inet 10.0.2.15/24 scope global eth0
3: wlan0    inet 192.168.1.44/24 scope global wlan0
4: docker0    inet 172.17.0.1/16 scope global docker0
5: br-abc123    inet 172.18.0.1/16 scope global br-abc123
6: veth99    inet 169.254.10.1/16 scope link veth99
7: virbr0    inet 192.168.122.1/24 scope global virbr0
"""


class TestLanIp(unittest.TestCase):
    """all_ips() must skip every net a phone can't reach, wifi first."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wf-wrap-")
        self._path = os.environ.get("PATH", "")
        ipstub = os.path.join(self.tmp, "ip")
        with open(ipstub, "w") as f:
            f.write("#!/bin/sh\n")
            f.write("cat <<'EOF'\n" + FAKE_IP_OUT + "EOF\n")
        os.chmod(ipstub, 0o755)
        os.environ["PATH"] = self.tmp + os.pathsep + self._path

    def tearDown(self):
        os.environ["PATH"] = self._path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_filters_unreachables(self):
        ips = wfd.all_ips()
        self.assertNotIn("127.0.0.1", ips)
        self.assertNotIn("172.17.0.1", ips)      # docker0
        self.assertNotIn("172.18.0.1", ips)      # br-*
        self.assertNotIn("192.168.122.1", ips)   # virbr
        self.assertNotIn("169.254.10.1", ips)    # link-local
        self.assertIn("192.168.1.44", ips)
        self.assertIn("10.0.2.15", ips)

    def test_wifi_first(self):
        ips = wfd.all_ips()
        self.assertEqual(ips[0], "192.168.1.44")
        self.assertEqual(wfd.lan_ip(), "192.168.1.44")


class TestPortFallback(unittest.TestCase):
    """serve() must take the next free port when the preferred is busy."""

    def test_falls_back(self):
        block = socket.socket()
        block.bind(("127.0.0.1", 0))
        port = block.getsockname()[1]
        block.listen(1)
        try:
            srv, bound = wfd.serve("127.0.0.1", port, wfd.Handler)
            try:
                self.assertIsNotNone(srv)
                self.assertNotEqual(bound, port)
                self.assertGreaterEqual(bound, port + 1)
            finally:
                if srv:
                    srv.server_close()
        finally:
            block.close()

    def test_gives_up_past_window(self):
        socks = []
        base = None
        try:
            s0 = socket.socket(); s0.bind(("127.0.0.1", 0)); s0.listen(1)
            base = s0.getsockname()[1]
            socks.append(s0)
            for i in range(1, 12):          # occupy the whole fallback window
                s = socket.socket(); s.bind(("127.0.0.1", base + i)); s.listen(1)
                socks.append(s)
            srv, _ = wfd.serve("127.0.0.1", base, wfd.Handler)
            self.assertIsNone(srv)
            if srv:
                srv.server_close()
        finally:
            for s in socks:
                s.close()


class TestDetachAndFootprint(unittest.TestCase):
    """A daemon launched under setsid lives in its own session (the CLI /
    terminal can die without it), answers on the LAN IP, and stays under
    the 80 MB idle bar. Real process, real sockets — a slow test."""

    PORT = 0

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-detach-")
        free = socket.socket(); free.bind(("127.0.0.1", 0))
        cls.PORT = free.getsockname()[1]; free.close()
        env = dict(os.environ)
        env["WF_PORT"] = str(cls.PORT)
        env["HOME"] = cls.tmp          # fresh key, bank, config in tmp
        srvdir = os.path.join(os.path.dirname(__file__), "..")
        cls.proc = subprocess.Popen(
            ["setsid", "-f", sys.executable,
             os.path.join(srvdir, "wfd.py")],
            env=env, cwd=srvdir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
        # wait for health
        for _ in range(80):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{cls.PORT}/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError("daemon did not come up")

    @classmethod
    def tearDownClass(cls):
        try:
            pid = cls._daemon_pid(cls)
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            cls.proc.terminate()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _health(self, host):
        with urllib.request.urlopen(
                f"http://{host}:{self.PORT}/health", timeout=3) as r:
            return r.status

    def _daemon_pid(self):
        # setsid -f forks: Popen.pid is the short-lived launcher, the daemon
        # is the wfd.py child in its new session.
        out = subprocess.run(["pgrep", "-f", "wfd.py"],
                             capture_output=True, text=True).stdout.split()
        mine = []
        for p in out:
            try:
                with open(f"/proc/{p}/environ", "rb") as ef:
                    if f"WF_PORT={self.PORT}".encode() in ef.read():
                        mine.append(int(p))
            except OSError:
                continue
        self.assertTrue(mine, "no daemon process with our WF_PORT")
        return mine[0]

    def test_own_session_survives_parent_death(self):
        # setsid: the daemon is a session leader — a SIGHUP to the dead
        # terminal's process group can never reach it.
        pid = self._daemon_pid()
        self.assertEqual(os.getsid(pid), pid,
                         "daemon must be its own session leader")
        # functional proof: still answering after the launcher exited
        self.assertEqual(self._health("127.0.0.1"), 200)

    def test_lan_ip_answers_200(self):
        ip = wfd.lan_ip()
        if ip == "127.0.0.1":
            self.skipTest("no LAN interface on this box")
        self.assertEqual(self._health(ip), 200)

    def test_idle_rss_under_80mb(self):
        rss_kb = 0
        with open(f"/proc/{self._daemon_pid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    rss_kb = int(line.split()[1])
        mb = rss_kb / 1024
        # write the measurement to proofs (kept on record)
        proofs = os.path.join(os.path.dirname(__file__), "..", "..",
                              "proofs", "daemon-footprint")
        os.makedirs(proofs, exist_ok=True)
        with open(os.path.join(proofs, "rss.txt"), "w") as f:
            f.write(f"idle daemon VmRSS: {mb:.1f} MB (target < 80 MB)\n")
        self.assertLess(mb, 80, f"idle daemon RSS {mb:.1f} MB >= 80 MB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
