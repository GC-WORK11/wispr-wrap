"""packs.py — prefilled Teach packs.

One tap installs a ready-made sign: hello and intro bind the text they
type at the laptop cursor; namaste is the reserved palms-together pause
gesture the daemon already owns — shown for information, never bindable
(teach refuses reserved glosses).

Takes are honest: install loads recorded kp42 frame lists from
server/packs_data/<id>/*.json when they exist ("real-takes"), else the
pack reports needs_takes. Nothing here fabricates a performance."""

import json
import os

PACKS = [
    {"id": "hello", "gloss": "HELLO", "title": "Hello", "tier": 1,
     "text": "hello"},
    {"id": "namaste", "gloss": "NAMASTE", "title": "Namaste",
     "builtin": True},
    {"id": "intro", "gloss": "INTRO", "title": "Introduce yourself",
     "tier": 1, "text": "Hello, my name is"},
]

DATA_DIR = os.path.join(os.path.dirname(__file__), "packs_data")


def _action(pack):
    text = pack["text"]
    return {"kind": "builtin", "tool": "type.text", "type": "type_text",
            "label": "type " + text, "tier": pack["tier"],
            "args": {"text": text},
            "argv": ["xdotool", "type", "--", text]}


def _gloss_row(gloss, rows):
    for r in rows or []:
        if str(r.get("gloss", "")).upper() == gloss:
            n = int(r.get("n_exemplars") or r.get("exemplar_count") or 0)
            return n, r.get("action")
    return 0, None


def list_packs(rows):
    out = []
    for p in PACKS:
        e = {"id": p["id"], "gloss": p["gloss"], "title": p["title"],
             "takes": 0, "action": None}
        if p.get("builtin"):
            e["state"] = "built-in"
        else:
            n, act = _gloss_row(p["gloss"], rows)
            e["takes"] = n
            e["action"] = act
            e["state"] = "ready" if (n >= 3 and act) else "needs_takes"
        out.append(e)
    return out


def _load_takes(pid):
    """kp42 take files recorded for this pack, or []. Each file is a
    frame list [{t_ms, points:[..]}...] or {frames:[...]}."""
    d = os.path.join(DATA_DIR, pid)
    takes = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                j = json.load(open(os.path.join(d, fn)))
                frames = j.get("frames") if isinstance(j, dict) else j
                if isinstance(frames, list) and frames:
                    takes.append(frames)
            except Exception:
                pass
    return takes


def install_pack(pid, rows, bind, add_take):
    """rows: bank_rows() view; bind(data)->(code,obj) is bank_put;
    add_take(gloss, frames, label)->bool is BANK.add_exemplar."""
    p = next((x for x in PACKS if x["id"] == pid), None)
    if p is None:
        return 400, {"ok": False, "error": "unknown pack"}
    if p.get("builtin"):
        return 400, {"ok": False,
                     "error": p["id"] + " is built in already"}
    n, act = _gloss_row(p["gloss"], rows)
    if act and n:
        # never clobber a sign the user already made theirs
        return 200, {"ok": True, "id": pid, "gloss": p["gloss"],
                     "takes": n, "source": "existing",
                     "state": "ready" if n >= 3 else "needs_takes"}
    takes, source = 0, "none"
    for frames in _load_takes(pid):
        try:
            if add_take(p["gloss"], frames, p["title"]):
                takes += 1
        except Exception:
            pass
    if takes:
        source = "real-takes"
    # argv form, not a dict action: it lands in commands.tsv even with
    # zero takes, and teach_begin re-binds it the moment a take exists.
    code, obj = bind({"gloss": p["gloss"], "label": p["title"],
                      "tier": p["tier"], "argv": _action(p)["argv"]})
    if not (200 <= code < 300):
        return code, obj
    n2, _ = _gloss_row(p["gloss"], rows)  # bind() may itself add takes
    total = max(n + takes, n2)
    return 200, {"ok": True, "id": pid, "gloss": p["gloss"],
                 "takes": total, "source": source,
                 "state": "ready" if total >= 3 else "needs_takes"}
