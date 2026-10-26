// Static output only: the build is a folder of plain files.
import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  trailingSlash: 'ignore',
  prefetch: false,
  devToolbar: { enabled: false },
  build: { inlineStylesheets: 'always' },
  compressHTML: true,
});
