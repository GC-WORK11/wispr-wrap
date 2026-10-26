#!/usr/bin/env python3
"""test_parity — the anti-"one-file-Python" proof for the merged product.

Identical trajectory sets are pushed through BOTH

  (a) server/signbank.py        — the shipping pure-Python DTW bank,
      via tools/e2e-live/bank_adapter.py's contract resolver, and
  (b) tools/e2e-live/refmatch.py — an independent re-implementation of
      the matcher.rs semantics (resample-32, banded DTW band=4 with the
      band-edge fix, best_d <= reject_threshold accept) ported from
      sign-flow's crates/alive-sign/src/matcher.rs,

and the DECISIONS must agree: same argmin winner (including refusal) and
the same accept/reject verdict on every random case. Raw distances are
allowed to differ inside an honest tolerance — the product question is
"does the same sign fire", not "is the float identical" — but every
delta is measured and reported, never hidden.

Run: python3 server/tests/test_parity.py   (stdlib only, no pytest)
Env: PARITY_CASES (default 80), PARITY_SEED (default 19),
     PARITY_DIST_TOL (default 0.02 — loose, informational),
     PARITY_JSON (write a machine-readable summary here)
"""
import json
import math
import os
import random
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, os.path.join(_ROOT, "server"),
           os.path.join(_ROOT, "tools", "e2e-live")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import refmatch  # noqa: E402
import bank_adapter  # noqa: E402

CASES = int(os.environ.get("PARITY_CASES", "80"))
SEED = int(os.environ.get("PARITY_SEED", "19"))
DIST_TOL = float(os.environ.get("PARITY_DIST_TOL", "0.02"))
REJECT_T = 0.45  # matcher.rs production default (TemplateBank::new(0.45))

STATS = {"cases": 0, "argmin_mismatch": 0, "verdict_mismatch": 0,
         "teach_mismatch": 0, "deltas": [], "worst": []}


# ------------------------------------------------------------------------
# trajectory generators — randomized families that exercise real DTW
# discriminations (direction, curvature, corner count), not degenerate
# shapes the argmin can't split.
# ------------------------------------------------------------------------

def _poly(ctrl, n):
    """Piecewise-linear path through control points, n samples."""
    if len(ctrl) < 2:
        return list(ctrl)
    segs = len(ctrl) - 1
    out = []
    for i in range(n):
        pos = i * segs / (n - 1)
        j = min(int(pos), segs - 1)
        t = pos - j
        a, b = ctrl[j], ctrl[j + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def rand_path(rng, n=None):
    n = n or rng.randint(14, 40)
    kind = rng.randrange(5)
    if kind == 0:  # straight-ish line, random direction
        a = (rng.uniform(0.1, 0.6), rng.uniform(0.2, 0.8))
        b = (a[0] + rng.uniform(-0.6, 0.6), a[1] + rng.uniform(-0.5, 0.5))
        return _poly([a, b], n)
    if kind == 1:  # two-segment elbow
        a = (rng.uniform(0.1, 0.5), rng.uniform(0.2, 0.7))
        m = (a[0] + rng.uniform(-0.4, 0.4), a[1] + rng.uniform(-0.35, 0.35))
        b = (m[0] + rng.uniform(-0.4, 0.4), m[1] + rng.uniform(-0.35, 0.35))
        return _poly([a, m, b], n)
    if kind == 2:  # arc / partial circle
        cx, cy, r = rng.uniform(0.35, 0.65), rng.uniform(0.35, 0.65), rng.uniform(0.12, 0.3)
        a0 = rng.uniform(0, 2 * math.pi)
        sweep = rng.uniform(1.2, 2 * math.pi) * rng.choice((-1, 1))
        return [(cx + r * math.cos(a0 + sweep * i / (n - 1)),
                 cy + r * math.sin(a0 + sweep * i / (n - 1))) for i in range(n)]
    if kind == 3:  # three-corner poly (z/n/v shapes)
        x0 = rng.uniform(0.15, 0.4)
        return _poly([(x0, rng.uniform(0.25, 0.45)),
                      (1 - x0, rng.uniform(0.25, 0.45)),
                      (x0, rng.uniform(0.6, 0.8)),
                      (1 - x0, rng.uniform(0.6, 0.8))], n)
    # kind == 4: wave
    x0, x1 = rng.uniform(0.1, 0.3), rng.uniform(0.7, 0.9)
    y0 = rng.uniform(0.35, 0.65)
    amp = rng.uniform(0.04, 0.16)
    cyc = rng.uniform(1.0, 2.5)
    return [(x0 + (x1 - x0) * i / (n - 1),
             y0 + amp * math.sin(cyc * 2 * math.pi * i / (n - 1)))
            for i in range(n)]


def jitter(path, rng, amt=0.012):
    return [(x + rng.gauss(0, amt), y + rng.gauss(0, amt)) for x, y in path]


def warp(path, rng):
    """Random non-uniform time warp — same shape, different pacing; this
    is what DTW exists for."""
    n = len(path)
    ts = sorted(rng.random() for _ in range(n))
    out = []
    for t in ts:
        pos = t * (n - 1)
        i = min(int(pos), n - 2)
        f = pos - i
        a, b = path[i], path[i + 1]
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return out


def morph(p, q, rng):
    """Blend two paths toward each other — boundary pressure for the
    argmin (a tie-prone query both engines must still agree on)."""
    a = refmatch.resample_nd([list(x) for x in p], refmatch.RESAMPLE_N)
    b = refmatch.resample_nd([list(x) for x in q], refmatch.RESAMPLE_N)
    t = rng.uniform(0.25, 0.75)
    return [((1 - t) * a[i][0] + t * b[i][0],
             (1 - t) * a[i][1] + t * b[i][1]) for i in range(len(a))]


def walk(rng, n=None):
    """Random meander — the must-reject case."""
    n = n or rng.randint(15, 35)
    x, y = rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)
    pts = []
    for _ in range(n):
        x = min(max(x + rng.uniform(-0.12, 0.12), 0.05), 0.95)
        y = min(max(y + rng.uniform(-0.12, 0.12), 0.05), 0.95)
        pts.append((x, y))
    return pts


# ------------------------------------------------------------------------

def pct(vals, q):
    if not vals:
        return 0.0
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * len(v)))]


class Parity(unittest.TestCase):
    """One bank per case built from the SAME raw exemplars in both
    engines — divergent teaching is a finding, so adds are compared."""

    @classmethod
    def setUpClass(cls):
        handle, detail = bank_adapter.try_load(reject_threshold=REJECT_T)
        if handle is None:
            raise unittest.SkipTest(
                f"server/signbank.py not importable ({detail}) — parity "
                f"suite parked until the bank lands. Reference port itself is "
                f"exercised by tools/e2e-live/refmatch.py self-test.")
        cls.impl = handle
        cls.mod = handle.mod
        print(f"\nparity vs signbank via {handle.ctor}; "
              f"match={getattr(handle._match,'__name__',None) or getattr(handle._best,'__name__',None)}")

    def setUp(self):
        self.rng = random.Random(SEED * 1000 + STATS["cases"])

    def _teach_both(self, exemplars):
        """exemplars: {gloss: [raw_pts]}. Returns (ref_bank, notes).

        The impl bank is reset per case: ref is rebuilt per case, so the
        impl must be too — a persistent bank (correct product semantics)
        would hold same-named exemplars from earlier cases and the two
        engines would honestly argmin different template SETS, not
        different decisions on the same set."""
        rst = getattr(self.impl, "reset", None)
        if callable(rst):
            rst()
        ref = refmatch.RefBank(reject_threshold=REJECT_T)
        notes = []
        for gloss, raws in exemplars.items():
            for r in raws:
                ok_ref = ref.add(gloss, r, user=True)
                try:
                    ok_impl = self.impl.add(gloss, r)
                except Exception as e:
                    ok_impl = False
                    notes.append(f"impl.add({gloss}) raised {type(e).__name__}: {e}")
                if ok_ref != bool(ok_impl):
                    STATS["teach_mismatch"] += 1
                    notes.append(f"teach divergence on {gloss}: "
                                 f"ref={ok_ref} impl={ok_impl}")
        return ref, notes

    def _decide_impl(self, pts):
        try:
            return self.impl.decide(pts)
        except Exception as e:
            return refmatch.Decision(None, float("inf"), float("inf"),
                                     False, 0.0,
                                     refused=f"impl raised {type(e).__name__}: {e}")

    # -- the core randomized parity case --------------------------------
    def test_argmin_and_verdict_parity(self):
        for case in range(CASES):
            rng = self.rng
            n_gloss = rng.randint(3, 7)
            exemplars = {}
            for g in range(n_gloss):
                base = rand_path(rng)
                exemplars[f"G{g}"] = [base] + [
                    warp(jitter(base, rng, 0.010), rng)
                    for _ in range(rng.randint(0, 3))]
            ref, notes = self._teach_both(exemplars)
            for n in notes:
                self._record(case, "teach", n)

            roll = rng.random()
            glosses = list(exemplars)
            if roll < 0.55:
                true_g = rng.choice(glosses)
                q = warp(jitter(rng.choice(exemplars[true_g]), rng, 0.015), rng)
                tag = f"known:{true_g}"
            elif roll < 0.75 and len(glosses) >= 2:
                a, b = rng.sample(glosses, 2)
                q = morph(rng.choice(exemplars[a]), rng.choice(exemplars[b]), rng)
                tag = f"morph:{a}~{b}"
            else:
                q = walk(rng)
                tag = "unknown"

            rd = ref.match([list(p) for p in q])
            id_ = self._decide_impl(q)
            STATS["cases"] += 1
            dref = rd.dist if math.isfinite(rd.dist) else None
            dimp = id_.dist if math.isfinite(id_.dist) else None
            if dref is not None and dimp is not None:
                STATS["deltas"].append(abs(dref - dimp))
            if rd.gloss != id_.gloss:
                STATS["argmin_mismatch"] += 1
                STATS["worst"].append(
                    f"case {case} [{tag}]: argmin ref={rd.gloss}@{rd.dist:.4f} "
                    f"impl={id_.gloss}@{id_.dist:.4f}")
            if rd.accepted != id_.accepted:
                STATS["verdict_mismatch"] += 1
                STATS["worst"].append(
                    f"case {case} [{tag}]: verdict ref={rd.accepted}@{rd.dist:.4f} "
                    f"impl={id_.accepted}@{id_.dist:.4f} (T={REJECT_T})")
            self.assertEqual(rd.gloss, id_.gloss,
                             f"argmin mismatch case {case} [{tag}]: "
                             f"ref={rd} impl={id_}")
            self.assertEqual(rd.accepted, id_.accepted,
                             f"verdict mismatch case {case} [{tag}]: "
                             f"ref={rd} impl={id_}")

    # -- refusal contract ------------------------------------------------
    def test_short_input_refused_identically(self):
        ref, _ = self._teach_both({"A": [rand_path(self.rng)],
                                   "B": [rand_path(self.rng)]})
        for n in (0, 1, 4, 7):
            q = rand_path(self.rng, n=max(2, n))[:n]
            rd, id_ = ref.match([list(p) for p in q]), self._decide_impl(q)
            self.assertEqual(rd.gloss is None, id_.gloss is None,
                             f"n={n}: ref refused={rd.refused} impl={id_}")
            self.assertFalse(id_.accepted,
                             f"n={n}: impl must never accept a {n}-pt query")

    # -- stored-bank format parity (if the impl loads Rust bank JSON) ----
    def test_bank_json_roundtrip(self):
        mod = self.mod
        loader = next((getattr(mod, n) for n in
                       ("load", "load_bank", "from_json", "load_json")
                       if callable(getattr(mod, n, None))), None)
        meth = next((getattr(self.impl.bank, n) for n in
                     ("load", "from_json") if callable(getattr(self.impl.bank, n, None))), None) \
            if getattr(self.impl, "bank", None) is not None else None
        if loader is None and meth is None:
            self.skipTest("signbank exposes no bank-JSON loader to roundtrip")
        import tempfile
        exemplars = {"P": [rand_path(self.rng), warp(rand_path(self.rng), self.rng)],
                     "Q": [rand_path(self.rng)]}
        ref, _ = self._teach_both(exemplars)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "signbank.json")
            ref.save(p)
            bank2 = None
            if loader is not None:
                try:
                    bank2 = bank_adapter._call_variants(loader, [(p,),
                                                                 (open(p).read(),)])
                except Exception as e:
                    self._record(0, "load", f"loader raised {e}")
            if bank2 is None and meth is not None:
                try:
                    bank_adapter._call_variants(meth, [(p,), (open(p).read(),)])
                    bank2 = self.impl.bank
                except Exception as e:
                    self._record(0, "load", f"load method raised {e}")
            if bank2 is None:
                self.skipTest("Rust bank JSON not loadable by impl (adapter "
                              "tried path+text args) — see report notes")
            h2 = bank_adapter.BankHandle.__new__(bank_adapter.BankHandle)
            h2.bank, h2.mod, h2.reject_threshold = bank2, mod, REJECT_T
            h2._match = next((getattr(bank2, n) for n in bank_adapter._MATCH_NAMES
                              if callable(getattr(bank2, n, None))), None)
            h2._best = next((getattr(bank2, n) for n in bank_adapter._BEST_NAMES
                             if callable(getattr(bank2, n, None))), None)
            if h2._match is None and h2._best is None:
                self.fail("loaded bank has no match/recognize/best method")
            for _ in range(10):
                q = warp(jitter(self.rng.choice(exemplars["P"]), self.rng), self.rng)
                rd = ref.match([list(x) for x in q])
                id_ = bank_adapter._normalize_result(
                    bank_adapter._call_variants(h2._match or h2._best,
                                                [([list(x) for x in q],),
                                                 ([(x[0], x[1]) for x in q],)]),
                    REJECT_T)
                self.assertEqual(rd.gloss, id_.gloss,
                                 f"roundtrip argmin: ref={rd} impl={id_}")

    def _record(self, case, kind, note):
        STATS["worst"].append(f"case {case} [{kind}]: {note}")


def tearDownModule():
    deltas = STATS["deltas"]
    verdict = ("DECISION-PARITY"
               if not STATS["argmin_mismatch"] and not STATS["verdict_mismatch"]
               else "DIVERGED")
    print("\n" + "=" * 64)
    print(f"PARITY SUMMARY  verdict: {verdict}")
    print(f"  cases run ......... {STATS['cases']}")
    print(f"  argmin mismatches . {STATS['argmin_mismatch']}")
    print(f"  verdict mismatches  {STATS['verdict_mismatch']}")
    print(f"  teach divergences . {STATS['teach_mismatch']}")
    if deltas:
        print(f"  |d_impl - d_ref| .. p50 {pct(deltas,0.5):.5f}  "
              f"p95 {pct(deltas,0.95):.5f}  max {max(deltas):.5f}  "
              f"(tol {DIST_TOL} is informational)")
    for w in STATS["worst"][:20]:
        print("   !", w)
    print("=" * 64)
    out = os.environ.get("PARITY_JSON")
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w") as f:
            json.dump({"verdict": verdict,
                       "cases": STATS["cases"],
                       "argmin_mismatches": STATS["argmin_mismatch"],
                       "verdict_mismatches": STATS["verdict_mismatch"],
                       "teach_divergences": STATS["teach_mismatch"],
                       "dist_delta_p50": pct(deltas, 0.5),
                       "dist_delta_p95": pct(deltas, 0.95),
                       "dist_delta_max": max(deltas) if deltas else None,
                       "notes": STATS["worst"][:50]}, f, indent=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
