#!/usr/bin/env python3
# checks-logo.py — build favicon.ico, numeric checks, contact sheet.
import sys, re, os
from PIL import Image

ROOT = "/workspace/wispr-wrap-site"
WEB = os.path.join(ROOT, "web")
LOGO = os.path.join(ROOT, "tools/site/logo")
PROOF = os.path.join(ROOT, "proofs/ui-v2/site-logo")

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

# ---- 1. favicon.ico from the rendered 16/32/48 transparent marks ----
ico_srcs = [os.path.join(LOGO, f"fav-{s}.png") for s in (48, 32, 16)]
imgs = [Image.open(p).convert("RGBA") for p in ico_srcs]
imgs[0].save(os.path.join(WEB, "favicon.ico"),
             format="ICO",
             sizes=[(48, 48), (32, 32), (16, 16)])
ico = Image.open(os.path.join(WEB, "favicon.ico"))
check("favicon.ico built (16/32/48)", os.path.getsize(os.path.join(WEB, "favicon.ico")) > 0,
      f"{os.path.getsize(os.path.join(WEB,'favicon.ico'))} bytes")

# ---- 2. SVG parses as XML, <=12 elements, no banned tags ----
import xml.etree.ElementTree as ET
svg_txt = open(os.path.join(WEB, "icon.svg")).read()
try:
    tree = ET.fromstring(svg_txt)
    xml_ok = True
except Exception as e:
    xml_ok = False
check("SVG parses as XML", xml_ok)

# count drawable elements
drawables = re.findall(r"<(path|circle|rect|ellipse)\b", svg_txt)
check("SVG <=12 simple elements", len(drawables) <= 12, f"{len(drawables)} elements")
banned = re.findall(r"<(filter|lineargradient|radialgradient|mask|clippath|image|text|defs|stroke)\b", svg_txt, re.I)
check("no filters/gradients/masks/text/raster", len(banned) == 0, f"banned={banned}")
check("fills deep pink only", set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg_txt)) <= {"#C2185B", "#E91E63", "#AD1457"},
      str(set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg_txt))))

# ---- 3. mark fills 80-92% of viewBox (alpha coverage of bbox span) ----
def bbox_alpha(path):
    im = Image.open(path).convert("RGBA")
    a = im.split()[3]
    bb = a.getbbox()
    return im, a, bb

im512, a512, bb = bbox_alpha(os.path.join(PROOF, "mark-512-alpha.png"))
W, H = im512.size
# the alpha render used pad 0.06 -> drawable area is inner box. Report span frac.
span_w = (bb[2] - bb[0]) / W
span_h = (bb[3] - bb[1]) / H
span = max(span_w, span_h)
check("mark fills 80-92% of viewBox", 0.80 <= span <= 0.92, f"span={span:.3f} (w={span_w:.3f} h={span_h:.3f})")

# ---- 4. no stray pixels outside bbox (coverage contiguous-ish): check corners empty ----
# every corner 5% region must be fully transparent
def corner_clear(a):
    w, h = a.size
    m = int(min(w, h) * 0.05) + 1
    px = a.load()
    pts = []
    for (cx, cy) in [(0, 0), (w-1, 0), (0, h-1), (w-1, h-1)]:
        sx = range(0, m) if cx == 0 else range(w-m, w)
        sy = range(0, m) if cy == 0 else range(h-m, h)
        pts.append(max(px[x, y] for x in sx for y in sy))
    return max(pts)
check("no stray pixels in corners", corner_clear(a512) == 0, f"maxcorner_alpha={corner_clear(a512)}")

# ---- 5. no transparent holes inside the mark (flood from outside, holes stay opaque) ----
# simple: within bbox, count fully-transparent pixels enclosed. Use flood fill from border.
import collections
def holes(a, bb):
    w, h = a.size
    px = a.load()
    # mask of transparent pixels (alpha < 20)
    trans = lambda x, y: px[x, y] < 20
    seen = [[False]*w for _ in range(h)]
    dq = collections.deque()
    for x in range(w):
        for y in (0, h-1):
            if trans(x, y): dq.append((x, y)); seen[y][x] = True
    for y in range(h):
        for x in (0, w-1):
            if trans(x, y) and not seen[y][x]: dq.append((x, y)); seen[y][x] = True
    while dq:
        x, y = dq.popleft()
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nx, ny = x+dx, y+dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and trans(nx, ny):
                seen[ny][nx] = True; dq.append((nx, ny))
    # holes = transparent pixels not reached from border, inside bbox.
    # cluster them; ignore sub-pixel AA specks (clusters < 8 px are seams, not holes).
    grid = [[False]*w for _ in range(h)]
    for y in range(bb[1], bb[3]):
        for x in range(bb[0], bb[2]):
            if trans(x, y) and not seen[y][x]:
                grid[y][x] = True
    visited = [[False]*w for _ in range(h)]
    real_hole = 0
    for y in range(bb[1], bb[3]):
        for x in range(bb[0], bb[2]):
            if grid[y][x] and not visited[y][x]:
                q = collections.deque([(x, y)]); visited[y][x] = True; sz = 0
                while q:
                    cx, cy = q.popleft(); sz += 1
                    for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                        nx, ny = cx+dx, cy+dy
                        if (bb[0] <= nx < bb[2] and bb[1] <= ny < bb[3]
                                and grid[ny][nx] and not visited[ny][nx]):
                            visited[ny][nx] = True; q.append((nx, ny))
                if sz >= 8:
                    real_hole += sz
    return real_hole
hole_px = holes(a512, bb)
check("no transparent holes in mark", hole_px == 0, f"hole_px={hole_px} (AA specks ignored)")

# ---- 6. contrast of pink on #FAFAF9 >= 4.5:1 ----
def lum(rgb):
    def f(c):
        c /= 255.0
        return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055) ** 2.4
    r, g, b = rgb
    return 0.2126*f(r) + 0.7152*f(g) + 0.0722*f(b)
def ratio(fg, bg):
    l1, l2 = lum(fg), lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)
bg = (0xFA, 0xFA, 0xF9)
fills = sorted(set(re.findall(r'fill="#([0-9A-Fa-f]{6})"', svg_txt)))
worst = 999.0
for hexc in fills:
    rgb = tuple(int(hexc[i:i+2], 16) for i in (0, 2, 4))
    rr = ratio(rgb, bg)
    check(f"contrast #{hexc} on #FAFAF9 >=4.5", rr >= 4.5, f"{rr:.2f}:1")
    worst = min(worst, rr)

# ---- 7. contact sheet ----
def load(p): return Image.open(p).convert("RGB")
tiles = []
labels = []
for s in (16, 64, 512):
    for dpr in (1, 2):
        tiles.append(load(os.path.join(PROOF, f"mark-{s}-dpr{dpr}.png")))
        labels.append(f"{s}px @{dpr}x")
# normalize each into a 160x160 cell on #FAFAF9, centered (16px shown tiny -> keep true scale)
CELL = 180
PAD = 14
cols = 3
rows = 2
sheet = Image.new("RGB", (cols*CELL, rows*CELL + 30), (0xFA, 0xFA, 0xF9))
from PIL import ImageDraw
d = ImageDraw.Draw(sheet)
order = [(16,1),(64,1),(512,1),(16,2),(64,2),(512,2)]
for i, (s, dpr) in enumerate(order):
    im = load(os.path.join(PROOF, f"mark-{s}-dpr{dpr}.png"))
    # place at true pixel size, centered in cell (cap 512 -> fit)
    disp = im
    if max(im.size) > CELL - 2*PAD:
        k = (CELL - 2*PAD) / max(im.size)
        disp = im.resize((int(im.size[0]*k), int(im.size[1]*k)), Image.LANCZOS)
    cx = (i % cols) * CELL
    cy = (i // cols) * CELL
    ox = cx + (CELL - disp.size[0])//2
    oy = cy + (CELL - disp.size[1])//2
    sheet.paste(disp, (ox, oy))
    d.text((cx+6, cy+CELL-16), f"{s}px @{dpr}x", fill=(0x66,0x66,0x66))
sheet.save(os.path.join(LOGO, "proof-contact.png"))
sheet.save(os.path.join(PROOF, "proof-contact.png"))
check("contact sheet written", os.path.exists(os.path.join(LOGO, "proof-contact.png")))

allpass = all(ok for _, ok, _ in results)
print("\nSUMMARY:", "ALL PASS" if allpass else "SOME FAIL")
# write RESULT.txt
with open(os.path.join(PROOF, "RESULT.txt"), "w") as f:
    f.write("Wispr Wrap — site logo (LOGO stage) proof\n")
    f.write("mark: hand-built geometric deep-pink lotus, viewBox 0 0 64 64, 5 petals\n\n")
    for name, ok, detail in results:
        f.write(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}\n")
    f.write(f"\nSUMMARY: {'ALL PASS' if allpass else 'SOME FAIL'}\n")
sys.exit(0 if allpass else 1)
