# Wispr Wrap brand
- `icon.svg` — the mark: a five-petal lotus on a water line (deep pink #C2185B / #D81B60 / #E91E63). Built from ONE petal path
  (`M32 7C38 15 40.5 26 32 46C23.5 26 26 15 32 7Z`) rotated about the base point (32,46): centre, ±30°, ±58° at 0.82 scale, plus a 3px round-capped cup.
  No traced or auto-generated geometry. Checked crisp at 16, 24, 32, 64, 128 and 512 px on #fafaf9 and white.
- `icon-app.svg` (full-bleed, maskable/apple-touch) and `icon-app-round.svg` (192/512 "any") — white lotus on the deep-pink field, opaque tints so petal overlaps never show seams.
- PNGs in `web/`: icon-192.png, icon-512.png, icon-maskable-512.png, apple-touch-icon.png (rendered from these SVGs with headless Chrome, transparent corners where rounded).
- In the app: the loading screen (inline in index.html) blooms the mark petal by petal, then dissolves while the ASCII lotus replays its sprout. Sound: web/js/sound.js, five quiet synthesised tones, no toggle.
