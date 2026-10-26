"""teach_train.py — the training step that runs when Teach finishes.

Every captured take is matched, held out, against a fresh bank built
from the OTHER takes — the same prep + match calls a live performance
goes through. A sign is "trained" when every take is recognised under
the live accept threshold; "recheck" means at least one take reads like
a different sign and is worth recording again. No numbers are invented:
the states below are exactly what the matcher returned."""

import math

import signbank


def verify(gloss, takes_frames, bank_cls=None):
    """takes_frames: list of captured take segments (raw frame lists).
    Returns {"state","takes","checked","ok","worst_dist"} — worst_dist is
    the largest held-out distance seen, null when nothing scored."""
    segs = [f for f in (takes_frames or []) if f]
    takes = len(segs)
    out = {"state": "needs_more", "takes": takes,
           "checked": 0, "ok": 0, "worst_dist": None}
    if takes < 2:
        return out
    cls = bank_cls or signbank.SignBank
    worst = None
    for i, fr in enumerate(segs):
        tmp = cls()
        for j, other in enumerate(segs):
            if j != i:
                tmp.add_exemplar(gloss, other)
        if not tmp.templates:
            continue
        out["checked"] += 1
        try:
            r = tmp.match(fr)
        except Exception:
            continue
        d = getattr(r, "dist", None)
        d = float(d) if isinstance(d, (int, float)) and math.isfinite(d) \
            else None
        if getattr(r, "gloss", None) == gloss and \
                getattr(r, "accepted", False):
            out["ok"] += 1
        if d is not None and (worst is None or d > worst):
            worst = d
    out["worst_dist"] = worst
    if takes >= 3 and out["checked"] and out["ok"] == out["checked"]:
        out["state"] = "trained"
    elif takes >= 3:
        out["state"] = "recheck"
    return out
