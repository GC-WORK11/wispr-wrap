#!/usr/bin/env python3
"""Phone -> laptop action/pointer surface.

Spawns a real wfd.py against a throwaway HOME on an ephemeral port, with
NO DISPLAY in the child env so the pointer path must degrade to ok:false
(never 500). Contracts tested:

  * /act/run and /act/pointer 403 without X-WF-Key
  * /act/pointer 400 on bad op / bad x; /act/run 400 on a non-dict action
  * both routes 413 over the body cap (4096 pointer, 16384 run)
  * /act/pointer returns ok:false (not 500) when there is no DISPLAY
  * /act/pointer rate limit returns 429 after a >90 req/s burst
  * /act/run with a tier-2 action parks a pending confirm and that
    pending row appears in GET /live/state
  * /act/run appends a 'fired' feed event visible in GET /live/state

Run:  python3 -m unittest server.tests.test_act_pointer   (stdlib only)
"""
import http.client, json, os, socket, subprocess, sys, tempfile, time, unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # server/
WFD = os.path.join(BASE, "wfd.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ActPointerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-act-")
        cls.port = free_port()
        cls.url = ("127.0.0.1", cls.port)
        cfg = os.path.join(cls.tmp, ".config", "wf")
        os.makedirs(cfg)
        # Deliberately start WITHOUT a DISPLAY so the pointer path has to
        # answer ok:false instead of 500. Keep PATH so xdotool resolves if
        # installed (it still fails cleanly without an X server).
        env = {"HOME": cls.tmp, "WF_PORT": str(cls.port),
               "WF_NO_LOG_TEXT": "1",
               "PATH": "/usr/local/bin:/usr/bin:/bin"}
        env.pop("DISPLAY", None)
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
            cls.proc.kill()
            raise RuntimeError("wfd.py did not come up on :%d" % cls.port)
        with open(os.path.join(cfg, "key")) as f:
            cls.key = f.read().strip()

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    # ---- plumbing --------------------------------------------------------
    @classmethod
    def raw(cls, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(cls.url[0], cls.url[1], timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    @classmethod
    def req(cls, method, path, body=None, headers=None, auth=True):
        h = dict(headers or {})
        if auth:
            h["X-WF-Key"] = cls.key
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        return cls.raw(method, path, body=body, headers=h)

    def _json(self, status_headers_body):
        status, _, body = status_headers_body
        try:
            return status, json.loads(body or b"{}")
        except Exception:
            return status, {}

    # ---- auth gate -------------------------------------------------------
    def test_act_run_403_without_key(self):
        status, _, _ = self.req("POST", "/act/run",
                                body={"action": {"kind": "builtin"}},
                                auth=False)
        self.assertEqual(status, 403)

    def test_act_pointer_403_without_key(self):
        status, _, _ = self.req("POST", "/act/pointer",
                                body={"op": "move", "x": 0.5, "y": 0.5},
                                auth=False)
        self.assertEqual(status, 403)

    # ---- validation ------------------------------------------------------
    def test_pointer_bad_op_400(self):
        status, obj = self._json(
            self.req("POST", "/act/pointer", body={"op": "teleport"}))
        self.assertEqual(status, 400)
        self.assertFalse(obj.get("ok"))

    def test_pointer_bad_x_400(self):
        status, obj = self._json(
            self.req("POST", "/act/pointer",
                     body={"op": "move", "x": 2.5, "y": 0.5}))
        self.assertEqual(status, 400)

    def test_run_non_dict_action_400(self):
        status, _ = self._json(
            self.req("POST", "/act/run", body={"action": "chrome"}))
        self.assertEqual(status, 400)
        status, _ = self._json(
            self.req("POST", "/act/run", body={"action": None}))
        self.assertEqual(status, 400)

    # ---- body caps -------------------------------------------------------
    def test_pointer_413_over_cap(self):
        big = b'{"op":"move","pad":"' + b"a" * 5000 + b'"}'
        status, _, _ = self.raw("POST", "/act/pointer", body=big,
                                headers={"X-WF-Key": self.key,
                                         "Content-Type": "application/json"})
        self.assertEqual(status, 413)

    def test_run_413_over_cap(self):
        big = b'{"action":{"pad":"' + b"a" * 20000 + b'"}}'
        status, _, _ = self.raw("POST", "/act/run", body=big,
                                headers={"X-WF-Key": self.key,
                                         "Content-Type": "application/json"})
        self.assertEqual(status, 413)

    # ---- pointer degrades, never 500 ------------------------------------
    def test_pointer_ok_false_no_display(self):
        status, obj = self._json(
            self.req("POST", "/act/pointer",
                     body={"op": "move", "x": 0.25, "y": 0.5}))
        self.assertEqual(status, 200)       # not 500
        self.assertFalse(obj.get("ok"))     # no DISPLAY -> ok:false
        self.assertIn("error", obj)

    # ---- rate limit ------------------------------------------------------
    def test_pointer_rate_limit_429(self):
        # a burst well past 90/s from one peer; a 429 must appear
        saw_429 = False
        for _ in range(140):
            status, _, _ = self.req("POST", "/act/pointer",
                                    body={"op": "move", "x": 0.5, "y": 0.5})
            if status == 429:
                saw_429 = True
                break
        self.assertTrue(saw_429, "expected a 429 within a 140-req burst")

    # ---- tier-2 parks pending, shows in /live/state ----------------------
    def test_run_tier2_pending_in_state(self):
        action = {"kind": "builtin", "tool": "open.url", "type": "open_url",
                  "args": {"url": "https://www.youtube.com"},
                  "label": "open YouTube (danger)", "tier": 3}
        status, obj = self._json(
            self.req("POST", "/act/run",
                     body={"action": action, "label": "open YouTube"}))
        self.assertEqual(status, 200)
        pid = obj.get("pending")
        self.assertTrue(pid, "tier>=2 action must park a pending confirm")
        # the pending row shows up in /live/state
        status, st = self._json(self.req("GET", "/live/state"))
        self.assertEqual(status, 200)
        ids = [row.get("id") for row in st.get("pending", [])]
        self.assertIn(pid, ids)

    # ---- /act/run feed event --------------------------------------------
    def test_run_feed_event(self):
        action = {"kind": "builtin", "tool": "open.url", "type": "open_url",
                  "args": {"url": "https://www.google.com"},
                  "label": "open Chrome", "tier": 1}
        status, _ = self._json(
            self.req("POST", "/act/run", body={"action": action}))
        self.assertEqual(status, 200)
        status, st = self._json(self.req("GET", "/live/state"))
        self.assertEqual(status, 200)
        fired = [e for e in st.get("feed", [])
                 if e.get("type") == "fired" and "PHONE" in (e.get("glosses") or [])]
        self.assertTrue(fired, "an /act/run 'fired' event must land in the feed")


if __name__ == "__main__":
    unittest.main()
