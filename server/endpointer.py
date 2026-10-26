#!/usr/bin/env python3
"""endpointer — motion-based segmenter for kp42 hand-keypoint streams.

Pure stdlib. Built for the phone-camera path in the merged product
(MERGED_PRODUCT_PRD §3): the composer runs MediaPipe Hands in-browser and
POSTs ~15-30fps keypoint frames; this decides WHEN a sign ends without
needing the signer to hold a deliberate pause.

Wire format (same as signbank.py):
    frame = {"t_ms": int, "points": [{"x":..,"y":..,"score":..} x42]}
    points[0:21] = left hand, points[21:42] = right (MediaPipe order).
    A 21-point frame is one hand given by frame["hand"] ("left"|"right",
    default "right"). An absent hand is all score-0 (or zero-padded).

feed(frame) -> list of events:
    {"type": "segment", "frames": [...], "dur_s": float}
        emitted on the quiet end-of-sign window (motion was active, then
        stayed below exit velocity for `quiet_s`), or when a segment hits
        `max_seg_s` and is force-closed. Too-short motion is dropped with
        {"type": "segment-drop"} for debuggability — never a segment.
    {"type": "mode", "name": <palm_sign|namaste_sign>}
        MODE EVENTS, not signs: a held open palm and held palms-together
        (namaste). The daemon maps them to listen start/stop. Names are
        configurable; defaults PALM-OPEN / NAMASTE. Fires once per hold
        and re-arms only after the pose releases.

Motion model: per-hand wrist velocity + palm-centroid velocity + hand
spread (openness) rate, EMA-smoothed, with enter/exit hysteresis.

Units: velocities are measured in PALM-WIDTHS per second (each hand's
own wrist->middle-MCP length is the ruler). That makes every threshold
invariant to camera distance AND to whether the wire carries pixel or
normalized-0..1 coordinates — signbank wants px upstream of it, but
this segmenter doesn't care. Openness/spread is likewise a unitless
ratio (mean fingertip reach / palm width). A hand whose palm size is
unmeasurable falls back to raw-units speed so nothing silently dies.
"""
import math
from collections import deque

WRIST = 0
TIPS = (4, 8, 12, 16, 20)          # thumb,index,middle,ring,pinky tips
MID_MCP = 9                       # wrist->middle-MCP = palm size ref
ANCHORS = (0, 5, 9, 13, 17)       # wrist + MCPs -> stable palm centroid
PRESENCE_SCORE = 0.25             # min per-point score to count
PRESENCE_KEYS = 6                 # min confident points for "hand present"


def _d(ax, ay, bx, by):
    return math.hypot(ax - bx, ay - by)


def _pt(p):
    """Tolerant point parse: {x,y,score} dict or [x,y,score] sequence."""
    if isinstance(p, dict):
        s = p.get("score", 1.0)
        return (float(p.get("x", 0.0)), float(p.get("y", 0.0)),
                1.0 if s is None else float(s))
    return (float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 1.0)


class _Hand:
    """One frame's worth of one hand: presence, wrist, palm centroid,
    palm size, openness (mean tip reach / palm size), this-frame speed."""
    __slots__ = ("present", "wx", "wy", "cx", "cy", "palm", "openness",
                 "speed")

    def __init__(self, pts):
        self.present = False
        self.wx = self.wy = self.cx = self.cy = 0.0
        self.palm = 1e-6
        self.openness = 0.0
        self.speed = 0.0
        if not pts or len(pts) < 21:
            return
        conf = sum(1 for p in pts[:21] if p[2] >= PRESENCE_SCORE)
        if conf < PRESENCE_KEYS:
            return
        # a zero-padded / frozen hand (every landmark identical) is absent
        if _d(pts[0][0], pts[0][1], pts[9][0], pts[9][1]) < 1e-4 and \
           _d(pts[0][0], pts[0][1], pts[17][0], pts[17][1]) < 1e-4:
            return
        self.present = True
        self.wx, self.wy = pts[WRIST][0], pts[WRIST][1]
        self.cx = sum(pts[i][0] for i in ANCHORS) / len(ANCHORS)
        self.cy = sum(pts[i][1] for i in ANCHORS) / len(ANCHORS)
        self.palm = max(_d(pts[0][0], pts[0][1], pts[9][0], pts[9][1]),
                        1e-6)
        self.openness = sum(
            _d(pts[t][0], pts[t][1], self.wx, self.wy)
            for t in TIPS) / (len(TIPS) * self.palm)


class Endpointer:
    """feed() one frame at a time; collect segment + mode events.

    Knobs (defaults tuned for 15-30fps kp42, speeds in palm-widths/s):
      enter_v/exit_v   EMA energy hysteresis — start above, end below
      quiet_s          quiet end-of-sign window (seconds of stillness)
      min_seg_s        shortest real sign — shorter motion is dropped
      max_seg_s        longest sign — force-close (a sign is seconds, not minutes)
      pre_roll         frames kept before activation so the segment
                       includes the lead-in
      min_frames       floor on segment length in frames
      hold_s           palm/namaste hold time before the mode event fires
      hold_v           max hand speed that still counts as "held"
      open_ratio       openness (tips/palm) that counts as a flat palm
      rearm_ratio      openness must fall below this to re-arm palm
      namaste_gap      palms-together distance, in avg-palm-size units
      namaste_open     minimum openness for each hand in namaste
      palm_sign / namaste_sign   emitted mode-event names (None disables)
    """

    def __init__(self, enter_v=1.5, exit_v=0.7, ema=0.45,
                 quiet_s=0.35, min_seg_s=0.20, max_seg_s=3.0,
                 pre_roll=4, min_frames=4,
                 hold_s=0.45, hold_v=0.6,
                 open_ratio=1.75, rearm_ratio=1.40,
                 namaste_gap=1.4, namaste_open=1.35,
                 palm_sign="PALM-OPEN", namaste_sign="NAMASTE"):
        self.enter_v = float(enter_v)
        self.exit_v = float(exit_v)
        self.ema = float(ema)
        self.quiet_s = float(quiet_s)
        self.min_seg_s = float(min_seg_s)
        self.max_seg_s = float(max_seg_s)
        self.min_frames = int(min_frames)
        self.hold_s = float(hold_s)
        self.hold_v = float(hold_v)
        self.open_ratio = float(open_ratio)
        self.rearm_ratio = float(rearm_ratio)
        self.namaste_gap = float(namaste_gap)
        self.namaste_open = float(namaste_open)
        self.palm_sign = palm_sign or None
        self.namaste_sign = namaste_sign or None
        self._pre = deque(maxlen=max(1, int(pre_roll)))
        self.reset()

    def reset(self):
        self._state = "idle"          # idle | active
        self._seg = []
        self._seg_t0 = 0.0
        self._quiet = None
        self._absent = 0              # consecutive no-hand frames
        self._pre.clear()
        self._prev = [None, None]     # last _Hand per slot
        self._t = None                # last frame time (s)
        self._ema = None
        self._palm = [{"since": None, "armed": False, "seen": False}
                      for _ in range(2)]
        self._nam = {"since": None, "armed": True}
        self._n = 0                   # frames seen before the current one

    @property
    def active(self):
        return self._state == "active"

    @property
    def current_len(self):
        """Frames in the in-flight segment (0 when idle)."""
        return len(self._seg)

    def _hands(self, frame):
        raw = frame.get("points") or []
        pts = [_pt(p) for p in raw]
        if len(pts) == 21:            # single-hand stream — slot by hand
            idx = 0 if str(frame.get("hand", "right")).lower() == "left" \
                else 1
            hands = [None, None]
            hands[idx] = pts
        else:
            hands = [pts[0:21] or None, pts[21:42] or None]
        return [_Hand(hands[0]), _Hand(hands[1])]

    def feed(self, frame):
        events = []
        t = float(frame.get("t_ms") or 0) / 1000.0
        dt = t - self._t if (self._t is not None and t > self._t) else 0.0
        hands = self._hands(frame)

        energy = 0.0
        for i, h in enumerate(hands):
            p = self._prev[i]
            if h.present and p is not None and p.present and dt > 0:
                # speeds in palm-widths/s — unit-free across px/normalized
                ruler = max(h.palm, p.palm, 1e-6)
                vw = _d(h.wx, h.wy, p.wx, p.wy) / (dt * ruler)
                vc = _d(h.cx, h.cy, p.cx, p.cy) / (dt * ruler)
                vs = abs(h.openness - p.openness) / dt     # spread rate
                h.speed = vw
                energy += vw + 0.4 * vc + 0.5 * vs
        self._prev = hands
        self._t = t
        self._ema = energy if self._ema is None else \
            self.ema * energy + (1.0 - self.ema) * self._ema

        events.extend(self._mode_events(hands, t))

        self._pre.append(frame)
        # a hand gone from view IS the end of the sign — no EMA decay to
        # wait out.  One absent frame can be an occlusion flicker, two in
        # a row cannot be mid-sign: force-close.  (Real streams and the
        # e2e clips both end signs by dropping the hand — without this a
        # held-open segment blends into the NEXT sign's lead-in and the
        # match sees a glued double-length arc.)
        if any(h.present for h in hands):
            self._absent = 0
        else:
            self._absent += 1
        if self._state == "idle":
            # a segment starts only on a frame that actually carries a
            # hand — a decaying EMA tail on an absent/still frame is the
            # residue of the LAST sign, not the start of a new one.
            if self._ema >= self.enter_v and any(h.present for h in hands):
                self._state = "active"
                self._seg = list(self._pre)   # pre-roll includes this frame
                self._seg_t0 = float(self._pre[0].get("t_ms") or 0) / 1000.0
                self._quiet = None
        else:
            self._seg.append(frame)
            if t - self._seg_t0 >= self.max_seg_s:
                events.append(self._close(t, "maxlen"))
            elif self._absent >= 2:
                events.append(self._close(t, "absent"))
                self._absent = 0
            elif self._ema < self.exit_v:
                if self._quiet is None:
                    self._quiet = t
                elif t - self._quiet >= self.quiet_s:
                    events.append(self._close(t, "quiet"))
            else:
                self._quiet = None
        self._n += 1
        return events

    def _close(self, t, how):
        seg, self._seg = self._seg, []
        self._state = "idle"
        self._quiet = None
        self._absent = 0
        # The EMA tail that ended this segment belongs to it — reset the
        # smoother so a just-closed sign can never re-trigger a phantom
        # segment on the very next frame (idle re-activation must be
        # driven by NEW energy, exactly like the stream's first segment;
        # a genuine continuation after a maxlen cut re-arms instantly
        # because its energy is real).
        self._ema = None
        dur = t - self._seg_t0
        if len(seg) >= self.min_frames and dur >= self.min_seg_s:
            return {"type": "segment", "frames": seg, "dur_s": dur,
                    "t_ms": int(t * 1000), "how": how}
        return {"type": "segment-drop", "reason": "short",
                "n": len(seg), "dur_s": dur, "t_ms": int(t * 1000)}

    def _mode_events(self, hands, t):
        ev = []
        a, b = hands
        together = False
        if a.present and b.present:
            gap = _d(a.cx, a.cy, b.cx, b.cy)
            together = gap <= self.namaste_gap * (a.palm + b.palm) / 2.0

        # held open palm — but hands-together means namaste, never palm.
        # A palm that is ALREADY resting in view when the stream starts is
        # not a deliberate presentation: arm on hand entry (absent->present
        # after the first frame) or after the hand closes past rearm_ratio.
        # Without this, a static rest pose fires PALM-OPEN by accident
        # (measured: 155/320 held-out benchmark signer clips, 125 of them
        # in the leading rest pad at ~0.47 s).
        for i, h in enumerate(hands):
            st = self._palm[i]
            if h.present and not st["seen"]:
                st["seen"] = True
                if self._n > 0:       # entered view after the stream began
                    st["armed"] = True
            elif not h.present and st["seen"]:
                st["seen"] = False
                st["armed"] = True    # leaving view re-arms the next entry
            held_open = h.present and not together and \
                self._state != "active" and \
                h.openness >= self.open_ratio and h.speed <= self.hold_v
            if held_open:
                if st["since"] is None:
                    st["since"] = t
                if st["armed"] and t - st["since"] >= self.hold_s:
                    st["armed"] = False
                    if self.palm_sign:
                        ev.append({"type": "mode", "name": self.palm_sign,
                                   "t_ms": int(t * 1000)})
            else:
                st["since"] = None
                if (not h.present) or h.openness <= self.rearm_ratio:
                    st["armed"] = True

        # held palms-together (namaste)
        st = self._nam
        if together and a.openness >= self.namaste_open and \
                b.openness >= self.namaste_open and \
                a.speed <= self.hold_v and b.speed <= self.hold_v:
            if st["since"] is None:
                st["since"] = t
            if st["armed"] and t - st["since"] >= self.hold_s:
                st["armed"] = False
                if self.namaste_sign:
                    ev.append({"type": "mode", "name": self.namaste_sign,
                               "t_ms": int(t * 1000)})
        else:
            st["since"] = None
            st["armed"] = True
        return ev


if __name__ == "__main__":
    # tiny self-check: still -> moving -> still emits exactly one segment
    def hand(cx, cy, tip=0.10):
        p = [[cx, cy, 0.9] for _ in range(21)]
        p[MID_MCP] = [cx, cy + 0.08, 0.9]
        for i, t_ in enumerate(TIPS):
            p[t_] = [cx + (i - 2) * 0.02, cy - tip, 0.9]
        return p
    ep = Endpointer()
    out = []
    t = 0
    for k in range(10):                       # still
        t += 33
        out += ep.feed({"t_ms": t, "points": hand(0.5, 0.5) + hand(0.9, 0.9, 0)})
    for k in range(15):                       # moving
        t += 33
        out += ep.feed({"t_ms": t, "points": hand(0.5 + k * 0.03, 0.5) + hand(0.9, 0.9, 0)})
    for k in range(25):                       # still -> quiet eos
        t += 33
        out += ep.feed({"t_ms": t, "points": hand(0.92, 0.5) + hand(0.9, 0.9, 0)})
    segs = [e for e in out if e["type"] == "segment"]
    assert len(segs) == 1 and len(segs[0]["frames"]) >= 15, out
    print("endpointer self-check ok: 1 segment, %d frames" % len(segs[0]["frames"]))
