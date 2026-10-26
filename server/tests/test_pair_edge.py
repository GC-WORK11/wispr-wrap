"""/pair and /pair-info edge cases (PEAK backend).

  * pair code is 8 chars, uppercase alnum
  * wrong code -> 403 JSON (ok:false)
  * correct code -> 200 with the key
  * second attempt within 1s -> 429 with Retry-After
  * /pair-info is loopback-only (127.0.0.1 from this test passes) and
    refuses proxied requests (cf-*/x-forwarded-*)
  * the pairing key never appears in a proxied /pair-info reply

Run:  python3 -m unittest server.tests.test_pair_edge
"""
import http.client, json, os, re, socket, subprocess, sys, tempfile, time, unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WFD = os.path.join(BASE, "wfd.py")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


class PairEdgeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-pair-")
        cls.port = free_port()
        cfg = os.path.join(cls.tmp, ".config", "wf"); os.makedirs(cfg)
        env = dict(os.environ, HOME=cls.tmp, WF_PORT=str(cls.port),
                   WF_NO_LOG_TEXT="1")
        env.pop("DISPLAY", None)
        cls.logf = open(os.path.join(cls.tmp, "wfd.log"), "wb")
        cls.proc = subprocess.Popen([sys.executable, WFD], env=env,
                                    stdout=cls.logf, stderr=cls.logf)
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
        # grab the pair code from the localhost /pair-info
        st, _, body = cls.raw("GET", "/pair-info")
        cls.info = json.loads(body)
        cls.code = cls.info["code"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.logf.close()

    @classmethod
    def raw(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, dict(r.getheaders()), data

    # ---------------------------------------------------------------------
    def test_pair_code_format(self):
        self.assertRegex(self.code, r"^[A-Z0-9]{8}$")

    def test_pairinfo_proxied_refused(self):
        st, _, body = self.raw("GET", "/pair-info",
                               headers={"X-Forwarded-For": "1.2.3.4"})
        self.assertEqual(st, 403)
        self.assertNotIn(self.key, body.decode())
        st, _, body = self.raw("GET", "/pair-info",
                               headers={"CF-Connecting-IP": "1.2.3.4"})
        self.assertEqual(st, 403)
        self.assertNotIn(self.key, body.decode())

    def test_wrong_code_403(self):
        time.sleep(1.1)              # clear the rate window
        st, _, body = self.raw("POST", "/pair",
                               body=json.dumps({"code": "ZZZZZZZZ"}).encode())
        self.assertEqual(st, 403)
        j = json.loads(body)
        self.assertFalse(j["ok"])

    def test_rate_limit_429_retry_after(self):
        time.sleep(1.1)
        st1, _, _ = self.raw("POST", "/pair",
                             body=json.dumps({"code": "AAAAAAAA"}).encode())
        st2, h2, _ = self.raw("POST", "/pair",
                             body=json.dumps({"code": "BBBBBBBB"}).encode())
        self.assertIn(st1, (403, 200))
        self.assertEqual(st2, 429)
        # Retry-After header present (case-insensitive)
        ra = {k.lower(): v for k, v in h2.items()}.get("retry-after")
        self.assertIsNotNone(ra)

    def test_correct_code_pairs(self):
        time.sleep(1.1)
        st, _, body = self.raw("POST", "/pair",
                               body=json.dumps({"code": self.code}).encode())
        self.assertEqual(st, 200)
        j = json.loads(body)
        self.assertTrue(j["ok"])
        self.assertEqual(j["key"], self.key)

    def test_key_never_logged(self):
        # force a flush
        time.sleep(0.2)
        with open(os.path.join(self.tmp, "wfd.log"), "rb") as f:
            log = f.read().decode("utf-8", "replace")
        self.assertNotIn(self.key, log, "pairing key must never hit the log")


if __name__ == "__main__":
    unittest.main()
