#!/usr/bin/env python3
"""compose.py — put a phone screenshot next to the laptop's real screen.

  python3 compose.py <phone.png> <laptop.png> <out.png> [caption]

The phone shot is scaled to the laptop's height; a thin caption strip says what
the picture proves. Pure PIL, no network, no people."""
import sys
from PIL import Image, ImageDraw, ImageFont


def font(size):
    for f in ("/usr/share/fonts/truetype/inter/Inter-Regular.ttf",
              "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    phone, laptop, out = sys.argv[1:4]
    cap = sys.argv[4] if len(sys.argv) > 4 else ""
    p = Image.open(phone).convert("RGB")
    l = Image.open(laptop).convert("RGB")
    H = l.height
    pw = round(p.width * H / p.height)
    p = p.resize((pw, H), Image.LANCZOS)
    gap, strip = 28, (44 if cap else 0)
    W = pw + gap + l.width + gap * 2
    img = Image.new("RGB", (W, H + gap * 2 + strip), (233, 231, 227))
    img.paste(p, (gap, gap))
    img.paste(l, (gap + pw + gap, gap))
    d = ImageDraw.Draw(img)
    d.rectangle([gap - 1, gap - 1, gap + pw, gap + H], outline=(205, 202, 197))
    d.rectangle([gap * 2 + pw - 1, gap - 1, gap * 2 + pw + l.width, gap + H], outline=(205, 202, 197))
    f = font(15)
    d.text((gap, gap + H + 14), "phone", fill=(90, 90, 90), font=f)
    d.text((gap * 2 + pw, gap + H + 14), "laptop (real X11 screen)" + ("  ·  " + cap if cap else ""),
           fill=(90, 90, 90), font=f)
    img.save(out, optimize=True)


if __name__ == "__main__":
    main()
