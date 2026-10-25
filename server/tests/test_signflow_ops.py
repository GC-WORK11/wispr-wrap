#!/usr/bin/env python3
"""Tests for signflow_ops.py — all synthetic: stub bank, stub router,
stub sink. 0 real spawns, 0 filesystem writes outside tmp dirs.

Covers: composition window timing (in/out/exact-boundary), longest-match
and dead-end resolution, single fallback, profile isolation (same sign,
different action per profile; profile-sign switching; bank-file
persistence), observe-mode suppression, stats accuracy, eval counters,
JSON hot-reload (good + corrupt edits), thread-safety smoke, and the
no-subprocess purity rule.

Run: python3 tests/test_signflow_ops.py  (from server/) or python3 -m unittest
"""
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signflow_ops as so
import signbank as sb


# ------------------------------------------------------------------
# stubs — no real bank, no real spawns
# ------------------------------------------------------------------

class FakeBank:
    """Minimal stand-in for SignBank: ops only needs list() +
    set_action() + reject_threshold for binding lookups and near flags."""
    def __init__(self, threshold=0.6):
        self.rows = {}
        self.reject_threshold = threshold

    def add(self, gloss, action=None, label=""):
        self.rows[gloss] = {"gloss": gloss, "label": label,
                            "action": action, "tier": 0, "n_exemplars": 1}

    def list(self):
        return list(self.rows.values())

    def set_action(self, gloss, label=None, action=None, tier=None):
        for row in self.rows.values():
            if row["gloss"].lower() == str(gloss).lower():
                if action is not None:
                    row["action"] = action
                if label is not None:
                    row["label"] = label
                if tier is not None:
                    row["tier"] = int(tier)
                return True
        return False


def harness(bank=None, path=None, window_ms=1200, autoload=None):
    """(ops, sink_events, router_calls). compositions_path=None by
    default — tests never touch the real ~/.config/wf/."""
    events, calls = [], []
    ops = so.SignFlowOps(
        bank=bank if bank is not None else FakeBank(),
        action_router=lambda a, c: calls.append((a, c)) or {"ok": True},
        event_sink=events.append,
        window_ms=window_ms,
        compositions_path=path,
        autoload=(path is not None) if autoload is None else autoload)
    return ops, events, calls


def types(evs):
    return [e["type"] for e in evs]


def fired(calls):
    return [a for a, _c in calls]


# ------------------------------------------------------------------
# 1. compositional sequences
# ------------------------------------------------------------------

class Composition(unittest.TestCase):
    def setUp(self):
        self.act_open = {"kind": "builtin", "tool": "app.open",
                         "label": "open", "tier": 1}
        self.act_browser = {"kind": "builtin", "tool": "key.press",
                            "args": {"keys": "ctrl+t"}, "tier": 1}
        self.act_ob = {"kind": "app", "tool": "app.open",
                       "label": "chrome", "argv": ["google-chrome"],
                       "tier": 1}
        self.bank = FakeBank()
        self.bank.add("OPEN", self.act_open, "open")
        self.bank.add("BROWSER", self.act_browser, "browser")
        self.bank.add("FOO", {"kind": "builtin", "tool": "key.press",
                              "args": {"keys": "F"}, "tier": 1})
        self.bank.add("NEXT", {"kind": "media", "tool": "media.next",
                               "tier": 1})

    def ops_with(self, table):
        ops, evs, calls = harness(self.bank)
        for k, v in table.items():
            ops.set_composition(k, v)
        events_start = len(evs)      # ignore set_composition notices
        return ops, evs, calls, events_start

    def test_single_fires_immediately(self):
        ops, evs, calls = harness(self.bank)     # empty table
        self.assertEqual(types(ops.on_match("OPEN", 0.4, 1000)),
                         ["match", "fire"])
        self.assertEqual(fired(calls), [self.act_open])
        self.assertEqual(calls[0][1]["kind"], "single")
        s = ops.stats()
        self.assertEqual(s["fires"], 1)
        self.assertEqual(s["single_fires"], 1)
        self.assertEqual(s["sequence_fires"], 0)
        self.assertEqual(s["accepted"], 1)

    def test_composition_within_window(self):
        ops, evs, calls, i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ev1 = ops.on_match("OPEN", 0.4, 1000)
        # held as prefix: reported, partial shown, NOTHING fired yet
        self.assertEqual(types(ev1), ["match", "partial"])
        self.assertEqual(ev1[-1]["seq"], ["OPEN"])
        self.assertEqual(ev1[-1]["next"], ["BROWSER"])
        self.assertFalse(ev1[-1]["exact"])
        self.assertEqual(calls, [])
        ev2 = ops.on_match("BROWSER", 0.5, 1800)
        self.assertEqual(types(ev2), ["match", "fire"])
        fev = ev2[-1]
        self.assertEqual(fev["kind"], "sequence")
        self.assertEqual(fev["glosses"], ["OPEN", "BROWSER"])
        self.assertEqual(fev["action"], self.act_ob)
        # the composed action fired ONCE; the singles' own actions didn't
        self.assertEqual(fired(calls), [self.act_ob])
        self.assertEqual(calls[0][1]["kind"], "sequence")
        s = ops.stats()
        self.assertEqual(s["sequence_fires"], 1)
        self.assertEqual(s["single_fires"], 0)

    def test_outside_window_falls_back_to_singles(self):
        ops, _evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)            # held as prefix
        ops.on_match("BROWSER", 0.5, 1000 + 1201)  # gap > window
        # OPEN resolves alone, BROWSER seeds fresh -> two singles
        self.assertEqual(fired(calls), [self.act_open, self.act_browser])
        self.assertTrue(all(c["kind"] == "single" for _a, c in calls))

    def test_gap_exactly_window_composes(self):
        ops, _evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)
        ops.on_match("BROWSER", 0.5, 1000 + 1200)  # boundary: inside
        self.assertEqual(fired(calls), [self.act_ob])

    def test_held_prefix_expires_via_tick(self):
        ops, evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)            # held
        ev = ops.tick(1000 + 1201)                 # past due_ms
        self.assertEqual(types(ev), ["fire"])
        self.assertEqual(fired(calls), [self.act_open])  # single fallback
        self.assertEqual(ops.state()["pending"], [])

    def test_tick_too_early_keeps_hold(self):
        ops, _evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)
        self.assertEqual(ops.tick(1000 + 1200), [])
        self.assertEqual(calls, [])
        self.assertEqual(ops.state()["pending"], ["OPEN"])

    def test_flush_forces_resolution(self):
        ops, evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)
        ev = ops.flush()
        self.assertEqual(types(ev), ["fire"])
        self.assertEqual(fired(calls), [self.act_open])

    def test_exact_and_prefix_waits_for_longest(self):
        # "NEXT" alone is an entry AND a prefix of "NEXT SLIDE"
        ops, evs, calls, _i = self.ops_with({
            "NEXT": self.act_open,
            "NEXT SLIDE": self.act_ob})
        ev = ops.on_match("NEXT", 0.4, 1000)
        self.assertEqual(types(ev), ["match", "partial"])
        self.assertTrue(ev[-1]["exact"])           # resolvable already
        self.assertEqual(calls, [])
        ev = ops.tick(1000 + 1300)
        self.assertEqual(types(ev), ["fire"])
        self.assertEqual(fired(calls), [self.act_open])   # "NEXT" comp, not bound
        self.assertEqual(calls[0][1]["kind"], "sequence")

    def test_exact_and_prefix_extends(self):
        ops, _evs, calls, _i = self.ops_with({
            "NEXT": self.act_open,
            "NEXT SLIDE": self.act_ob})
        ops.on_match("NEXT", 0.4, 1000)
        ops.on_match("SLIDE", 0.5, 1600)
        self.assertEqual(fired(calls), [self.act_ob])     # longer wins

    def test_three_sign_chain(self):
        ops, evs, calls, _i = self.ops_with({
            "OPEN BROWSER TAB": self.act_ob})
        e1 = ops.on_match("OPEN", 0.4, 1000)
        e2 = ops.on_match("BROWSER", 0.4, 1500)
        e3 = ops.on_match("TAB", 0.4, 2000)
        self.assertEqual(e1[-1]["next"], ["BROWSER"])
        self.assertEqual(e2[-1]["next"], ["TAB"])
        self.assertEqual(types(e3), ["match", "fire"])
        self.assertEqual(e3[-1]["glosses"], ["OPEN", "BROWSER", "TAB"])
        self.assertEqual(fired(calls), [self.act_ob])

    def test_dead_end_reseeds(self):
        # OPEN is a prefix; FOO can't extend it -> OPEN fires single,
        # FOO fires its own single immediately.
        ops, evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)
        ev = ops.on_match("FOO", 0.5, 1500)
        self.assertEqual(types(ev), ["match", "fire", "fire"])
        self.assertEqual(fired(calls),
                         [self.act_open,
                          {"kind": "builtin", "tool": "key.press",
                           "args": {"keys": "F"}, "tier": 1}])

    def test_rejected_match_never_composes(self):
        ops, _evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.on_match("OPEN", 0.4, 1000)
        ev = ops.on_match("SWIPE", 0.99, 1200, accepted=False,
                          reason="threshold")
        self.assertEqual(types(ev), ["reject"])
        self.assertTrue(ev[-1]["near"])            # 0.99 <= 0.6*1.5
        ops.on_match("BROWSER", 0.5, 1900)
        self.assertEqual(fired(calls), [self.act_ob])     # still composed

    def test_matchresult_input(self):
        ops, evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        r = sb.MatchResult(gloss="OPEN", dist=0.4, accepted=True,
                           action=self.act_open, label="open",
                           reason="ok", second_gloss="FOO", margin=0.2)
        ev = ops.on_match(r, t_ms=1000)
        self.assertEqual(types(ev), ["match", "partial"])
        self.assertEqual(ev[0]["second_gloss"], "FOO")
        self.assertAlmostEqual(ev[0]["margin"], 0.2)

    def test_case_insensitive_keys(self):
        ops, _evs, calls, _i = self.ops_with({"open  browser": self.act_ob})
        ops.on_match("Open", 0.4, 1000)
        ops.on_match("BROWSER", 0.4, 1600)
        self.assertEqual(fired(calls), [self.act_ob])

    def test_single_gloss_composition_beats_bound_action(self):
        ops, _evs, calls, _i = self.ops_with({"NEXT": self.act_ob})
        ops.on_match("NEXT", 0.4, 1000)
        self.assertEqual(fired(calls), [self.act_ob])   # table wins

    def test_unbound_single_reports_not_fires(self):
        self.bank.add("GHOST")                     # no action bound
        ops, evs, calls = harness(self.bank)
        ev = ops.on_match("GHOST", 0.4, 1000)
        self.assertEqual(types(ev), ["match", "unbound"])
        self.assertEqual(calls, [])
        self.assertEqual(ops.stats()["unbound"], 1)

    def test_window_zero_disables_holding(self):
        ops, _evs, calls, _i = self.ops_with({"OPEN BROWSER": self.act_ob})
        ops.window_ms = 0
        ops.on_match("OPEN", 0.4, 1000)
        ops.on_match("BROWSER", 0.4, 1010)
        self.assertEqual(fired(calls), [self.act_open, self.act_browser])

    def test_no_router_still_reports(self):
        bank = FakeBank()
        bank.add("OPEN", self.act_open)
        evs = []
        ops = so.SignFlowOps(bank=bank, action_router=None,
                             event_sink=evs.append,
                             compositions_path=None)
        ev = ops.on_match("OPEN", 0.4, 1000)
        self.assertEqual(types(ev), ["match", "fire"])
        self.assertIsNone(ev[-1]["ok"])
        self.assertFalse(ev[-1]["routed"])

    def test_router_error_surfaces(self):
        def bad(_a, _c):
            raise RuntimeError("spawn failed")
        evs = []
        bank = FakeBank()
        bank.add("OPEN", self.act_open)
        ops = so.SignFlowOps(bank=bank, action_router=bad,
                             event_sink=evs.append,
                             compositions_path=None)
        ev = ops.on_match("OPEN", 0.4, 1000)
        self.assertEqual(ev[-1]["type"], "fire")
        self.assertFalse(ev[-1]["ok"])
        self.assertIn("spawn failed", ev[-1]["error"])
        self.assertEqual(ops.stats()["fire_errors"], 1)


# ------------------------------------------------------------------
# 2. profiles
# ------------------------------------------------------------------

class Profiles(unittest.TestCase):
    def setUp(self):
        self.base = {"kind": "builtin", "tool": "key.press",
                     "args": {"keys": "Right"}, "tier": 1,
                     "profiles": {
                         "cooking": {"kind": "builtin",
                                     "tool": "key.press",
                                     "args": {"keys": "Down"},
                                     "tier": 1}}}
        self.bank = FakeBank()
        self.bank.add("NEXT", self.base, "next")

    def test_same_sign_different_action_per_profile(self):
        ops, _evs, calls = harness(self.bank)
        ops.on_match("NEXT", 0.4, 1000)
        ops.set_profile("cooking")
        ops.on_match("NEXT", 0.4, 2000)
        self.assertEqual(fired(calls)[0]["args"]["keys"], "Right")
        self.assertEqual(fired(calls)[1]["args"]["keys"], "Down")
        self.assertEqual(calls[1][0].get("profiles"), None)  # resolved out
        self.assertEqual(calls[1][1]["profile"], "cooking")

    def test_profile_only_sign_unbound_elsewhere(self):
        bank = FakeBank()
        bank.add("TIMER", {"profiles": {
            "cooking": {"kind": "sys", "tool": "sys.timer", "tier": 1}}})
        ops, evs, calls = harness(bank)
        ev = ops.on_match("TIMER", 0.4, 1000)          # in default: unbound
        self.assertEqual(types(ev), ["match", "unbound"])
        ops.set_profile("cooking")
        ev = ops.on_match("TIMER", 0.4, 2000)          # in cooking: fires
        self.assertEqual(types(ev), ["match", "fire"])
        self.assertEqual(len(calls), 1)

    def test_profile_sign_switches(self):
        bank = FakeBank()
        bank.add("NEXT", self.base)
        bank.add("CHEF", {"kind": "builtin", "tool": "key.press",
                          "args": {"keys": "x"}, "tier": 1})
        ops, evs, calls = harness(bank)
        ops.set_profile_sign("CHEF", "cooking")
        ev = ops.on_match("CHEF", 0.3, 1000)
        self.assertEqual(types(ev), ["match", "profile"])
        self.assertEqual(ev[-1]["profile"], "cooking")
        self.assertEqual(ev[-1]["via"], "sign")
        self.assertEqual(calls, [])                    # sign never fires
        ops.on_match("NEXT", 0.4, 2000)
        self.assertEqual(fired(calls)[0]["args"]["keys"], "Down")
        self.assertEqual(ops.stats()["profile_switches"], 1)

    def test_profile_switch_flushes_pending_under_old(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "builtin", "tool": "key.press",
                          "args": {"keys": "O"}, "tier": 1,
                          "profiles": {"cooking": {
                              "kind": "builtin", "tool": "key.press",
                              "args": {"keys": "OCOOK"}, "tier": 1}}})
        bank.add("CHEF")
        ops, evs, calls = harness(bank)
        ops.set_composition("OPEN BROWSER", self.base)
        ops.set_profile_sign("CHEF", "cooking")
        ops.on_match("OPEN", 0.4, 1000)        # held under default
        ops.on_match("CHEF", 0.3, 1100)        # switch: OPEN resolves first
        # OPEN's single fired with the DEFAULT (Right) action, not cooking's
        self.assertEqual(fired(calls)[0]["args"]["keys"], "O")

    def test_file_profile_signs(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "compositions.json")
            with open(p, "w") as f:
                json.dump({"profile_signs": {"CHEF": "cooking"},
                           "sequences": {}}, f)
            ops, evs, calls = harness(self.bank, path=p)
            ev = ops.on_match("CHEF", 0.3, 1000)
            self.assertEqual(types(ev), ["match", "profile"])
            self.assertEqual(ops.profile, "cooking")

    def test_persisted_profile_in_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "compositions.json")
            with open(p, "w") as f:
                json.dump({"profile": "cooking", "sequences": {}}, f)
            ops, evs, _c = harness(self.bank, path=p)
            self.assertEqual(ops.profile, "cooking")
            self.assertIn(("profile"), types(evs))

    def test_bind_writes_profile_variant_into_bank(self):
        bank = FakeBank()
        bank.add("NEXT", {"kind": "builtin", "tool": "key.press",
                          "args": {"keys": "Right"}, "tier": 1})
        ops, _e, calls = harness(bank)
        self.assertTrue(ops.bind("NEXT",
                                 {"kind": "builtin", "tool": "key.press",
                                  "args": {"keys": "Down"}, "tier": 1},
                                 profile="cooking"))
        row = bank.rows["NEXT"]
        self.assertEqual(row["action"]["profiles"]["cooking"]
                         ["args"]["keys"], "Down")
        ops.set_profile("cooking")
        ops.on_match("NEXT", 0.4, 1000)
        self.assertEqual(fired(calls)[0]["args"]["keys"], "Down")

    def test_profile_bindings_survive_bank_roundtrip(self):
        # the honest persistence claim: profiles live inside the bank
        # file's action JSON — save/load must keep them.
        b = sb.SignBank()
        b.templates.append({"gloss": "NEXT", "label": "n", "tier": 0,
                            "action": self.base, "user_samples": 0,
                            "samples": []})
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "signbank.json")
            b.save(p)
            b2 = sb.SignBank.load(p)
        act = [r for r in b2.list() if r["gloss"] == "NEXT"][0]["action"]
        self.assertEqual(act["profiles"]["cooking"]["args"]["keys"], "Down")
        ops, _e, calls = harness(FakeBank())  # router check via resolve
        ops.bank = b2
        ops.set_profile("cooking")
        ops.on_match("NEXT", 0.4, 1000)
        self.assertEqual(fired(calls)[0]["args"]["keys"], "Down")

    def test_profiles_listing(self):
        ops, _e, _c = harness(self.bank)
        ops.set_profile_sign("CHEF", "cooking")
        self.assertIn("cooking", ops.profiles())
        self.assertIn("default", ops.profiles())


# ------------------------------------------------------------------
# 3. observe / rehearsal mode
# ------------------------------------------------------------------

class ObserveMode(unittest.TestCase):
    def test_observe_never_calls_router(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "builtin", "tool": "app.open", "tier": 1})
        bank.add("CHEF", {"kind": "builtin", "tool": "key.press", "tier": 1})
        ops, evs, calls = harness(bank)
        ops.set_composition("OPEN BROWSER", {"kind": "app", "tier": 1})
        ops.set_profile_sign("CHEF", "cooking")
        ops.set_observe(True)
        ev1 = ops.on_match("OPEN", 0.4, 1000)
        ev2 = ops.on_match("BROWSER", 0.4, 1500)
        ops.on_match("CHEF", 0.3, 2200)
        ops.on_match("OPEN", 0.4, 3000)         # would single-fire
        self.assertEqual(calls, [])              # NOTHING routed, ever
        self.assertEqual(types(ev2), ["match", "would-fire"])
        self.assertEqual(ev2[-1]["action"]["kind"], "app")
        self.assertIn("profile", types(evs))
        s = ops.stats()
        self.assertEqual(s["would_fire"], 2)
        self.assertEqual(s["fires"], 0)
        self.assertEqual(s["profile_switches"], 1)
        # observe still resolves pending -> state stays consistent
        self.assertEqual(ops.state()["pending"], [])

    def test_observe_eval_counters(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "x"})
        ops, evs, calls = harness(bank)
        ops.set_observe(True)
        ops.on_match("OPEN", 0.4, 1000, expected="OPEN")      # right
        ops.on_match("OPEN", 0.4, 1100, expected="BROWSER")   # wrong accept
        ops.on_match("MUD", 0.9, 1200, accepted=False,
                     reason="threshold", expected="MUD")      # wrong reject
        ops.on_match("MUD", 0.9, 1300, accepted=False,
                     reason="threshold", expected=None)       # correct reject
        s = ops.stats()
        self.assertEqual(s["eval_n"], 4)
        self.assertEqual(s["eval_correct"], 2)
        self.assertEqual(s["wrong_accept"], 1)
        self.assertEqual(s["wrong_reject"], 1)
        self.assertEqual(s["correct_reject"], 1)
        self.assertIn("eval", types(evs))       # surfaced for the strip


# ------------------------------------------------------------------
# 4. stats + feedback ring
# ------------------------------------------------------------------

class StatsAndFeed(unittest.TestCase):
    def test_counters_and_recent(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "x"})
        ops, evs, calls = harness(bank)
        ops.set_composition("A B", {"kind": "y"})
        ops.on_match("OPEN", 0.4, 1000)                      # fire
        ops.on_match("NOPE", 0.9, 1100, accepted=False,
                     reason="threshold")                     # reject
        ops.on_match("VETOED", 0.5, 1200, accepted=False,
                     reason="veto")                          # reject
        s = ops.stats()
        self.assertEqual(s["accepted"], 1)
        self.assertEqual(s["rejected"], 2)
        self.assertEqual(s["reject_reasons"],
                         {"threshold": 1, "veto": 1})
        self.assertEqual(s["fires"], 1)
        self.assertGreaterEqual(len(ops.state()["recent"]), 4)
        kinds = [e["type"] for e in ops.state()["recent"]]
        for t in ("match", "fire", "reject"):
            self.assertIn(t, kinds)

    def test_reset_session(self):
        ops, _e, _c = harness(FakeBank())
        ops.on_match("X", 0.4, 1000)
        ops.reset_session()
        s = ops.stats()
        self.assertEqual(s["accepted"], 0)
        self.assertEqual(s["events"], 1)        # the reset event itself
        self.assertEqual(ops.state()["recent"][0]["type"], "session")


# ------------------------------------------------------------------
# 5. compositions.json: load + hot-reload + save
# ------------------------------------------------------------------

class CompositionsFile(unittest.TestCase):
    def test_load_on_start(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "b1"})
        bank.add("BROWSER", {"kind": "b2"})
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "compositions.json")
            with open(p, "w") as f:
                json.dump({"window_ms": 900,
                           "sequences": {"OPEN BROWSER": {"kind": "ob"}}}, f)
            ops, evs, calls = harness(bank, path=p)
            self.assertEqual(ops.window_ms, 900)
            ops.on_match("OPEN", 0.4, 1000)
            ops.on_match("BROWSER", 0.4, 1800)   # within 900
            self.assertEqual(fired(calls)[0]["kind"], "ob")
            comp_ev = [e for e in evs if e["type"] == "compositions"]
            self.assertTrue(comp_ev and comp_ev[0]["ok"])

    def test_hot_reload_picks_up_edit(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "b1"})
        bank.add("BROWSER", {"kind": "b2"})
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "compositions.json")
            with open(p, "w") as f:
                json.dump({"sequences": {"OPEN BROWSER": {"kind": "ob"}}}, f)
            ops, _e, calls = harness(bank, path=p)
            ops.on_match("OPEN", 0.4, 1000)
            self.assertEqual(ops.state()["pending"], ["OPEN"])   # held
            # delete the sequence -> OPEN is no longer a prefix
            with open(p, "w") as f:
                json.dump({"sequences": {}}, f)
            os.utime(p)
            ops.tick(1010)
            ev = ops.on_match("BROWSER", 0.4, 1020)
            # stale hold resolved as single on the boundary, BROWSER single
            self.assertEqual([a["kind"] for a in fired(calls)],
                             ["b1", "b2"])
            self.assertEqual(ops.state()["pending"], [])

    def test_corrupt_file_keeps_last_good(self):
        bank = FakeBank()
        bank.add("OPEN", {"kind": "b1"})
        bank.add("BROWSER", {"kind": "b2"})
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "compositions.json")
            with open(p, "w") as f:
                json.dump({"sequences": {"OPEN BROWSER": {"kind": "ob"}}}, f)
            ops, evs, calls = harness(bank, path=p)
            with open(p, "w") as f:
                f.write("{broken json")
            os.utime(p)
            ops.tick(2000)
            comp_ev = [e for e in evs if e["type"] == "compositions"]
            self.assertTrue(any(not e["ok"] for e in comp_ev))
            # table survived: composition still resolves
            ops.on_match("OPEN", 0.4, 3000)
            ops.on_match("BROWSER", 0.4, 3400)
            self.assertEqual(fired(calls)[0]["kind"], "ob")

    def test_save_roundtrip_and_mode600(self):
        ops, _e, calls = harness(FakeBank())
        ops.set_composition("OPEN BROWSER", {"kind": "ob"})
        ops.set_profile_sign("CHEF", "cooking")
        ops.set_profile("cooking")
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "compositions.json")
            ops.save_compositions(p)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            bank = FakeBank()
            bank.add("OPEN", {"kind": "b1"})
            bank.add("BROWSER", {"kind": "b2"})
            ops2, _e2, calls2 = harness(bank, path=p)
            self.assertEqual(ops2.profile, "cooking")      # persisted
            self.assertEqual(ops2.profile_signs()["CHEF"], "cooking")
            ops2.on_match("OPEN", 0.4, 1000)
            ops2.on_match("BROWSER", 0.4, 1500)
            self.assertEqual(fired(calls2)[0]["kind"], "ob")


# ------------------------------------------------------------------
# 6. threading + module purity
# ------------------------------------------------------------------

class ThreadingAndPurity(unittest.TestCase):
    def test_concurrent_feed_and_tick(self):
        bank = FakeBank()
        for g in ("A", "B", "C"):
            bank.add(g, {"kind": g})
        ops, _e, calls = harness(bank)
        ops.set_composition("A B", {"kind": "ab"})
        errs = []

        def feed(gloss, t0):
            try:
                for i in range(40):
                    ops.on_match(gloss, 0.4, t0 + i * 50)
                    ops.tick(t0 + i * 50)
            except Exception as e:
                errs.append(e)

        ts = [threading.Thread(target=feed, args=(g, 1000 + n * 17))
              for n, g in enumerate("ABC")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        s = ops.stats()
        self.assertEqual(s["accepted"], 120)
        self.assertEqual(s["fires"], s["single_fires"] +
                         s["sequence_fires"])

    def test_module_never_imports_subprocess(self):
        src = open(so.__file__).read()
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("subprocess.", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
