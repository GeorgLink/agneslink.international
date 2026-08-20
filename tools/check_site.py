#!/usr/bin/env python3
"""Verify the built site.

The load-bearing checks are the ones about origin and inertness: nothing may be
fetched from another host, and there must be no script or form anywhere. The
rest guard against the ordinary ways a static build rots.

    python3 tools/check_site.py [--site site]
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from html.parser import HTMLParser

CONTENT = "content"
EXTERNAL = re.compile(r"^(?:https?:)?//", re.I)
# Any URI scheme that is not a document fetch: mailto:, tel:, skype:, etc.
NON_HTTP_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:(?!//)", re.I)
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)")
CSS_IMPORT = re.compile(r"@import\s+(?:url\()?['\"]([^'\"]+)")

# Subresource attributes -- these cause a fetch. `href` on <a> does not.
FETCHING = {"src", "srcset", "poster", "data"}
FETCHING_LINK_RELS = {"stylesheet", "preload", "prefetch", "preconnect",
                      "dns-prefetch", "modulepreload", "icon", "apple-touch-icon"}

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def to_text(markup):
    """Visible text of an HTML fragment."""
    if not markup:
        return ""
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    return ok


class Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.subresources = []   # (attr, url)
        self.links = []          # <a href>
        self.scripts = 0
        self.inline_handlers = []
        self.forms = Counter()
        self.images = []         # (src, alt, width, height)
        self.text = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "script":
            self.scripts += 1
        if tag in ("form", "input", "textarea", "select", "button"):
            self.forms[tag] += 1
        for key in a:
            if key.startswith("on"):
                self.inline_handlers.append("%s[%s]" % (tag, key))
        for key in FETCHING:
            if key in a and a[key]:
                for part in (a[key].split(",") if key == "srcset" else [a[key]]):
                    url = part.strip().split(" ")[0]
                    if url:
                        self.subresources.append((tag + "@" + key, url))
        if tag == "link":
            rel = (a.get("rel") or "").lower()
            if any(r in FETCHING_LINK_RELS for r in rel.split()) and a.get("href"):
                self.subresources.append("link@" + rel, ) if False else \
                    self.subresources.append(("link@" + rel, a["href"]))
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "img":
            self.images.append((a.get("src", ""), a.get("alt"), a.get("width"), a.get("height")))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self.text.append(data)


def expected_text(page):
    """The authored text of a page, straight from its blocks."""
    parts = []
    for b in page.get("blocks", []):
        kind = b.get("type")
        if kind == "heading":
            parts.append(b.get("text", ""))
        elif kind == "richtext":
            parts.append(to_text(b.get("html", "")))
        elif kind in ("icon-box", "image-box"):
            parts.append(b.get("title", ""))
            parts.append(re.sub(r"[*\[\]()#]", " ", b.get("markdown", "")))
        elif kind == "icon-list":
            parts.extend(i.get("text", "") for i in b.get("items", []))
        elif kind == "testimonial":
            parts.append(re.sub(r"[*\[\]()#]", " ", b.get("markdown", "")))
            parts.append(b.get("name", ""))
            parts.append(b.get("role", ""))
        elif kind == "button":
            parts.append(b.get("text", ""))
        elif kind == "map":
            parts.append(b.get("query", ""))
        elif kind == "raw-html":
            parts.append(to_text(b.get("html", "")))
        elif kind in ("image", "gallery", "carousel"):
            if kind == "image":
                parts.append(b.get("alt") or b.get("title") or "")
            else:
                parts.extend(i.get("alt") or "" for i in b.get("images", []))
    return re.sub(r"\s+", " ", " ".join(parts))


def local_path(site, url, page=None):
    """Resolve a URL to a file on disk, or None if it is not a local file.

    Paths in the build are relative to the referencing page, so resolution
    needs that page's directory as the base.
    """
    url = url.split("#")[0].split("?")[0]
    if not url or NON_HTTP_SCHEME.match(url):
        return None
    if EXTERNAL.match(url):
        return None
    if url.startswith("/"):
        return os.path.normpath(os.path.join(site, url.lstrip("/")))
    base = os.path.dirname(page) if page else site
    return os.path.normpath(os.path.join(base, url))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="docs")
    args = ap.parse_args()
    site = os.path.abspath(args.site)

    pages = []
    for base, _, files in os.walk(site):
        if os.path.join(site, "assets") in base:
            continue
        for name in files:
            if name.endswith(".html"):
                pages.append(os.path.join(base, name))
    pages.sort()

    scans = {}
    for path in pages:
        scan = Scan()
        scan.feed(open(path, encoding="utf-8").read())
        scan.close()
        scans[path] = scan

    def rel(path):
        return os.path.relpath(path, site)

    # 1. no external subresources
    external = []
    for path, scan in scans.items():
        for where, url in scan.subresources:
            if EXTERNAL.match(url):
                external.append("%s %s -> %s" % (rel(path), where, url[:60]))
    css_path = os.path.join(site, "assets", "css", "site.css")
    css = open(css_path, encoding="utf-8").read() if os.path.exists(css_path) else ""
    for url in CSS_URL.findall(css) + CSS_IMPORT.findall(css):
        if EXTERNAL.match(url):
            external.append("site.css -> %s" % url[:60])
    check("no subresource loads from another origin", not external,
          "; ".join(external[:4]))

    # 2. inert: no script, no inline handlers
    scripts = [rel(p) for p, s in scans.items() if s.scripts]
    handlers = ["%s %s" % (rel(p), h) for p, s in scans.items() for h in s.inline_handlers]
    check("no <script> tags", not scripts, "; ".join(scripts[:4]))
    check("no inline event handlers", not handlers, "; ".join(handlers[:4]))

    # 3. no forms survive
    forms = ["%s %s" % (rel(p), dict(s.forms)) for p, s in scans.items() if s.forms]
    check("no form elements remain", not forms, "; ".join(forms[:4]))

    # 4. every local subresource resolves
    missing = []
    for path, scan in scans.items():
        for where, url in scan.subresources:
            target = local_path(site, url, path)
            if target and not os.path.exists(target):
                missing.append("%s %s -> %s" % (rel(path), where, url))
    for url in CSS_URL.findall(css):
        target = local_path(site, url, css_path)
        if target and not os.path.exists(target):
            missing.append("site.css -> %s" % url)
    check("every local subresource exists on disk", not missing,
          "%d missing%s" % (len(missing), (": " + "; ".join(missing[:4])) if missing else ""))

    # 5. structure matches the sitemap
    sitemap = json.load(open(os.path.join(CONTENT, "sitemap.json"), encoding="utf-8"))
    slugs = {p["slug"] for p in sitemap["pages"]}
    expected = {"index.html"} | {os.path.join(s, "index.html") for s in slugs}
    actual = {rel(p) for p in pages}
    check("one page per sitemap entry, plus the root",
          expected == actual,
          "missing=%s extra=%s" % (sorted(expected - actual)[:3], sorted(actual - expected)[:3]))

    # 6. internal links resolve
    documented = {
        (b["page"], b["href"].split("#")[0].strip("/"))
        for b in sitemap.get("broken_internal_links", [])
    }
    dangling, known = [], []
    for path, scan in scans.items():
        page_slug = os.path.basename(os.path.dirname(path)) or "index"
        for href in scan.links:
            if EXTERNAL.match(href) or href.startswith("#") or NON_HTTP_SCHEME.match(href):
                continue
            target = local_path(site, href, path)
            if target is None:
                continue
            candidate = target if os.path.splitext(target)[1] else os.path.join(target, "index.html")
            if os.path.exists(candidate):
                continue
            resolved = os.path.relpath(os.path.dirname(candidate), site).strip("./")
            key = (page_slug, resolved)
            (known if key in documented else dangling).append("%s -> %s" % (rel(path), href))
    check("internal links resolve (documented 404s allowed)", not dangling,
          "%d dangling%s" % (len(dangling), (": " + "; ".join(dangling[:4])) if dangling else "")
          + ("; %d documented" % len(known) if known else ""))

    # 7. images are accessible and reserve their space
    bad_img = []
    for path, scan in scans.items():
        for src, alt, w, h in scan.images:
            if alt is None:
                bad_img.append("%s missing alt: %s" % (rel(path), src[:48]))
            elif not (w and h):
                bad_img.append("%s missing width/height: %s" % (rel(path), src[:48]))
    check("every <img> has alt and explicit width/height", not bad_img,
          "%d issues%s" % (len(bad_img), (": " + "; ".join(bad_img[:3])) if bad_img else ""))

    # 8. every page renders the text its source describes. Compared against
    #    the block JSON rather than the Markdown, since a Markdown rendering
    #    carries link targets and image paths as literal text. Image alt text
    #    lives in an attribute, so it is folded in to compare like with like.
    low, overridden = [], []
    for path, scan in scans.items():
        page_slug = os.path.basename(os.path.dirname(path))
        if not page_slug or page_slug not in slugs:
            continue
        if os.path.exists(os.path.join("content", page_slug + ".html")):
            # Legal pages are maintained as hand-written HTML, not blocks, so a
            # block-by-block comparison does not apply.
            overridden.append(page_slug)
            continue
        blob = os.path.join(CONTENT, "pages", page_slug + ".json")
        if not os.path.exists(blob):
            continue
        want = Counter(re.findall(r"\w+", expected_text(json.load(open(blob, encoding="utf-8"))).lower()))
        rendered = " ".join(scan.text) + " " + " ".join(a or "" for _, a, _, _ in scan.images)
        got = Counter(re.findall(r"\w+", rendered.lower()))
        if not want:
            continue
        covered = sum(want.values()) - sum((want - got).values())
        ratio = covered / sum(want.values())
        if ratio < 0.95 and sum(want.values()) >= 20:
            sample = ", ".join(w for w, _ in (want - got).most_common(5))
            low.append("%s %.0f%% (%s)" % (page_slug, ratio * 100, sample))
    detail = ", ".join(low[:6])
    if overridden:
        detail += ("; " if detail else "") + (
            "%d page(s) exempt as reviewed rewrites: %s"
            % (len(overridden), ", ".join(sorted(overridden))))
    check("every page carries >=95% of its exported text", not low, detail)

    # 9. weight budget
    weights = {}
    for path, scan in scans.items():
        total = os.path.getsize(path) + os.path.getsize(css_path)
        seen = set()
        for where, url in scan.subresources:
            if where == "img@srcset":
                continue          # accounted for via the chosen variant below
            target = local_path(site, url, path)
            if target and target not in seen and os.path.exists(target):
                seen.add(target)
                total += os.path.getsize(target)
        # Fonts are shared across the site and cached after the first page.
        total += sum(os.path.getsize(os.path.join(site, "assets", "fonts", f))
                     for f in os.listdir(os.path.join(site, "assets", "fonts"))
                     if f.endswith(".woff2")) if os.path.isdir(
                         os.path.join(site, "assets", "fonts")) else 0
        weights[rel(path)] = total
    heavy = ["%s %.1f MB" % (p, w / 1e6) for p, w in weights.items() if w > 2e6]
    check("no page over 2 MB at a 1280px viewport", not heavy, "; ".join(sorted(heavy)[:5]))

    biggest = sorted(weights.items(), key=lambda kv: -kv[1])[:3]
    print("\n%d pages · css %.0f KB · heaviest: %s"
          % (len(pages), os.path.getsize(css_path) / 1000,
             ", ".join("%s %.0f KB" % (p, w / 1000) for p, w in biggest)))
    print("total site: %.1f MB"
          % (sum(os.path.getsize(os.path.join(b, f))
                 for b, _, fs in os.walk(site) for f in fs) / 1e6))

    failed = [n for n, ok in results if not ok]
    print("\n%d/%d checks passed" % (len(results) - len(failed), len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
