#!/usr/bin/env python3
"""Turn full-resolution source photos into web-sized WebP under docs/assets/.

Photographs come off a phone at sizes no browser should download. Each image
becomes up to three WebP widths for `srcset`; originals are never upscaled, so
a small image stays a single file.

Also stages the self-hosted fonts and video posters from vendor/.

Uses `sips` (macOS built-in) to resize and `cwebp` to encode.

    python3 tools/optimize_images.py [--force] [--jobs N]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

SRC = "source-images"
OUT = "docs/assets"
WIDTHS = [480, 960, 1600]
# Large variants are only ever seen on retina displays, where compression
# artefacts are far less visible -- so they can be pushed harder.
QUALITY = {480: 78, 960: 78, 1600: 70}
POSTER_WIDTH = 720
POSTER_QUALITY = 72


def run(cmd):
    return subprocess.run(cmd, capture_output=True, check=False)


def dimensions(path):
    out = run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path]).stdout.decode()
    w = h = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            w = int(line.split(":")[1])
        elif line.startswith("pixelHeight:"):
            h = int(line.split(":")[1])
    return w, h


def encode(src, dst, width, native_w, quality):
    """Resize to `width` (skipping the resize when the source is smaller) and
    encode to WebP. sips writes intermediates as PNG so cwebp gets clean input."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if native_w and native_w <= width:
        return run(["cwebp", "-quiet", "-q", str(quality), src, "-o", dst]).returncode == 0
    tmp = dst + ".tmp.png"
    if run(["sips", "-Z", str(width), src, "--out", tmp]).returncode != 0:
        return False
    ok = run(["cwebp", "-quiet", "-q", str(quality), tmp, "-o", dst]).returncode == 0
    if os.path.exists(tmp):
        os.remove(tmp)
    return ok


def collect(root, exts):
    for base, _, files in os.walk(root):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in exts:
                yield os.path.join(base, name)


def process_image(path, force):
    rel = os.path.relpath(path, SRC)
    stem, _ = os.path.splitext(rel)
    native_w, native_h = dimensions(path)
    if not native_w:
        return rel, None, "unreadable"

    made = []
    for width in WIDTHS:
        # Only emit a wider variant when the source can actually fill it.
        if width > WIDTHS[0] and native_w <= width // 2:
            continue
        dst = os.path.join(OUT, "img", "%s-%d.webp" % (stem, width))
        if not os.path.exists(dst) or force:
            if not encode(path, dst, width, native_w, QUALITY[width]):
                return rel, None, "encode failed"
        actual = min(width, native_w)
        made.append({
            "width": actual,
            "height": round(native_h * actual / native_w),
            "path": "/assets/img/%s-%d.webp" % (stem.replace(os.sep, "/"), width),
            "bytes": os.path.getsize(dst),
        })
    return rel, {"native": [native_w, native_h], "variants": made}, None


# The site's own logo, used for the browser tab icon.
FAVICON_SOURCE = "2019/09/LOGO.png"
FAVICON_SIZES = [16, 32, 48, 180]


def build_favicons(force):
    """Square the logo and emit PNG icons plus a real .ico container.

    The logo is 623x564, so it is padded to a square first -- squashing it to
    fit would distort the artwork.
    """
    source = os.path.join(SRC, FAVICON_SOURCE)
    if not os.path.exists(source):
        return []
    native_w, native_h = dimensions(source)
    side = max(native_w or 0, native_h or 0)
    if not side:
        return []

    os.makedirs(os.path.join(OUT, "icons"), exist_ok=True)
    square = os.path.join(OUT, "icons", "_square.png")
    # White pad rather than transparent: the logo has white in it already, and
    # a transparent icon reads as a hole against a dark browser chrome.
    run(["sips", "-p", str(side), str(side),
         "--padColor", "FFFFFF", source, "--out", square])

    made = []
    for size in FAVICON_SIZES:
        dst = os.path.join(OUT, "icons", "favicon-%d.png" % size)
        if not os.path.exists(dst) or force:
            run(["sips", "-z", str(size), str(size), square, "--out", dst])
        made.append((size, dst))

    write_ico([d for s_, d in made if s_ in (16, 32, 48)],
              os.path.join(OUT, "..", "favicon.ico"))
    if os.path.exists(square):
        os.remove(square)
    return made


def write_ico(png_paths, out_path):
    """Wrap PNGs in an ICO container (PNG-in-ICO, supported since Vista)."""
    import struct

    entries = []
    for path in png_paths:
        with open(path, "rb") as fh:
            entries.append(fh.read())
    if not entries:
        return
    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory, blobs = b"", b""
    for data, path in zip(entries, png_paths):
        size = int(os.path.basename(path).split("-")[1].split(".")[0])
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size, 0 if size >= 256 else size,
            0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(header + directory + blobs)


def stage_vendor(force):
    """Fonts and video posters, straight from vendor/."""
    fonts_src, fonts_dst = "vendor/fonts", os.path.join(OUT, "fonts")
    if os.path.isdir(fonts_src):
        os.makedirs(fonts_dst, exist_ok=True)
        for name in os.listdir(fonts_src):
            if name.endswith(".woff2"):
                shutil.copy2(os.path.join(fonts_src, name), os.path.join(fonts_dst, name))

    posters = {}
    src_dir = "vendor/posters"
    if os.path.isdir(src_dir):
        for name in sorted(os.listdir(src_dir)):
            if not name.endswith(".jpg"):
                continue
            video_id = os.path.splitext(name)[0]
            src = os.path.join(src_dir, name)
            dst = os.path.join(OUT, "posters", "%s.webp" % video_id)
            native_w, native_h = dimensions(src)
            if not os.path.exists(dst) or force:
                encode(src, dst, POSTER_WIDTH, native_w, POSTER_QUALITY)
            width = min(POSTER_WIDTH, native_w or POSTER_WIDTH)
            posters[video_id] = {
                "path": "/assets/posters/%s.webp" % video_id,
                "width": width,
                "height": round((native_h or 405) * width / (native_w or 720)),
            }
    return posters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    for binary in ("sips", "cwebp"):
        if not shutil.which(binary):
            print("missing required tool: %s" % binary, file=sys.stderr)
            return 1

    if not os.path.isdir(SRC):
        # The full-resolution originals live outside the repo. The optimised
        # WebP under docs/assets/img is committed, so the site still builds --
        # only re-encoding needs the originals.
        print("  no %s/ directory; keeping the committed images as-is" % SRC)
        return 0

    images = list(collect(SRC, {".jpg", ".jpeg", ".png"}))
    print("optimizing %d images (%d jobs) ..." % (len(images), args.jobs))

    sizes, failures = {}, []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for rel, info, err in pool.map(lambda p: process_image(p, args.force), images):
            if err:
                failures.append("%s: %s" % (rel, err))
            else:
                sizes[rel.replace(os.sep, "/")] = info

    posters = stage_vendor(args.force)

    icons = build_favicons(args.force)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "sizes.json"), "w", encoding="utf-8") as fh:
        json.dump({"images": sizes, "posters": posters}, fh, indent=1, sort_keys=True)

    before = sum(os.path.getsize(p) for p in images)
    after = sum(v["bytes"] for info in sizes.values() for v in info["variants"])
    print("  %d images -> %d WebP variants" % (len(sizes), sum(len(i["variants"]) for i in sizes.values())))
    print("  %.1f MB -> %.1f MB (%.0f%% smaller)"
          % (before / 1e6, after / 1e6, 100 * (1 - after / before) if before else 0))
    print("  %d posters staged, fonts staged" % len(posters))
    print("  %d favicons generated from %s" % (len(icons), FAVICON_SOURCE))
    for f in failures:
        print("  !! %s" % f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
