"""Backend fixes the Go Live screen depends on.

(a) TEXT mode word: a labelled sign types its label as-is (stripped); an
    unlabelled sign humanizes the gloss ('OPEN-NOTES' -> 'open notes'),
    and the typed word is carried on both match.action.typed and
    last_action.typed.
(b) A client that hangs up mid-reply (BrokenPipeError /
    ConnectionResetError while the handler writes the body) must not dump
    a Python traceback — those two are swallowed, anything else still
    propagates.
"""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wfd  # noqa: E402


def _pts(x, y, n=42):
    return [{"x": x, "y": y, "score": 0.9} for _ in range(n)]


class _StubEP:
    current_len = 0
    active = False

    def feed(self, frame):
        return []


def _fresh():
    wfd.LIVE.update(session=None, listening=False, last_match=None,
                    seen=0.0, last_accept=None, dup_fires=0,
                    observe=False, text_mode=False, last_action=None)
    wfd.LIVE["rates"].clear()
    wfd.LIVE["rate_all"].clear()
    wfd.LIVE["feed"].clear()
    wfd.LIVE["ep"] = _StubEP()
    wfd.TEACH.update(active=False, name="", takes=0, got=0,
                     label="", action=None, session=None)


class _Match:
    def __init__(self, gloss, label):
        self.gloss = gloss
        self.label = label
        self.dist = 0.01
        self.accepted = True
        self.score = 0.9
        self.margin = 0.5
        self.second_gloss = None
        self.reason = ""
        self.action = None
        self.tier = 0


class _MatchBank:
    def __init__(self, gloss, label):
        self._m = _Match(gloss, label)

    def match(self, frames):
        return self._m


class TestWordFor(unittest.TestCase):
    """The pure word-mapping helper — the heart of fix (a)."""

    def test_unlabelled_gloss_humanized(self):
        self.assertEqual(wfd._word_for("", "OPEN-NOTES"), "open notes")
        self.assertEqual(wfd._word_for(None, "OPEN_NOTES"), "open notes")
        self.assertEqual(wfd._word_for("", "HELLO"), "hello")

    def test_label_typed_as_is_stripped(self):
        self.assertEqual(wfd._word_for("Open Notes", "OPEN-NOTES"),
                         "Open Notes")
        self.assertEqual(wfd._word_for("  hey there  ", "X"), "hey there")

    def test_blank_label_falls_through_to_gloss(self):
        self.assertEqual(wfd._word_for("   ", "SWIPE-UP"), "swipe up")


class TestTextModeTypedWord(unittest.TestCase):
    def setUp(self):
        _fresh()
        self._bank, self._ops = wfd.BANK, wfd.OPS
        wfd.OPS = None
        wfd.LIVE["listening"] = True
        wfd.LIVE["session"] = "sess-A"
        wfd.LIVE["text_mode"] = True
        # the act queue is a module global shared across tests — drain any
        # job a sibling test left behind so get() returns ours.
        try:
            while True:
                wfd._ACT_Q.get_nowait()
        except Exception:
            pass

    def tearDown(self):
        wfd.BANK, wfd.OPS = self._bank, self._ops
        _fresh()

    def _seg(self, gloss, label):
        wfd.BANK = _MatchBank(gloss, label)
        fr = [{"t_ms": i * 66, "points": _pts(0.4 + i * 0.01, 0.5, 21)}
              for i in range(8)]
        return wfd._on_segment(fr, fr[-1]["t_ms"])

    def test_unlabelled_gloss_types_human_word(self):
        out = self._seg("OPEN-NOTES", "")
        act = (out.get("match") or {}).get("action") or {}
        self.assertEqual(act.get("kind"), "text")
        self.assertEqual(act.get("typed"), "open notes")
        # last_action carries the same word
        self.assertEqual(wfd.LIVE["last_action"]["typed"], "open notes")
        # and that is exactly what was queued to type (+ trailing space)
        job = wfd._ACT_Q.get(timeout=1)
        captured = {}
        orig = wfd.send_text
        wfd.send_text = lambda text, mode: captured.update(text=text,
                                                           mode=mode)
        try:
            job()
        finally:
            wfd.send_text = orig
        self.assertEqual(captured["text"], "open notes ")
        self.assertEqual(captured["mode"], "live-text")

    def test_label_typed_as_is(self):
        out = self._seg("OPEN-NOTES", "Launch Notes")
        act = (out.get("match") or {}).get("action") or {}
        self.assertEqual(act.get("typed"), "Launch Notes")
        self.assertEqual(wfd.LIVE["last_action"]["typed"], "Launch Notes")


class _FakeSock(io.BytesIO):
    """A wfile that raises the given error on write, like a hung-up peer."""

    def __init__(self, err):
        super().__init__()
        self._err = err

    def write(self, b):
        raise self._err


class _BareHandler(wfd.Handler):
    """Bypass BaseHTTPRequestHandler.__init__ (which would try to serve a
    request); we only exercise _reply's body-write path."""

    def __init__(self, wfile):
        self.wfile = wfile
        self._sent = []

    # neutralize the header machinery so _reply reaches the body write
    def send_response(self, *a, **k):
        pass

    def send_header(self, *a, **k):
        pass

    def end_headers(self, *a, **k):
        pass


class TestReplySwallowsHangup(unittest.TestCase):
    def test_broken_pipe_swallowed(self):
        h = _BareHandler(_FakeSock(BrokenPipeError()))
        h._reply(200, b"body")   # must not raise

    def test_connection_reset_swallowed(self):
        h = _BareHandler(_FakeSock(ConnectionResetError()))
        h._reply(200, b"body")   # must not raise

    def test_jreply_swallows_too(self):
        h = _BareHandler(_FakeSock(BrokenPipeError()))
        h._jreply(200, {"a": 1})  # delegates to _reply; must not raise

    def test_other_errors_still_raise(self):
        h = _BareHandler(_FakeSock(OSError("disk full")))
        with self.assertRaises(OSError):
            h._reply(200, b"body")


if __name__ == "__main__":
    unittest.main()
