#!/usr/bin/env python3
"""seed.py — pre-seed the scratch demo HOME's bank + commands.tsv.

The demo still TEACHES one sign on camera (the teach chapter builds
TAKE-A-SHOT through the real wizard); these rows exist so the earlier
chapters have bound signs to perform, exactly like a returning user's
laptop would have.

Gloss <- e2e grammar path:
  OPEN-TERM  <- SWIPE-R   (commands.tsv argv: xfce4-terminal -x fired.sh)
  NEXT       <- SWIPE-L   (context dispatch: chrome=ctrl+Tab, term=text)
  STANDUP    <- V-CHECK   (macro: open terminal, wait, type)
  VOL-UP     <- PUSH-UP   (sys.volume-up — refuses honestly: no pactl)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, HERE)

import signbank                       # noqa: E402
import clips                          # noqa: E402

HOME = os.environ.get("WF_DEMO_HOME", "/tmp/demo/home")
DEMO = os.environ.get("WF_DEMO_DIR", "/tmp/demo")
CFG = os.path.join(HOME, ".config", "wf")
os.makedirs(CFG, exist_ok=True)

B = signbank.SignBank()

SEEDED = {
    "OPEN-TERM": ("SWIPE-R", "open a terminal",
                  None),                       # tsv row carries the argv
    "NEXT": ("SWIPE-L", "next (context-aware)",
             {"kind": "context", "label": "context: tabs vs type",
              "per_app": {
                  "google-chrome": {"tool": "key.press",
                                    "args": {"keys": "ctrl+Tab"}},
                  "xfce4-terminal": {"tool": "type.text",
                                     "args": {"text":
                                              "  ⟦NEXT — context dispatch⟧"}},
              }}),
    "STANDUP": ("V-CHECK", "open terminal + type standup",
                {"kind": "macro", "label": "macro: terminal + type",
                 "steps": [
                     {"tool": "app.open",
                      "args": {"id": "xfce4-terminal"}},
                     {"wait_ms": 1600},
                     {"type": "macro sign typed this — STANDUP"},
                 ]}),
    "VOL-UP": ("PUSH-UP", "volume up",
               {"tool": "sys.volume-up", "kind": "sys",
                "label": "volume up"}),
}

for gloss, (sign_name, label, action) in SEEDED.items():
    for v in (1, 2, 3):
        frames = clips.sign(sign_name, seed=v)
        ok = B.add_exemplar(gloss, frames, label=label)
        assert ok, "exemplar refused for %s" % gloss
    if action is not None:
        B.set_action(gloss, label=label, action=action)
    print("seeded %-9s <- %-8s x3%s" % (gloss, sign_name,
                                        " +action" if action else ""))

bank_path = os.path.join(CFG, "signbank.json")
B.save(bank_path)
print("bank ->", bank_path)

tsv = os.path.join(CFG, "commands.tsv")
with open(tsv, "w") as f:
    f.write("# wf commands — GLOSS<TAB>label<TAB>argv[<TAB>tier]\n")
    f.write("OPEN-TERM\topen a terminal\t"
            "xfce4-terminal --title wf-fired -x %s\n"
            % os.path.join(DEMO, "fired.sh"))
os.chmod(tsv, 0o600)
print("commands ->", tsv)
print(json.dumps({"signs": [r["gloss"] for r in B.list()]}))
