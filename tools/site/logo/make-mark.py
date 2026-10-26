#!/usr/bin/env python3
"""make-mark.py — the Wispr Wrap lotus, built from five mirrored leaf-shaped petals (cubic Beziers).
Writes web/icon.svg (viewBox 64) and tools/site/logo/mark-inline.txt (24-unit, currentColor).
Every petal is one closed path with exactly mirrored coordinates; nothing else is in the file."""
import re, sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
# petals in a 64-unit box, base point (32, 55). left side defined; the right side is the exact mirror.
CENTER = [(32, 55), [(23, 45), (23, 21), (32, 4)], [(41, 21), (41, 45), (32, 55)]]
MID_L  = [(32, 55), [(19, 51), (8, 35), (15, 14)], [(25, 22), (32, 37), (32, 55)]]
OUT_L  = [(32, 55), [(16, 58), (3, 47), (4, 30)], [(15, 35), (26, 43), (32, 55)]]
SCALE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.92   # fit 80-92% of the box
def tf(p, size=64, off=0.0):
    x, y = p
    return ((32 + (x - 32) * SCALE) / 64 * size + off, (32 + (y - 32) * SCALE + 1.0) / 64 * size + off)
def mirror(path):
    return [(64 - path[0][0], path[0][1]), [(64 - x, y) for x, y in path[1]], [(64 - x, y) for x, y in path[2]]]
def d(path, size=64):
    f = lambda q: '%g %g' % tuple(round(v, 2) for v in tf(q, size))
    a, b, c = path
    return 'M%s C %s, %s, %s C %s, %s, %s Z' % (f(a), f(b[0]), f(b[1]), f(b[2]), f(c[0]), f(c[1]), f(c[2]))
OUTER, MIDC, CENT = '#AD1457', '#C2185B', '#D81B60'
paths = [(OUTER, OUT_L), (OUTER, mirror(OUT_L)), (MIDC, MID_L), (MIDC, mirror(MID_L)), (CENT, CENTER)]
svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n' + \
      '\n'.join('  <path fill="%s" d="%s"/>' % (col, d(p)) for col, p in paths) + '\n</svg>\n'
open(os.path.join(ROOT, 'web/icon.svg'), 'w').write(svg)
inline = '<svg viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg"><path d="%s"/></svg>\n' % \
         ' '.join(d(p, 24) for _, p in paths)
open(os.path.join(ROOT, 'tools/site/logo/mark-inline.txt'), 'w').write(inline)
print(svg)
