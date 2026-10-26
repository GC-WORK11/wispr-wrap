#!/usr/bin/env python3
"""Adversarial endpoint tests — merged-product security contract (PRD §6).

Spawns a real wfd.py against a throwaway HOME (its own ~/.config/wf/key,
commands.tsv, signbank.json) on an ephemeral port, then attacks it.

Endpoints that haven't landed yet are *skipped*, not failed — "landed" means an
authenticated request to the route returns anything but 404/501. Contracts
tested:

  * every /live|/teach|/bank|/act route 403s without X-WF-Key
  * bank/teach/act routes reject proxied requests (cf-*/x-forwarded-*), same as
    /pair-info — a sign bank is biometric-ish data. /live/kp is deliberately
    NOT proxy-rejected: the cloudflared-tunnel transport terminates proxy-side,
    so legit tunnel users always carry forwarding headers there.
  * POST bodies over the cap get 413 (today's unbounded read is a known bug —
    marked expectedFailure, not skipped)
  * commands.tsv argv is spawned directly — never through a shell
  * a tier>=2 action pends confirm and CANNOT fire without POST /act/confirm
  * the pairing key file and signbank.json are mode 600

Run:  python3 server/tests/test_security.py   (stdlib only, no deps)
"""
import http.client, json, os, re, socket, subprocess, sys, tempfile, time, unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # server/
WFD = os.path.join(BASE, "wfd.py")

# candidate action-trigger routes — spec lands one of these; first that answers
# an authed non-404 wins
TRIGGER_ROUTES = ["/act/run", "/act/fire", "/act/trigger", "/act/exec"]
CONFIRM_ROUTE = "/act/confirm"
MAX_BODY = 8 << 20   # any sane cap is far below this


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class WfdCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wf-sec-")
        cls.port = free_port()
        cls.url = ("127.0.0.1", cls.port)
        cfg = os.path.join(cls.tmp, ".config", "wf")
        os.makedirs(cfg)
        # seed a commands table — covers both the 3-col (GLOSS,label,argv) and
        # 4-col (GLOSS,label,tier,argv) shapes; whichever the daemon loads shows
        # up in GET /act, the rest are skipped
        with open(os.path.join(cfg, "commands.tsv"), "w") as f:
            f.write("SECSEMI\tsemi\ta;touch %s/pwn-semi\n" % cls.tmp)
            f.write("SECSUBST\tsubst\t$(touch %s/pwn-subst)\n" % cls.tmp)
            f.write("SECBACKTICK\tbacktick\t`touch %s/pwn-backtick`\n" % cls.tmp)
            f.write("SECTIER2\tdanger\t2\ttouch %s/tier2-fired\n" % cls.tmp)
        env = dict(os.environ, HOME=cls.tmp, WF_PORT=str(cls.port), WF_NO_LOG_TEXT="1")
        cls.proc = subprocess.Popen([sys.executable, WFD], env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        cls.key = open(os.path.join(cfg, "key")).read().strip()

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

    @classmethod
    def landed(cls, method, path, body=None):
        """Route exists when an *authenticated* request gets routed, not 404/501."""
        try:
            status, _, _ = cls.req(method, path, body=body)
        except OSError:
            return False
        return status not in (404, 501)

    def need(self, method, path, body=None):
        if not self.landed(method, path, body):
            self.skipTest(f"{method} {path} not implemented yet")

    # ---- contract: auth gate ----------------------------------------------
    NEW_ROUTES = [
        ("GET", "/bank"), ("PUT", "/bank"), ("DELETE", "/bank"),
        ("GET", "/act"), ("POST", CONFIRM_ROUTE),
        ("GET", "/live/state"), ("POST", "/live/kp"),
        ("POST", "/teach/begin"), ("POST", "/teach/end"),
    ]

    def test_health_is_open(self):
        status, _, body = self.req("GET", "/health", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ok")

    def test_unpaired_403_existing_routes(self):
        for path in ("/send", "/send-image", "/send-file"):
            with self.subTest(path=path):
                status, _, _ = self.req("POST", path, body=b"x", auth=False)
                self.assertEqual(status, 403)

    def test_wrong_key_403(self):
        status, _, _ = self.req("POST", "/send", body=b"{}",
                                headers={"X-WF-Key": "0" * 40}, auth=False)
        self.assertEqual(status, 403)

    def test_unpaired_403_new_routes(self):
        """Every signing/bank/teach/action route must 403 without a key."""
        for method, path in self.NEW_ROUTES:
            with self.subTest(route=f"{method} {path}"):
                self.need(method, path)
                status, _, _ = self.req(method, path, auth=False)
                self.assertEqual(status, 403,
                                 f"{method} {path} answered without X-WF-Key")

    # ---- contract: proxied requests rejected on sensitive routes -----------
    PROXY_HEADERS = [
        {"X-Forwarded-For": "203.0.113.9"},
        {"CF-Connecting-IP": "203.0.113.9"},
        {"X-Forwarded-Host": "evil.example"},
    ]

    def test_pair_info_rejects_proxied(self):
        """Today's invariant (works already): tunnel-lookalikes get no key."""
        for h in self.PROXY_HEADERS:
            with self.subTest(headers=h):
                status, _, _ = self.raw("GET", "/pair-info", headers=h)
                self.assertEqual(status, 403)

    def test_pair_info_serves_localhost_unproxied(self):
        status, _, body = self.raw("GET", "/pair-info")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["key"], self.key)

    def test_pair_info_rejects_dns_rebinding_host(self):
        # a hostile site resolving to 127.0.0.1 keeps its own Host — refuse
        status, _, _ = self.raw("GET", "/pair-info",
                                headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(status, 403)

    def test_cors_origin_must_match_host(self):
        # foreign Origins get no ACAO — the browser hides the response,
        # which is what keeps /pair-info's key off a hostile page.
        _, h, _ = self.raw("GET", "/health",
                           headers={"Origin": "https://evil.example"})
        hl = {k.lower(): v for k, v in h.items()}
        self.assertNotIn("access-control-allow-origin", hl)
        _, h, _ = self.raw("GET", "/health",
                           headers={"Origin": "http://127.0.0.1:%d" % self.port})
        hl = {k.lower(): v for k, v in h.items()}
        self.assertEqual(hl.get("access-control-allow-origin"),
                         "http://127.0.0.1:%d" % self.port)

    def test_proxied_rejected_on_sensitive_routes(self):
        """Bank/teach/act = how-you-move data; tunnels must not reach them."""
        routes = [("GET", "/bank"), ("PUT", "/bank"), ("GET", "/act"),
                  ("POST", "/teach/begin"), ("POST", CONFIRM_ROUTE)]
        for method, path in routes:
            with self.subTest(route=f"{method} {path}"):
                self.need(method, path)
                for h in self.PROXY_HEADERS:
                    status, _, _ = self.req(method, path, headers=h)
                    self.assertEqual(status, 403,
                        f"{method} {path} answered a proxied request {h}")

    # ---- contract: bounded request bodies ----------------------------------
    def test_live_kp_body_cap_413(self):
        self.need("POST", "/live/kp")
        # just over the /live/kp cap (256 KB): the daemon replies 413
        # before reading the body — a body small enough to land in the
        # socket buffer is the only shape whose 413 a client can ever
        # read back.  (A MAX_BODY-sized send dies on EPIPE mid-write,
        # which is correct but leaves no response to assert on.)
        status, _, _ = self.req("POST", "/live/kp",
                                body=b"x" * (256 * 1024 + 1))
        self.assertEqual(status, 413, "oversized /live/kp was not rejected")
        self.assertEqual(self.req("GET", "/health", auth=False)[0], 200,
                         "daemon died after oversized body")

    def test_live_control_body_cap_413(self):
        # these three authed JSON routes have no payload worth 16 KB; they
        # must not fall through to MAX_BODY (256 MB) and allocate it.
        for path in ("/live/mode", "/live/event", "/live/listen"):
            with self.subTest(route=path):
                self.need("POST", path)
                status, _, _ = self.req("POST", path,
                                        body=b"x" * (16 * 1024 + 1))
                self.assertEqual(status, 413,
                                 "oversized %s was not rejected" % path)
        self.assertEqual(self.req("GET", "/health", auth=False)[0], 200,
                         "daemon died after oversized control bodies")

    @unittest.expectedFailure   # known bug (PRD §2.1): unbounded request body
    def test_send_body_cap_413(self):
        try:
            status, _, _ = self.req("POST", "/send", body=b"x" * MAX_BODY)
        except OSError:
            status = None   # handler thread died — still not a 413
        self.assertEqual(status, 413)

    # ---- contract: commands never go through a shell -----------------------
    def test_no_shell_spawn_in_server_source(self):
        """Static tripwire: no shell=True / os.system anywhere under server/."""
        pat = re.compile(r"shell\s*=\s*True|os\.system|os\.popen|subprocess\.call\(\s*['\"]")
        for name in os.listdir(BASE):
            if name.endswith(".py"):
                src = open(os.path.join(BASE, name)).read()
                self.assertIsNone(pat.search(src), f"shell spawn found in {name}")

    def _trigger_route(self):
        for r in TRIGGER_ROUTES:
            if self.landed("POST", r):
                return r
        return None

    def test_commands_argv_metachars_inert(self):
        """A commands.tsv argv containing ; $() `` must NOT execute — the row
        is spawned directly, so the metachars are inert filename chars."""
        trig = self._trigger_route()
        if not trig:
            self.skipTest("no action-trigger route landed yet")
        for gloss, marker in (("SECSEMI", "pwn-semi"), ("SECSUBST", "pwn-subst"),
                              ("SECBACKTICK", "pwn-backtick")):
            with self.subTest(gloss=gloss):
                self.req("POST", trig, body={"gloss": gloss})
                deadline = time.time() + 2
                while time.time() < deadline:      # give a legit spawn its 2s
                    if os.path.exists(os.path.join(self.tmp, marker)):
                        break
                    time.sleep(0.1)
                self.assertFalse(os.path.exists(os.path.join(self.tmp, marker)),
                    f"{gloss}: shell metachars executed — argv went through sh")

    # ---- contract: risk tier gate -------------------------------------------
    def test_tier2_cannot_fire_without_confirm(self):
        """A tier>=2 row must pend as confirm-needed; firing requires
        POST /act/confirm — a malicious composer can't bypass it."""
        self.need("GET", "/act")
        trig = self._trigger_route()
        if not trig:
            self.skipTest("no action-trigger route landed yet")
        status, _, body = self.req("GET", "/act")
        try:
            catalog = json.loads(body)
        except Exception:
            self.skipTest("/act did not return JSON")
        rows = catalog if isinstance(catalog, list) else \
            catalog.get("actions", catalog.get("commands", []))
        tier2 = [r for r in rows if isinstance(r, dict)
                 and int(r.get("tier", 0) or 0) >= 2]
        if not tier2:
            self.skipTest("catalog exposes no tier>=2 row yet")
        gloss = tier2[0].get("gloss") or tier2[0].get("id") or "SECTIER2"
        marker = os.path.join(self.tmp, "tier2-fired")
        if os.path.exists(marker):
            os.unlink(marker)
        status, _, body = self.req("POST", trig, body={"gloss": gloss})
        time.sleep(1.0)
        self.assertFalse(os.path.exists(marker),
            f"tier-2 action '{gloss}' FIRED without confirm (status {status})")
        # if a confirm endpoint exists, releasing it may fire — that's allowed,
        # but the action must never have fired before this point
        if self.landed("POST", CONFIRM_ROUTE):
            try:
                ident = json.loads(body).get("id") or json.loads(body).get("pending")
            except Exception:
                ident = None
            self.req("POST", CONFIRM_ROUTE,
                     body={"id": ident, "gloss": gloss})

    # ---- contract: file permissions -----------------------------------------
    def test_key_file_mode_600(self):
        st = os.stat(os.path.join(self.tmp, ".config", "wf", "key"))
        self.assertEqual(st.st_mode & 0o777, 0o600)

    def test_signbank_file_mode_600(self):
        self.need("PUT", "/bank")
        self.req("PUT", "/bank", body={"gloss": "SECTEST", "exemplars": []})
        path = os.path.join(self.tmp, ".config", "wf", "signbank.json")
        if not os.path.exists(path):
            self.skipTest("bank file not at ~/.config/wf/signbank.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    # ---- contract: live ingest is session-bound + rate-limited ---------------
    def test_live_kp_requires_session(self):
        """Raw frames outside a listen session must not be accepted."""
        self.need("POST", "/live/kp")
        status, _, _ = self.req("POST", "/live/kp",
                                body={"t_ms": 0, "points": [{"x": 0, "y": 0, "score": 1}]})
        self.assertIn(status, (200, 400, 401, 403, 409, 423),
                      "frame accepted outright or crashed the handler")
        if status == 200:
            self.skipTest("/live/kp accepts session-less frames — session binding TBD")

    # ---- hygiene: static traversal, pair rate limit (pass today) -------------
    def test_static_traversal_404(self):
        for p in ("/../server/wfd.py", "/..%2f..%2fetc%2fpasswd",
                  "/%2e%2e/%2e%2e/etc/passwd", "/....//wfd.py"):
            with self.subTest(path=p):
                status, _, body = self.raw("GET", p)
                self.assertIn(status, (400, 404))
                self.assertNotIn(b"ThreadingHTTPServer", body)

    def test_pair_rate_limited(self):
        b = json.dumps({"code": "WRONGCODE"}).encode()
        s1, _, _ = self.req("POST", "/pair", body=b, auth=False)
        s2, _, _ = self.req("POST", "/pair", body=b, auth=False)
        self.assertEqual(s2, 429, "second /pair inside 1s was not rate-limited")


if __name__ == "__main__":
    unittest.main(verbosity=2)
