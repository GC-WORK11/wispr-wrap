# Sign-Flow landing

Static site — builds to `dist/` with no server-side code. Deployable to any
static host (Vercel, Cloudflare Pages, GitHub Pages, nginx).

```bash
npm install     # installs Astro + dev deps
npm run build   # outputs the static site to dist/
```

Preview the built site locally:

```bash
npm run preview            # serves dist/
npx serve dist             # or any static file server
```

Layout:

- `src/pages/index.astro` — the single page
- `src/components/` — Hero, Send, Teach, Try, How, Footer sections
- `public/media/clips/` — real product footage (muted autoplay loops + posters)
- `dist/` — build output (generated, not committed)
