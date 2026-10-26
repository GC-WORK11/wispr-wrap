#!/usr/bin/env python3
"""make-blob.py — the Sign-Flow mark: a glossy soft lotus-blob (FOUNDER-COLOR-1658).

The outline is the union of five lobe circles (centre, two mid, two outer) on a round base, softened by a small
radial smoothing so the valleys melt into one calm blob. It is written as ONE closed path (Catmull-Rom to cubic
Bezier), shaded by ONE subtle radial gradient (userSpaceOnUse) and lit by two white glints. No filters, no raster,
no masks. Writes web/icon.svg and tools/site/logo/mark-inline.txt (same drawing, caller-chosen gradient id).
usage: make-blob.py [repo_root]"""
import math, os, sys
import numpy as np
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
DY = -3.0                       # centre the drawing in the 64-box
CX, CY = 32.0, 38.0 + DY
LOBES0 = [  # (cx, cy, r)  — a lotus' fan on a round base
    (32.0, 41.0, 18.4),      # base
    (32.0, 22.8, 12.4),      # centre petal
    (19.5, 28.2, 10.6), (44.5, 28.2, 10.6),   # mid petals
    (12.4, 42.2, 7.6), (51.6, 42.2, 7.6),     # outer petals
]
LOBES = [(x, y + DY, r) for x, y, r in LOBES0]
SIGMA_DEG = 6.0
M = 2880
phis = np.linspace(-math.pi, math.pi, M, endpoint=False)
def ray(phi, c):
    """distance along the ray from (CX,CY) at angle phi (0 = up) to the far side of circle c"""
    ux, uy = math.sin(phi), -math.cos(phi)
    ox, oy = c[0] - CX, c[1] - CY
    b = ox * ux + oy * uy
    disc = b * b - (ox * ox + oy * oy - c[2] * c[2])
    return b + math.sqrt(disc) if disc >= 0 else 0.0
r = np.array([max(ray(p, c) for c in LOBES) for p in phis])
k = int(round(SIGMA_DEG / (360.0 / M) * 3))
ker = np.exp(-0.5 * (np.arange(-k, k + 1) / (SIGMA_DEG / (360.0 / M))) ** 2); ker /= ker.sum()
rs = np.convolve(np.concatenate([r[-k:], r, r[:k]]), ker, mode='valid')
N = 96
idx = np.linspace(0, M, N, endpoint=False).astype(int)
pts = [(CX + rs[i] * math.sin(phis[i]), CY - rs[i] * math.cos(phis[i])) for i in idx]
f = lambda v: ('%.2f' % v).rstrip('0').rstrip('.')
def spline(pts):
    n = len(pts); out = []
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        out.append(((p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6), (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6), p2))
    return out
D = 'M%s %s' % (f(pts[0][0]), f(pts[0][1])) + ''.join(' C%s %s %s %s %s %s' % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(c[0]), f(c[1])) for a, b, c in spline(pts)) + 'Z'
xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
BBOX = (min(xs), min(ys), max(xs), max(ys))
# two glints (rounded strokes), upper-left of the centre petal: light comes from the top-left
GL = [((24.4, 26.2 + DY), (27.6, 19.8 + DY), 3.6), ((34.0, 18.6 + DY), (35.6, 15.4 + DY), 3.0)]
def svg(grad_id='g', pretty=True):
    nl = '\n' if pretty else ''
    glints = ''.join('  <path d="M%s %sL%s %s" stroke="#FFFFFF" stroke-opacity=".95" stroke-width="%s" stroke-linecap="round" fill="none"/>%s' % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(w), nl) for a, b, w in GL)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' + nl +
      '  <defs><radialGradient id="%s" gradientUnits="userSpaceOnUse" cx="25" cy="18" r="44">' % grad_id +
      '<stop offset="0" stop-color="#FF4CA8"/><stop offset=".6" stop-color="#FF309C"/><stop offset="1" stop-color="#E02085"/></radialGradient></defs>' + nl +
      '  <path fill="url(#%s)" d="%s"/>' % (grad_id, D) + nl + glints + '</svg>' + nl)
if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else ROOT
    open(os.path.join(out, 'web/icon.svg'), 'w').write(svg('wwg'))
    open(os.path.join(out, 'tools/site/logo/mark-inline.txt'), 'w').write(svg('wwg', pretty=False))
    print('bbox', [round(v, 1) for v in BBOX], 'w=%.1f h=%.1f' % (BBOX[2] - BBOX[0], BBOX[3] - BBOX[1]), 'points', N)
