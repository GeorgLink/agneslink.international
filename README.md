# agneslink.international

Static site for Agnes Link — gospel singer, and founder of the youth organisation
Agri-Connect Tanzania in the Kilimanjaro region.

**Live at <https://agneslink.international>**

## How it works

Plain HTML and CSS. No JavaScript, no database, no server-side code. GitHub Pages
serves `docs/` from `main` directly — there is no Actions workflow and nothing runs
on push, so what is committed is exactly what is served.

Every asset is self-hosted: fonts, images and video thumbnails all come from this
domain. Videos and maps are links rather than embeds, so no page contacts a third
party, the site sets no cookies, and it needs no consent banner.

## Layout

| Path | |
|---|---|
| `docs/` | The site. 26 pages, one stylesheet, all assets. This is what is served. |
| `content/` | Page content as structured JSON, plus the hand-written legal pages |
| `src/` | Tailwind entry point, design tokens, self-hosted `@font-face` rules |
| `tools/` | Build and verification scripts |
| `vendor/` | Fonts and video posters, stored locally so the build never needs the network |

## Building

Requires Python 3 and Node. Image optimisation additionally uses `sips` (macOS) and `cwebp`.

```bash
npm install
bash tools/build.sh          # images -> pages -> css
python3 tools/check_site.py  # ten checks, all must pass
```

Tailwind scans the generated HTML for class names, so the pages have to be built
before the CSS — `tools/build.sh` handles that ordering.

To change wording, edit `content/pages/<slug>.json` and rebuild. To change the design,
edit `src/theme.css`; the block renderers live in `tools/build_site.py`. The two legal
pages are hand-written HTML in `content/` and bypass the block renderer.

## Verification

`tools/check_site.py` enforces the properties the site depends on:

- no subresource loads from another origin
- no `<script>` tags and no inline event handlers
- no form elements
- every local reference resolves, every image has `alt` and explicit dimensions
- every page renders the text its source describes
- no page exceeds 2 MB at a 1280px viewport

`tools/responsive_check.py` carries the probe for checking horizontal overflow across
breakpoints in a browser.

## Notes

- `docs/CNAME` sets the custom domain and is load-bearing — without it, `www` stops
  resolving to this repo.
- `docs/.nojekyll` stops Pages processing the output through Jekyll.
- Full-resolution originals live outside the repo. `tools/optimize_images.py` needs
  them in `source-images/`; the committed WebP is enough to serve and rebuild the site.
