#!/usr/bin/env python3
"""One-time download of the assets the site must self-host.

Everything the finished site loads has to come from our own origin, so the two
things that would otherwise be hot-linked -- the brand fonts and the YouTube
poster images -- are fetched once into `vendor/` and committed. After this runs
the build is fully offline.

Each file's source URL and SHA-256 are recorded in `vendor/manifest.json` so the
provenance of every binary in the repo is auditable.

    python3 tools/fetch_vendor.py [--force]
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

VENDOR = "vendor"

# Google serves woff2 only to user agents it recognises as modern.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

FONT_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Mouse+Memoirs"
    "&family=Playfair+Display:wght@400;700"
    "&display=swap"
)

# The videos linked from the site; posters are stored locally so no page
# ever contacts YouTube unless a visitor clicks through.
POSTER_IDS = [
    "2bW7g4b3wPA", "5b4kCtG8-uQ", "I01J6NcNhPA", "Mv5hf2aI5uA",
    "OTuMpbjmzak", "Y-HG0lXgznA", "hwktlJNbjjo", "mX4bQaQYy5I",
    "o2K0U1ZKncY", "zg7azS3Y_A0", "zu6Swo7gMBM",
]

FONT_FACE = re.compile(r"@font-face\s*\{(.*?)\}", re.S)
SRC_URL = re.compile(r"url\((https://[^)]+\.woff2)\)")


def get(url, binary=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return data if binary else data.decode("utf-8")


def save(path, data, manifest, source):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    manifest[os.path.relpath(path, VENDOR)] = {
        "source": source,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def fetch_fonts(manifest, force):
    """Download the Google Fonts CSS, then every woff2 it points at.

    The CSS is rewritten to local paths and kept verbatim otherwise, so the
    per-subset `unicode-range` rules survive -- they are what stop the browser
    downloading the latin-ext file unless a page actually needs it.
    """
    out_dir = os.path.join(VENDOR, "fonts")
    css_path = os.path.join(out_dir, "fonts.css")
    if os.path.exists(css_path) and not force:
        print("  fonts: already present, skipping (use --force to refetch)")
        return

    print("  fonts: fetching stylesheet ...")
    css = get(FONT_CSS_URL, binary=False)

    seen = {}
    for url in SRC_URL.findall(css):
        if url in seen:
            continue
        name = url.rstrip("/").split("/")
        # e.g. .../s/playfairdisplay/v37/<hash>.woff2 -> playfairdisplay-<hash>.woff2
        family = name[-3] if len(name) >= 3 else "font"
        filename = "%s-%s" % (family, name[-1])
        print("    %s" % filename)
        save(os.path.join(out_dir, filename), get(url), manifest, url)
        seen[url] = filename

    local_css = css
    for url, filename in seen.items():
        local_css = local_css.replace("url(%s)" % url, "url(/assets/fonts/%s)" % filename)

    families = sorted({m.group(1) for m in re.finditer(r"font-family:\s*'([^']+)'", local_css)})
    header = ("/* Self-hosted from Google Fonts, fetched once by "
              "tools/fetch_vendor.py.\n   Families: %s */\n" % ", ".join(families))
    save(css_path, (header + local_css).encode("utf-8"), manifest, FONT_CSS_URL)
    print("  fonts: %d files, families: %s" % (len(seen), ", ".join(families)))


def fetch_posters(manifest, force):
    """YouTube poster images. maxres is not always generated; hq always is."""
    out_dir = os.path.join(VENDOR, "posters")
    got = 0
    for video_id in POSTER_IDS:
        path = os.path.join(out_dir, "%s.jpg" % video_id)
        if os.path.exists(path) and not force:
            got += 1
            continue
        for quality in ("maxresdefault", "hqdefault"):
            url = "https://img.youtube.com/vi/%s/%s.jpg" % (video_id, quality)
            try:
                data = get(url)
            except urllib.error.HTTPError:
                continue
            # YouTube answers 200 with a tiny grey placeholder for missing sizes.
            if len(data) < 6000:
                continue
            save(path, data, manifest, url)
            print("    %s.jpg (%s, %.0f KB)" % (video_id, quality, len(data) / 1000))
            got += 1
            break
        else:
            print("    !! no poster available for %s" % video_id)
    print("  posters: %d/%d" % (got, len(POSTER_IDS)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="refetch even if present")
    args = ap.parse_args()

    manifest_path = os.path.join(VENDOR, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, encoding="utf-8"))

    print("fetching vendor assets into %s/ ..." % VENDOR)
    try:
        fetch_fonts(manifest, args.force)
        fetch_posters(manifest, args.force)
    except urllib.error.URLError as exc:
        print("\nnetwork error: %s" % exc, file=sys.stderr)
        print("These downloads happen once; rerun when you have connectivity.", file=sys.stderr)
        return 1

    os.makedirs(VENDOR, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
    print("manifest: %d files recorded" % len(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
