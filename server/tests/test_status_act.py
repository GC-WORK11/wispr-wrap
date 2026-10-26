"""/status, /health, /act catalog + icons contract (PEAK backend).

Spawns a real wfd.py (no DISPLAY -> can_act must be false) and checks:
  * HEAD /health and HEAD /status answer headers-only, never 501
  * unauthenticated /status is 403 and leaks nothing but the error
  * /status.laptop carries connected/name/display/can_act/signs/engine
  * can_act is false with no DISPLAY
  * /act returns features/commands and is cheap on the warm call
  * /act/icons guards traversal (../, %2f, over-long ids)

Run:  python3 -m unittest server.tests.test_status_act
"""
import http.client, json, os, socket, subprocess, sys, tempfile, time, unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WFD = os.path.join(BASE, "wfd.py")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class StatusActCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-stat-")
        cls.port = free_port()
        cfg = os.path.join(cls.tmp, ".config", "wf"); os.makedirs(cfg)
        env = dict(os.environ, HOME=cls.tmp, WF_PORT=str(cls.port),
                   WF_NO_LOG_TEXT="1")
        env.pop("DISPLAY", None)      # no X11 -> can_act must be False
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

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    @classmethod
    def raw(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, dict(r.getheaders()), data

    def auth(self, method, path, headers=None):
        h = {"X-WF-Key": self.key}
        if headers:
            h.update(headers)
        return self.raw(method, path, None, h)

    # ---- health / status -------------------------------------------------
    def test_head_health_no_body_not_501(self):
        st, _, body = self.raw("HEAD", "/health")
        self.assertEqual(st, 200)
        self.assertEqual(body, b"")

    def test_head_status_auth(self):
        st, _, body = self.raw("HEAD", "/status")       # unauth
        self.assertEqual(st, 403)
        st, _, body = self.auth("HEAD", "/status")       # authed
        self.assertEqual(st, 200)
        self.assertEqual(body, b"")

    def test_status_unauth_leaks_nothing(self):
        st, _, body = self.raw("GET", "/status")
        self.assertEqual(st, 403)
        j = json.loads(body)
        self.assertFalse(j.get("ok", True))
        # only ok + error, nothing about the host
        self.assertLessEqual(set(j.keys()), {"ok", "error"})

    def test_status_laptop_block(self):
        st, _, body = self.auth("GET", "/status")
        self.assertEqual(st, 200)
        j = json.loads(body)
        lap = j["laptop"]
        for k in ("connected", "name", "display", "can_act", "signs", "engine"):
            self.assertIn(k, lap)
        self.assertIn("focus", j)
        self.assertIn("focus_title", j)
        # no DISPLAY in this spawn -> can_act must be false
        self.assertFalse(lap["can_act"], "can_act must be false w/o DISPLAY")

    def test_status_strict_json(self):
        st, _, body = self.auth("GET", "/status")
        txt = body.decode()
        self.assertNotIn("Infinity", txt)
        self.assertNotIn("NaN", txt)

    # ---- act -------------------------------------------------------------
    def test_act_shape_and_warm_is_cheap(self):
        t0 = time.time()
        st, _, body = self.auth("GET", "/act")
        cold = time.time() - t0
        self.assertEqual(st, 200)
        j = json.loads(body)
        self.assertTrue(j["ok"])
        for k in ("features", "commands", "pending"):
            self.assertIn(k, j)
        t1 = time.time()
        self.auth("GET", "/act")
        warm = time.time() - t1
        # warm call should be fast (cached scan); generous bound for CI
        self.assertLess(warm, max(cold, 0.5))

    def test_act_icons_traversal_guard(self):
        for bad in ("../../../etc/passwd", "..%2f..%2fpasswd",
                    "%2e%2e%2fpasswd", "a" * 300, "foo/bar"):
            st, _, _ = self.auth("GET", "/act/icons/" + bad)
            self.assertIn(st, (403, 404),
                          "traversal id %r must not resolve" % bad)


if __name__ == "__main__":
    unittest.main()
