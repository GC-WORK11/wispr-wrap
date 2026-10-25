#!/usr/bin/env python3
"""bank_adapter — tolerantly bind whatever server/signbank.py actually
shipped to the contract the e2e lane tests against:

    bank = SignBank(...)            (reject_threshold / path config)
    bank.add(gloss, points2d)       (teach an exemplar, >=8 raw points)
    bank.match(points2d) -> MatchResult(gloss, dist, accepted, ...)

The mission names that contract; until W1 lands, this module resolves it
by duck-typing across the plausible spellings so the parity suite and
the replay harness keep working without edits. If signbank.py is
absent, load_signbank() returns None -> callers SKIP cleanly. If it
exists but exposes none of the expected API, AdapterError names every
spelling tried — that's an integration FINDING, not a skip.
"""
import importlib
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, os.path.join(_ROOT, "server")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from refmatch import Decision, INF  # noqa: E402  (shared normalized result)


class AdapterError(Exception):
    pass


def load_signbank():
    """-> module | None. Resolution order: server.signbank (package),
    signbank (server/ on path), then a direct file load of
    server/signbank.py for un-packaged checkouts."""
    for name in ("server.signbank", "signbank"):
        try:
            return importlib.import_module(name)
        except ImportError:
            pass
        except Exception as e:  # module exists but fails at import = report
            raise AdapterError(f"server/signbank.py present but import fails: {e}")
    path = os.path.join(_ROOT, "server", "signbank.py")
    if os.path.isfile(path):
        spec = importlib.util.spec_from_file_location("signbank", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            return mod
        except Exception as e:
            raise AdapterError(f"server/signbank.py present but fails to load: {e}")
    return None


_ADD_NAMES = ("add", "add_sample", "add_sample_nd", "teach", "add_exemplar",
              "enroll", "add_template")
_MATCH_NAMES = ("match", "decide", "classify", "match_points", "match_nd")
_BEST_NAMES = ("recognize", "recognize_nd", "best", "best_nd")
_CTOR_NAMES = ("SignBank", "TemplateBank", "Bank")
_LOAD_NAMES = ("load", "load_bank", "open", "from_file")


def _call_variants(fn, args_pool):
    """Try fn(*args) over a few arg spellings; return first that doesn't
    raise TypeError (a TypeError from INSIDE the impl is re-raised via the
    last error only when every spelling fails)."""
    last = None
    for args in args_pool:
        try:
            return fn(*args)
        except TypeError as e:
            last = e
            continue
    raise last or TypeError("no arg spellings accepted")


def _normalize_result(res, reject_threshold):
    """-> Decision. Tolerates dicts, namedtuples/objects, and tuples:
    (gloss, dist, accepted, ...), (gloss, dist, second), (gloss, score)
    from recognize-style APIs, and None (rejected/refused)."""
    if res is None:
        return Decision(None, INF, INF, False, 0.0, refused="no-match")
    if isinstance(res, dict):
        get = lambda *ks: next((res[k] for k in ks if k in res), None)
        gloss = get("gloss", "name", "label", "winner")
        dist = get("dist", "d", "best_d", "distance")
        acc = get("accepted", "ok", "accept", "committed")
        score = get("score", "conf", "confidence")
        second = get("second", "second_d", "runner_up")
        dist = INF if dist is None else float(dist)
        if acc is None:
            acc = gloss is not None and dist <= reject_threshold
        return Decision(gloss, dist, float(second) if second is not None else INF,
                        bool(acc), float(score) if score is not None else 0.0)
    if isinstance(res, tuple) or isinstance(res, list):
        vals = list(res)
        if not vals:
            return Decision(None, INF, INF, False, 0.0, refused="empty-result")
        gloss = vals[0]
        nums = [float(v) for v in vals[1:] if isinstance(v, (int, float))]
        dist = nums[0] if nums else INF
        second = nums[1] if len(nums) > 1 else INF
        return Decision(gloss, dist, second, gloss is not None and dist <= reject_threshold,
                        1.0 / (1.0 + dist))
    # object with attributes (MatchResult dataclass, namedtuple, ...)
    gloss = getattr(res, "gloss", getattr(res, "name", None))
    dist = getattr(res, "dist", getattr(res, "d", getattr(res, "best_d", INF)))
    acc = getattr(res, "accepted", getattr(res, "ok", None))
    score = getattr(res, "score", getattr(res, "conf", None))
    second = getattr(res, "second", getattr(res, "second_d", INF))
    if acc is None:
        acc = gloss is not None and float(dist) <= reject_threshold
    return Decision(gloss, float(dist), float(second), bool(acc),
                    float(score) if score is not None else 0.0)


class BankHandle:
    """Uniform face over however W1 shaped the bank. Construction tries
    the class spellings then module-level loaders."""

    def __init__(self, mod, path=None, reject_threshold=None):
        tried = []
        self.mod = mod
        self.reject_threshold = 0.45 if reject_threshold is None else reject_threshold
        kwargs_pool = []
        if path:
            kwargs_pool += [dict(path=path), dict(bank_path=path), dict(file=path)]
        if reject_threshold is not None:
            kwargs_pool += [dict(reject_threshold=reject_threshold),
                            dict(threshold=reject_threshold)]
        kwargs_pool += [dict()]
        for cname in _CTOR_NAMES:
            cls = getattr(mod, cname, None)
            if cls is None:
                continue
            for kw in kwargs_pool:
                try:
                    self.bank = cls(**kw)
                    self.ctor = f"{cname}({','.join(kw) or ''})"
                    break
                except TypeError as e:
                    tried.append(f"{cname}{kw}: {e}")
                    continue
                except Exception as e:
                    tried.append(f"{cname}{kw}: {type(e).__name__} {e}")
                    continue
            if getattr(self, "bank", None) is not None:
                break
        if getattr(self, "bank", None) is None and path:
            for lname in _LOAD_NAMES:
                fn = getattr(mod, lname, None)
                if fn is None:
                    continue
                try:
                    self.bank = _call_variants(fn, [(path,), ()])
                    self.ctor = f"{lname}({path})"
                    break
                except Exception as e:
                    tried.append(f"{lname}: {e}")
        if getattr(self, "bank", None) is None:
            raise AdapterError(
                "signbank module found but no bank could be constructed; tried: "
                + "; ".join(tried) if tried else "no candidate names present")

        # resolve methods once — missing pieces fail loudly at use
        self._add = next((getattr(self.bank, n) for n in _ADD_NAMES
                          if callable(getattr(self.bank, n, None))), None)
        self._match = next((getattr(self.bank, n) for n in _MATCH_NAMES
                            if callable(getattr(self.bank, n, None))), None)
        self._best = next((getattr(self.bank, n) for n in _BEST_NAMES
                           if callable(getattr(self.bank, n, None))), None)
        if self._match is None and self._best is None:
            raise AdapterError(
                "bank constructed via %s but has no match/recognize/best method; "
                "tried %s | %s" % (self.ctor, _MATCH_NAMES, _BEST_NAMES))

    # -- teach ----------------------------------------------------------
    def add(self, gloss, points):
        """points: [(x,y) | [x,y,...]]. Returns bool."""
        if self._add is None:
            raise AdapterError("bank has no add/teach method; tried " + str(_ADD_NAMES))
        pts_xy = [(float(p[0]), float(p[1])) for p in points]
        pts_ll = [[float(v) for v in p] for p in points]
        r = _call_variants(self._add, [(gloss, pts_xy), (gloss, pts_ll),
                                       (pts_ll, gloss)])
        return bool(r) if r is not None else True

    # -- decide ---------------------------------------------------------
    def decide(self, points):
        """points2d trajectory -> Decision."""
        pts_xy = [(float(p[0]), float(p[1])) for p in points]
        pts_ll = [[float(v) for v in p] for p in points]
        if self._match is not None:
            res = _call_variants(self._match, [(pts_xy,), (pts_ll,)])
        else:
            res = _call_variants(self._best, [(pts_xy,), (pts_ll,)])
        return _normalize_result(res, self.reject_threshold)


def try_load(path=None, reject_threshold=None):
    """-> (BankHandle|None, detail). None only when the module is absent."""
    mod = load_signbank()
    if mod is None:
        return None, "server/signbank.py not present (W1 in-flight)"
    return BankHandle(mod, path=path, reject_threshold=reject_threshold), "ok"
