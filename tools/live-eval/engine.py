"""engine.py — which bank the harness drives, resolved at runtime.

Two stacks today:
  * `real`     — the merged product's actual modules, when present:
                 server/endpointer.py (kp42 segmenter, normalized 0..1
                 coords) + server/signbank.py (fused traj+shape bank —
                 match(frames) -> MatchResult with its own accept rule:
                 dist <= reject_threshold AND traj-anchor veto).
  * `contract` — tools/live-eval/contract.py: this repo's stdlib port of
                 matcher.rs (traj-only Pixel bank) + alive-temporal
                 Endpointer. The parity reference — same role dtw_full
                 plays for dtw_w in Rust.

The harness asks each impl for the same four things:
    eng.name                      # "signbank" | "contract"
    eng.reject_threshold
    eng.teach(gloss, segment)     # segment = wire frames (real) or
                                  # [(x,y)...] centroid path (contract)
    eng.match(segment)            # -> (gloss|None, dist, accepted, reason)

A wire frame is the kp42 payload the composer POSTs:
    {"t_ms": int, "points": [[x, y, score], ...] x42}
    normalized 0..1, points[0:21] left hand, [21:42] right,
    absent hand = score 0.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import contract  # noqa: E402

_REPO_SERVER = os.path.join(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "server")


def server_available():
    return (os.path.isfile(os.path.join(_REPO_SERVER, "signbank.py"))
            and os.path.isfile(os.path.join(_REPO_SERVER, "endpointer.py")))


def _import_server(name):
    if _REPO_SERVER not in sys.path:
        sys.path.insert(0, _REPO_SERVER)
    try:
        return __import__(name)
    except Exception:
        return None


class _SignbankImpl:
    """The real stack: server.endpointer + server.signbank."""

    def __init__(self, reject_threshold=None):
        self.sb = _import_server("signbank")
        self.ep_mod = _import_server("endpointer")
        kw = ({} if reject_threshold is None
              else {"reject_threshold": reject_threshold})
        self.bank = self.sb.SignBank(**kw) if self.sb else None

    @property
    def reject_threshold(self):
        return self.bank.reject_threshold

    def new_segmenter(self):
        return self.ep_mod.Endpointer() if self.ep_mod else None

    def teach(self, gloss, segment):
        return bool(self.bank.add_exemplar(gloss, segment))

    def match(self, segment):
        r = self.bank.match(segment)
        return r.gloss if r.accepted or r.gloss else None, \
            r.dist, bool(r.accepted), r.reason

    @property
    def raw(self):
        return self.bank


class _ContractBank:
    """The Rust-faithful reference bank (traj-only Pixel space)."""

    def __init__(self, reject_threshold=0.45):
        self.bank = contract.TemplateBank.new(
            0.45 if reject_threshold is None else reject_threshold)

    @property
    def reject_threshold(self):
        return self.bank.reject_threshold

    @staticmethod
    def new_segmenter(**kw):
        return contract.Endpointer(
            contract.EndpointConfig(**kw)) if kw else contract.Endpointer()

    def teach(self, gloss, path):
        ok = self.bank.add_sample(gloss, [list(p[:2]) for p in path])
        if ok:
            self.bank.mark_taught(gloss)
        return ok

    def match(self, path):
        r = self.bank.best([list(p[:2]) for p in path])
        if r is None:
            return None, float("inf"), False, "thin-query"
        gloss, d, _s = r
        if not (math_isfinite(d) and d <= self.bank.reject_threshold):
            return gloss, d, False, "threshold"
        if not self.bank.traj_channel_agrees(path, gloss):
            return gloss, d, False, "veto"
        return gloss, d, True, "ok"

    @property
    def raw(self):
        return self.bank


def math_isfinite(x):
    return x == x and x not in (float("inf"), float("-inf"))


class Engine:
    """One bank instance. `which` = "auto"|"signbank"|"contract"; auto
    prefers the real modules and reports which won in proofs."""

    def __init__(self, which="auto", reject_threshold=None):
        self.impl = None
        self.name = None
        self.kind = None
        if which in ("auto", "signbank") and server_available():
            impl = _SignbankImpl(reject_threshold)
            if impl.bank is not None:
                self.impl, self.name, self.kind = impl, "signbank", "real"
        if self.impl is None:
            if which == "signbank":
                raise RuntimeError(
                    "server/signbank.py not importable — refusing to "
                    "silently substitute the contract")
            self.impl = _ContractBank(reject_threshold)
            self.name, self.kind = "contract", "contract"

    # uniform surface ------------------------------------------------
    def teach(self, gloss, segment):
        return self.impl.teach(gloss, segment)

    def match(self, segment):
        """(gloss|None, dist, accepted, reason)."""
        return self.impl.match(segment)

    @property
    def reject_threshold(self):
        return self.impl.reject_threshold

    @property
    def raw(self):
        return self.impl.raw

    def new_segmenter(self):
        return self.impl.new_segmenter()
