#!/usr/bin/env python3
"""screen.py — read the laptop's real screen for the Go Live proofs (no browser
instrumentation: the proof looks at pixels like a person would).

  python3 screen.py thumb  <shot.png>                  -> "x y" of the first fully visible
                                                          video thumbnail in YouTube's results
  python3 screen.py diff   <a.png> <b.png> x0 y0 x1 y1 -> mean abs pixel difference in a box
  python3 screen.py redact <shot.png> <out.png>        -> blur the video player and the right
                                                          column of a YouTube watch page
                                                          (pre-roll ads can show real people)

Layout assumptions: the laptop is 1280x800 with Chrome maximized (tools/live-ui/boot.sh)."""
import sys
from PIL import Image, ImageChops, ImageFilter, ImageStat

TOP = 140          # below Chrome's tab strip + URL bar + YouTube's top bar


def rows_busy(img, x0, x1, step=3, light=235):
    """fraction of non-background pixels per row in [x0, x1)"""
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    xs = range(max(0, x0), min(w, x1), step)
    n = max(1, len(xs))
    return [sum(1 for x in xs if px[x, y] < light) / n for y in range(h)]


def runs(flags, start=0):
    out, y0 = [], None
    for y, f in enumerate(flags):
        if y < start:
            continue
        if f and y0 is None:
            y0 = y
        elif not f and y0 is not None:
            out.append((y0, y - 1))
            y0 = None
    if y0 is not None:
        out.append((y0, len(flags) - 1))
    return out


def thumb(path):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    busy = rows_busy(img, 110, 580)
    for a, b in runs([f >= 0.75 for f in busy], TOP):
        if 150 <= b - a <= 430 and a >= TOP + 4 and b <= h - 8:
            y = (a + b) // 2
            g = img.convert("L").load()
            xs = [x for x in range(80, 640) if g[x, y] < 235]
            x = (xs[0] + xs[-1]) // 2 if xs else 346
            return x, y
    return None


def diff(a, b, box):
    A = Image.open(a).convert("L").crop(box)
    B = Image.open(b).convert("L").crop(box)
    return round(sum(ImageStat.Stat(ImageChops.difference(A, B)).mean), 2)


def redact(path, out):
    img = Image.open(path).convert("RGB")
    w, h = img.size
    busy = rows_busy(img, 30, 860)
    band = max(runs([f >= 0.8 for f in busy], TOP), key=lambda r: r[1] - r[0], default=None)
    boxes = [(890, TOP, w, h)]                          # recommendations, chat, ads
    if band and band[1] - band[0] >= 120:
        boxes.append((0, max(TOP, band[0] - 12), 890, min(h, band[1] + 12)))
    for bx in boxes:
        img.paste(img.crop(bx).filter(ImageFilter.GaussianBlur(26)), bx)
    img.save(out)
    return boxes


def main():
    cmd = sys.argv[1]
    if cmd == "thumb":
        r = thumb(sys.argv[2])
        if not r:
            sys.exit(2)
        print(*r)
    elif cmd == "diff":
        print(diff(sys.argv[2], sys.argv[3], tuple(int(v) for v in sys.argv[4:8])))
    elif cmd == "redact":
        print(redact(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(64)


if __name__ == "__main__":
    main()
