#!/usr/bin/env bash
# Full static build. Order matters: Tailwind scans the generated HTML, so the
# pages must exist before the CSS is compiled.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 1/4 vendor assets (fonts, video posters)"
python3 tools/fetch_vendor.py

echo "==> 2/4 images"
python3 tools/optimize_images.py "$@"

echo "==> 3/4 pages"
# Remove only the generated page directories; assets/ is expensive to rebuild.
find docs -mindepth 1 -maxdepth 1 -type d ! -name assets -exec rm -rf {} +
python3 tools/build_site.py

echo "==> 4/4 css"
npx tailwindcss -i src/site.css -o docs/assets/css/site.css --minify

echo
echo "docs/ is ready:"
du -sh docs
