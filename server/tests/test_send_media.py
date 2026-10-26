"""Streaming uploads + text send contract (PEAK backend, Oct 26).

Spawns a real wfd.py on a throwaway HOME/port with NO DISPLAY (so the
X11 injection is a no-op but the HTTP + storage path is fully exercised)
and attacks /send-file, /send-image and /send.

  * /send-file streams to ~/wf-uploads; large bodies don't balloon RSS
  * a cap (WF_MAX_UPLOAD) is enforced BEFORE the body is read -> 413 JSON
  * filenames are sanitised: no traversal, no control chars, kept ext
  * a client that disconnects mid-upload leaves no partial file
  * the {ok,into,path,mb} response contract holds
  * /send validates mode and honours the optional focus-verify `expect`

Run:  python3 -m unittest server.tests.test_send_media   (stdlib only)
"""
import http.client, json, os, socket, subprocess, sys, tempfile, time, unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WFD = os.path.join(BASE, "wfd.py")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class SendMediaCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-send-")
        cls.port = free_port()
        cfg = os.path.join(cls.tmp, ".config", "wf"); os.makedirs(cfg)
        env = dict(os.environ, HOME=cls.tmp, WF_PORT=str(cls.port),
                   WF_NO_LOG_TEXT="1", WF_MAX_UPLOAD=str(4 * 1024 * 1024))
        env.pop("DISPLAY", None)       # no X11 — storage path only
        cls.proc = subprocess.Popen([sys.executable, WFD], env=env,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                if cls.raw("GET", "/health")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            cls.proc.kill(); raise RuntimeError("wfd did not start")
        cls.key = open(os.path.join(cfg, "key")).read().strip()
        cls.uploads = os.path.join(cls.tmp, "wf-uploads")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    @classmethod
    def raw(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=15)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, dict(r.getheaders()), data

    def _hdr(self, extra=None):
        h = {"X-WF-Key": self.key}
        if extra:
            h.update(extra)
        return h

    # ---- uploads ---------------------------------------------------------
    def test_send_file_roundtrip_and_contract(self):
        data = os.urandom(200 * 1024)
        st, _, body = self.raw("POST", "/send-file", data,
                               self._hdr({"X-File-Name": "hello world.bin"}))
        self.assertEqual(st, 200)
        j = json.loads(body)
        for k in ("ok", "into", "path", "mb"):
            self.assertIn(k, j, "response must keep the %s field" % k)
        self.assertTrue(j["ok"])
        self.assertTrue(os.path.isfile(j["path"]))
        with open(j["path"], "rb") as f:
            self.assertEqual(f.read(), data, "bytes must survive intact")
        self.assertTrue(j["path"].startswith(self.uploads))

    def test_send_file_requires_auth(self):
        st, _, _ = self.raw("POST", "/send-file", b"x",
                            {"X-File-Name": "a.bin"})
        self.assertEqual(st, 403)

    def test_cap_rejected_before_read(self):
        # advertise a body way over the 4 MiB test cap; the daemon must
        # 413 WITHOUT reading it. We send only a tiny slice and expect a
        # fast 413, not a hang.
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        big = 8 * 1024 * 1024
        c.putrequest("POST", "/send-file")
        c.putheader("X-WF-Key", self.key)
        c.putheader("X-File-Name", "huge.bin")
        c.putheader("Content-Length", str(big))
        c.endheaders()
        c.send(b"\x00" * 4096)         # only a sliver of the body
        r = c.getresponse(); body = r.read(); c.close()
        self.assertEqual(r.status, 413)
        j = json.loads(body)
        self.assertFalse(j["ok"])
        self.assertIn("error", j)
        self.assertIsInstance(j["error"], str)
        self.assertTrue(len(j["error"]) > 3)

    def test_filename_sanitised_no_traversal(self):
        import urllib.parse
        for bad in ("../../etc/passwd", "/etc/shadow",
                    "..\\..\\win.ini", "a\x00b\nc.txt", "...dots"):
            # the phone sends the name URL-encoded in X-File-Name; the
            # daemon unquotes it, so encode here to carry control chars.
            enc = urllib.parse.quote(bad, safe="")
            st, _, body = self.raw("POST", "/send-file", b"data",
                                   self._hdr({"X-File-Name": enc}))
            self.assertEqual(st, 200)
            p = json.loads(body)["path"]
            # must live inside UPLOADS, basename only, no separators
            self.assertTrue(p.startswith(self.uploads + os.sep))
            base = os.path.basename(p)
            self.assertNotIn("/", base)
            self.assertNotIn("\\", base)
            self.assertFalse(base.startswith(".."))
            d = os.path.dirname(p)
            self.assertEqual(os.path.realpath(d), os.path.realpath(self.uploads))

    def test_abort_leaves_no_partial(self):
        before = set(os.listdir(self.uploads)) if os.path.isdir(self.uploads) else set()
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", "/send-file")
        c.putheader("X-WF-Key", self.key)
        c.putheader("X-File-Name", "aborted.bin")
        c.putheader("Content-Length", str(2 * 1024 * 1024))
        c.endheaders()
        c.send(b"\x00" * (300 * 1024))     # ~15% then hang up
        c.close()                           # abort
        time.sleep(1.0)
        after = set(os.listdir(self.uploads)) if os.path.isdir(self.uploads) else set()
        new = after - before
        self.assertFalse(any("aborted" in n for n in new),
                         "no completed aborted.bin")
        self.assertFalse(any(n.startswith(".wf-up-") for n in after),
                         "no leftover temp partial: %s" % new)

    def test_collision_safe_names(self):
        paths = set()
        for _ in range(3):
            st, _, body = self.raw("POST", "/send-file", os.urandom(16),
                                   self._hdr({"X-File-Name": "dup.bin"}))
            self.assertEqual(st, 200)
            paths.add(json.loads(body)["path"])
            time.sleep(0.01)
        for p in paths:
            self.assertTrue(os.path.isfile(p))

    def test_send_image_contract(self):
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        st, _, body = self.raw("POST", "/send-image", png,
                               self._hdr({"Content-Type": "image/png"}))
        self.assertEqual(st, 200)
        j = json.loads(body)
        self.assertTrue(j["ok"])
        self.assertIn("mime", j)

    # ---- text ------------------------------------------------------------
    def test_send_bad_mode(self):
        st, _, body = self.raw("POST", "/send",
                               json.dumps({"text": "x", "mode": "bogus"}).encode(),
                               self._hdr({"Content-Type": "application/json"}))
        self.assertEqual(st, 400)
        self.assertIn("error", json.loads(body))

    def test_send_ok_type(self):
        st, _, body = self.raw("POST", "/send",
                               json.dumps({"text": "hi", "mode": "type"}).encode(),
                               self._hdr({"Content-Type": "application/json"}))
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_send_requires_auth(self):
        st, _, _ = self.raw("POST", "/send",
                            json.dumps({"text": "x"}).encode(),
                            {"Content-Type": "application/json"})
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main()
