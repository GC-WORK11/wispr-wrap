#!/usr/bin/env python3
"""Tests for macro.py — validation, serial execution, stop-on-fail,
optional-step continue, total timeout, and the type/keys focus guard.

Uses a FakeProvider (stub class — never real xdotool): records every call
so we can assert order and that failed-focus steps never reach run().

Run: python3 tests/test_macro.py  (from server/) or python3 -m unittest
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import macro


class FakeResult:
    __slots__ = ("ok", "detail", "tier", "confirm_id")

    def __init__(self, ok, detail="", tier=1):
        self.ok, self.detail, self.tier, self.confirm_id = ok, detail, tier, None


class FakeProvider:
    """Stub ActionProvider — records calls, scripted failures."""

    def __init__(self, focus=True, fail_tools=()):
        self.focus = focus
        self.calls = []
        self.fail_tools = set(fail_tools)

    def verify_focus(self, ctx=None):
        self.calls.append(("verify_focus", ctx))
        return {"ok": self.focus, "class": "firefox",
                "note": "" if self.focus else "focus lost"}

    def run(self, action, ctx=None):
        self.calls.append(("run", action))
        tool = (action or {}).get("tool", "")
        if tool in self.fail_tools:
            return FakeResult(False, "scripted fail: %s" % tool)
        return FakeResult(True, "did %s" % tool)


class Clock:
    """Deterministic monotonic clock + sleep for timeout tests."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class ValidationCase(unittest.TestCase):
    def test_bad_step_types_rejected(self):
        bad = [
            "not a list",
            [],
            ["not a dict"],
            [{"nonsense": 1}],                       # no step key / tool
            [{"type": "a", "keys": "b"}],            # ambiguous
            [{"step": "bogus"}],
            [{"type": 3}],                           # text not a string
            [{"keys": "  "}],                        # empty key spec
            [{"argv": "rm -rf /"}],                  # string, not argv list
            [{"argv": []}],
            [{"argv": ["ok", 2]}],                   # non-string member
            [{"wait_ms": -1}],
            [{"wait_ms": "later"}],
            [{"wait_ms": 999_999_999}],              # over cap
            [{"context_dispatch": {"by_wm_class": {}}}],  # no default
            [{"tool": "   "}],                       # empty catalog id
            [{"tool": "sys.mute", "args": [1, 2]}],  # args not object
        ]
        for steps in bad:
            norm, errs = macro.validate_steps(steps)
            self.assertTrue(errs, "expected errors for %r" % (steps,))
            self.assertEqual(norm, [])

    def test_shorthand_forms(self):
        norm, errs = macro.validate_steps([
            {"wait_ms": 250},
            {"type": "hello"},
            {"keys": "ctrl+Tab"},
            {"argv": ["notify-send", "hi"]},
            {"open_app": "firefox"},
            {"context_dispatch": {"default": {"tool": "media.next"},
                                  "by_wm_class": {"firefox": {"tool": "key.press"}}}},
        ])
        self.assertEqual(errs, [])
        self.assertEqual([s["step"] for s in norm],
                         ["wait_ms", "type", "keys", "argv", "open_app",
                          "context_dispatch"])
        self.assertEqual(norm[1]["text"], "hello")
        self.assertEqual(norm[4]["exec"], "firefox")

    def test_contract_tool_form(self):
        norm, errs = macro.validate_steps([
            {"tool": "app.open", "args": {"exec": "firefox"}, "delay_ms": 800,
             "label": "browser up"},
            {"tool": "sys.mute"},
        ])
        self.assertEqual(errs, [])
        self.assertEqual(norm[0]["step"], "tool")
        self.assertEqual(norm[0]["tool"], "app.open")
        self.assertEqual(norm[0]["args"], {"exec": "firefox"})
        self.assertEqual(norm[0]["delay_ms"], 800)


class RunCase(unittest.TestCase):
    def test_serial_run_order_and_results(self):
        p = FakeProvider()
        res = macro.run_macro([
            {"open_app": "firefox", "delay_ms": 0},
            {"type": "hi there"},
            {"keys": "Return"},
            {"tool": "media.next"},
        ], p, _sleep=lambda s: None)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ran"], 4)
        self.assertEqual([r["ok"] for r in res["steps"]],
                         [True, True, True, True])
        runs = [c[1].get("tool") for c in p.calls if c[0] == "run"]
        self.assertEqual(runs, ["app.open", "type.text", "key.press",
                                "media.next"])

    def test_focus_guard_before_type_and_keys(self):
        p = FakeProvider()
        res = macro.run_macro([{"type": "x"}, {"keys": "ctrl+x"}], p,
                              _sleep=lambda s: None)
        self.assertTrue(res["ok"], res)
        kinds = [c[0] for c in p.calls]
        self.assertEqual(kinds, ["verify_focus", "run",
                                 "verify_focus", "run"])

    def test_focus_fail_refuses_type(self):
        p = FakeProvider(focus=False)
        res = macro.run_macro([{"type": "x"}, {"tool": "media.next"}], p,
                              _sleep=lambda s: None)
        self.assertFalse(res["ok"])
        # the type step failed without ever reaching provider.run;
        # media.next is skipped after the failure
        self.assertEqual([c[0] for c in p.calls], ["verify_focus"])
        self.assertIn("focus", res["steps"][0]["detail"])
        self.assertEqual(res["steps"][1]["detail"], "skipped")

    def test_no_focus_guard_means_no_typing(self):
        p = FakeProvider()
        p.verify_focus = None   # provider without a focus guard
        res = macro.run_macro([{"type": "x"}], p, _sleep=lambda s: None)
        self.assertFalse(res["ok"])
        self.assertIn("focus", res["steps"][0]["detail"])
        self.assertEqual(p.calls, [])

    def test_stop_on_fail(self):
        p = FakeProvider(fail_tools={"sys.mute"})
        res = macro.run_macro([
            {"tool": "media.next"},
            {"tool": "sys.mute"},
            {"tool": "media.prev"},
        ], p, _sleep=lambda s: None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["ran"], 2)
        self.assertEqual(res["steps"][2]["detail"], "skipped")
        self.assertFalse(res["steps"][2]["ok"])
        self.assertIn("stopped at step 1", res["error"])

    def test_optional_step_continues(self):
        p = FakeProvider(fail_tools={"sys.mute"})
        res = macro.run_macro([
            {"tool": "sys.mute", "optional": True},
            {"tool": "media.next"},
        ], p, _sleep=lambda s: None)
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["steps"][0]["optional"])
        self.assertIn("scripted fail", res["steps"][0]["detail"])
        self.assertTrue(res["steps"][1]["ok"])
        self.assertIsNone(res["error"])

    def test_total_timeout_skips_remaining(self):
        c = Clock()
        p = FakeProvider()
        res = macro.run_macro([
            {"wait_ms": 60, "optional": True},   # eats the whole 50ms budget
            {"type": "x"},
            {"tool": "media.next"},
        ], p, timeout_s=0.05, _sleep=c.sleep, _now=c.now)
        self.assertFalse(res["ok"])
        self.assertIn("timeout", res["steps"][0]["detail"])
        self.assertEqual(res["steps"][1]["detail"], "timeout")
        self.assertEqual(res["steps"][2]["detail"], "timeout")
        # nothing reached the provider past the deadline
        self.assertEqual(p.calls, [])

    def test_failed_wait_then_skipped(self):
        c = Clock()
        p = FakeProvider()
        res = macro.run_macro([
            {"wait_ms": 100},                    # required — fails on timeout
            {"tool": "media.next"},
        ], p, timeout_s=0.05, _sleep=c.sleep, _now=c.now)
        self.assertFalse(res["ok"])
        self.assertIn("timeout", res["steps"][0]["detail"])
        self.assertEqual(res["steps"][1]["detail"], "skipped")
        self.assertEqual(p.calls, [])

    def test_argv_step_is_tier2_command(self):
        p = FakeProvider()
        res = macro.run_macro([{"argv": ["notify-send", "hi"]}], p,
                              _sleep=lambda s: None)
        self.assertTrue(res["ok"], res)
        action = p.calls[0][1]
        self.assertEqual(action["kind"], "command")
        self.assertEqual(action["tier"], 2)
        self.assertEqual(action["argv"], ["notify-send", "hi"])

    def test_context_dispatch_passthrough(self):
        p = FakeProvider()
        steps = [{"context_dispatch": {
            "default": {"tool": "media.next", "kind": "media"},
            "by_wm_class": {"firefox": {"tool": "key.press",
                                        "args": {"keys": "ctrl+Tab"}}}}}]
        res = macro.run_macro(steps, p, _sleep=lambda s: None)
        self.assertTrue(res["ok"], res)
        action = p.calls[0][1]
        self.assertEqual(action["kind"], "context")
        self.assertIn("firefox", action["by_wm_class"])

    def test_invalid_macro_returns_error_not_exception(self):
        res = macro.run_macro([{"argv": "rm -rf /"}], FakeProvider(),
                              _sleep=lambda s: None)
        self.assertFalse(res["ok"])
        self.assertIn("argv", res["error"])
        self.assertEqual(res["ran"], 0)

    def test_run_macro_action_adapter(self):
        p = FakeProvider()
        r = macro.run_macro_action(
            {"kind": "macro", "tier": 2,
             "steps": [{"tool": "media.next"}]}, p)
        self.assertTrue(getattr(r, "ok", r.get("ok") if isinstance(r, dict)
                                else False), r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
