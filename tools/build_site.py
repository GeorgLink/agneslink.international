#!/usr/bin/env python3
"""Render content/ into a static HTML site under docs/.

Every page is plain HTML: no JavaScript, no forms, and no subresource from any
other origin. Pages are described as an ordered list of blocks, each tagged with
a section and column index; those indices become responsive grids that stack on
small screens.

    python3 tools/build_site.py
"""

import html
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONTENT = "content"
OUT = "docs"

# Which arm of the site a page belongs to; drives the accent colour.
BRANCH_ROOTS = {
    "agri-connect-tanzania": "ngo",
    "agneslink-channel": "music",
}

# Pages where the CD-ordering address is the right contact, per the decision
# that Peter handles orders and Agnes everything else.
ORDERS_EMAIL = "peter.w.link@gmail.com"
GENERAL_EMAIL = "agnesier@yahoo.com"
ORDERS_PAGES = {"albums", "audio-dvd", "concerts"}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}

FRONT_HEADLINE = "Supporting youth to support themselves"

DEFAULT_DESCRIPTION = (
    "Agnes Link — gospel singer, and founder of Agri-Connect Tanzania, "
    "a youth organisation in the Kilimanjaro region."
)

BRANCH_LABEL = {
    "ngo": "Agri-Connect Tanzania",
    "music": "AgnesLink Channel",
    "general": "Agnes Link",
}


def relative(url, depth):
    """Rewrite a root-relative URL for a page nested `depth` levels down.

    Keeping every reference relative means the same build works at a domain
    root, under a project subpath like georglink.github.io/<repo>/, and from
    the local filesystem -- so the site is previewable before DNS points at it.
    """
    if not url.startswith("/"):
        return url
    prefix = "../" * depth
    return (prefix + url.lstrip("/")) or "./"


def esc(text):
    return html.escape(text or "", quote=True)


class Builder:
    def __init__(self):
        self.sitemap = load(os.path.join(CONTENT, "sitemap.json"))
        self.tokens = load(os.path.join(CONTENT, "design-tokens.json"))
        self.sizes = load(os.path.join(OUT, "assets", "sizes.json"))
        self.forms = {}
        forms_dir = os.path.join(CONTENT, "forms")
        if os.path.isdir(forms_dir):
            for name in os.listdir(forms_dir):
                data = load(os.path.join(forms_dir, name))
                self.forms[str(data["id"])] = data

        self.menus = self.sitemap.get("menus", {})
        self.main_menu = pick_menu(self.menus, "haupt")
        self.footer_menu = pick_menu(self.menus, "foot")
        self.branch_of = self.map_branches()
        self.slugs = {p["slug"] for p in self.sitemap["pages"]}
        self.front = self.sitemap.get("front_page")
        self.warnings = []
        self.documents = []
        self._contact = None
        seo_path = os.path.join(CONTENT, "seo.json")
        self.seo = load(seo_path) if os.path.exists(seo_path) else {}
        self.depth = 0   # directory levels between the page and the site root
        self._overrides = {}
        self._used_overrides = set()

    # -- branches ------------------------------------------------------
    def map_branches(self):
        """A page inherits its branch from the top-level menu item above it."""
        branch = {}
        for node in self.main_menu:
            root = BRANCH_ROOTS.get(node.get("slug"), "general")
            for slug in walk_slugs(node):
                branch[slug] = root
        return branch

    def branch(self, slug):
        return self.branch_of.get(slug, "general")

    # -- images --------------------------------------------------------
    def picture(self, src, alt, classes, sizes_attr, caption=None):
        """<img> with srcset built from the optimizer's manifest."""
        key = src[len("assets/"):] if src.startswith("assets/") else src
        stem = os.path.splitext(key)[0]
        info = self.sizes["images"].get(key)
        if not info:
            self.warnings.append("no optimized variant for %s" % src)
            return ""
        variants = info["variants"]
        srcset = ", ".join("%s %dw" % (relative(v["path"], self.depth), v["width"])
                           for v in variants)
        # `src` is the mid variant: a sane fallback, and never the 1600 file.
        # `srcset` + `sizes` decide what is actually fetched.
        default = variants[min(1, len(variants) - 1)]
        img = (
            '<img src="%s" srcset="%s" sizes="%s" alt="%s" width="%d" height="%d" '
            'loading="lazy" decoding="async" class="%s">'
            % (relative(default["path"], self.depth), srcset, sizes_attr, esc(alt),
               default["width"], default["height"], classes)
        )
        if caption:
            return ('<figure class="my-2">%s<figcaption class="mt-2 font-body '
                    'text-[0.7rem] uppercase tracking-[0.12em] text-muted">%s</figcaption>'
                    '</figure>' % (img, esc(caption)))
        return img

    def override(self, slug):
        """A full-page HTML override, if one exists for this slug.

        Used for the legal pages, whose text is maintained by hand rather than
        assembled from blocks.
        """
        if slug not in self._overrides:
            path = os.path.join("content", slug + ".html")
            self._overrides[slug] = (
                open(path, encoding="utf-8").read() if os.path.exists(path) else None)
        return self._overrides[slug]

    def href(self, url):
        """Normalise a link from the export for use in the built page.

        Returns None when the value is not usable as a link at all: a few
        entries hold a bare handle or a stray character rather than a URL.
        """
        if not url:
            return None
        url = url.strip()
        if url.startswith(("http://", "https://", "mailto:", "tel:", "#", "skype:")):
            return url
        if url.startswith("/"):
            return relative(url, self.depth)
        if re.fullmatch(r"\+?[0-9 ()/-]{7,}", url):
            return "tel:" + re.sub(r"[^+0-9]", "", url)
        if "@" in url and "." in url.split("@")[-1]:
            return "mailto:" + url
        return None

    def contact_details(self):
        """Address and phone, read from the Impressum page at build time."""
        if self._contact is not None:
            return self._contact
        details = {}
        path = os.path.join(CONTENT, "pages", "imprint.json")
        if os.path.exists(path):
            page = load(path)
            raw = " ".join(b.get("html", "") for b in page["blocks"]
                           if b.get("type") == "raw-html")
            text = html.unescape(re.sub(r"<br\s*/?>", "\n", raw))
            text = re.sub(r"<[^>]+>", "\n", text)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            for i, line in enumerate(lines):
                if not line.lower().startswith("telefon"):
                    continue
                # The source markup puts the label and the number on separate
                # lines about as often as it puts them on one.
                value = line.split(":", 1)[1].strip() if ":" in line else ""
                if not value and i + 1 < len(lines):
                    value = lines[i + 1].strip()
                if re.search(r"\d", value):
                    details["phone"] = value
                break
            try:
                start = next(i for i, l in enumerate(lines) if "TMG" in l)
                block = [l for l in lines[start + 1:start + 6]
                         if not l.lower().startswith(("kontakt", "telefon", "e-mail"))]
                details["address"] = block[:4]
            except StopIteration:
                pass
        self._contact = details
        return details

    def document(self, src, label, slug):
        """Stage a non-image media file and link it as a download."""
        rel = src[len("assets/"):] if src.startswith("assets/") else src
        dest = os.path.join(OUT, "assets", "files", rel)
        source = os.path.join(CONTENT, "assets", rel)
        if os.path.exists(source):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(source, dest)
        elif not os.path.exists(dest):
            # Neither a source copy nor a previously staged one.
            self.warnings.append("%s: missing document %s" % (slug, src))
            return ""
        source = dest
        self.documents.append((slug, rel))
        kb = os.path.getsize(source) / 1000
        ext = os.path.splitext(rel)[1].lstrip(".").upper()
        return (
            '<a href="%s" class="my-6 flex items-center gap-4 border '
            'border-rule bg-surface p-5 no-underline hover:border-coral">'
            '<span class="font-display text-[1.6rem] leading-none text-coral">%s</span>'
            '<span><span class="block font-heading text-[1.05rem] text-ink">%s</span>'
            '<span class="block font-body text-[0.7rem] uppercase tracking-[0.14em] '
            'text-muted">Download &middot; %.0f KB</span></span></a>'
            % (esc(relative("/assets/files/" + rel.replace(os.sep, "/"), self.depth)),
               esc(ext), esc(label), kb)
        )

    def rewrite_richtext(self, markup):
        """Point inline <img> at the optimized variants."""
        def repl(m):
            src = m.group(1)
            key = src[len("assets/"):] if src.startswith("assets/") else None
            info = self.sizes["images"].get(key) if key else None
            if not info:
                return m.group(0)
            variants = info["variants"]
            return 'src="%s"' % relative(
                variants[min(1, len(variants) - 1)]["path"], self.depth)
        markup = re.sub(r'src="([^"]+)"', repl, markup)
        return re.sub(r'href="(/[^"]*)"',
                      lambda m: 'href="%s"' % relative(m.group(1), self.depth), markup)

    # -- blocks --------------------------------------------------------
    def block(self, b, slug, cols):
        kind = b.get("type")
        wide = cols == 1
        sizes_attr = "(max-width: 768px) 100vw, %dpx" % (960 if wide else 560)
        align = {"center": "text-center", "right": "text-right"}.get(b.get("align"), "")

        if kind == "heading":
            level = min(max(b.get("level", 2), 2), 6)
            scale = {2: "text-[1.9rem] sm:text-[2.15rem]", 3: "text-[1.4rem]",
                     4: "text-[1.15rem]"}.get(level, "text-[1.05rem]")
            inner = esc(b["text"])
            link = self.href(b.get("href"))
            if link:
                inner = '<a href="%s" class="no-underline hover:text-coral">%s</a>' % (
                    esc(link), inner)
            return '<h%d class="mt-10 mb-3 leading-tight %s %s">%s</h%d>' % (
                level, scale, align, inner, level)

        if kind == "richtext":
            return ('<div class="prose prose-neutral max-w-none %s">%s</div>'
                    % (align, self.rewrite_richtext(b["html"])))

        if kind == "image":
            if os.path.splitext(b["src"])[1].lower() not in IMAGE_EXTS:
                # A non-image file referenced where an image was expected;
                # offer it as a download instead of a broken image.
                return self.document(b["src"], b.get("title") or "Download", slug)
            img = self.picture(b["src"], b.get("alt") or b.get("title") or "",
                               "w-full rounded-card object-cover", sizes_attr,
                               b.get("caption"))
            if not img:
                return ""
            link = self.href(b.get("href"))
            if link:
                img = '<a href="%s" class="block no-underline">%s</a>' % (esc(link), img)
            wrap = {"center": "mx-auto", "right": "ml-auto"}.get(b.get("align"), "")
            return '<div class="my-6 %s">%s</div>' % (wrap, img)

        if kind == "button":
            return '<p class="my-6 %s"><a href="%s" class="btn">%s</a></p>' % (
                align, esc(self.href(b.get("href")) or "#"), esc(b["text"]))

        if kind == "icon-box":
            parts = ['<div class="my-4 border border-rule bg-surface p-6">']
            if b.get("title"):
                parts.append('<h3 class="mb-2 text-[1.2rem]">%s</h3>' % esc(b["title"]))
            if b.get("markdown"):
                parts.append('<div class="prose prose-neutral max-w-none text-[0.9rem]">%s</div>'
                             % md_to_html(b["markdown"]))
            parts.append("</div>")
            return "".join(parts)

        if kind == "image-box":
            parts = ['<div class="my-4 border border-rule bg-surface p-6">']
            if b.get("image"):
                parts.append(self.picture(b["image"]["src"], b.get("title") or "",
                                          "mb-4 w-full rounded-card object-cover", sizes_attr))
            if b.get("title"):
                parts.append('<h3 class="mb-2 text-[1.2rem]">%s</h3>' % esc(b["title"]))
            if b.get("markdown"):
                parts.append('<div class="prose prose-neutral max-w-none text-[0.9rem]">%s</div>'
                             % md_to_html(b["markdown"]))
            parts.append("</div>")
            return "".join(parts)

        if kind == "icon-list":
            items = "".join(
                '<li class="border-l-2 border-[var(--accent)] pl-5 text-[0.92rem] leading-[1.8]">%s</li>'
                % (('<a href="%s" class="no-underline hover:text-coral">%s</a>'
                    % (esc(self.href(i["href"])), esc(i["text"])))
                   if self.href(i.get("href")) else esc(i["text"]))
                for i in b["items"])
            return '<ul class="my-6 space-y-4 list-none pl-0">%s</ul>' % items

        if kind == "video":
            return self.video(b)

        if kind == "gallery" or kind == "carousel":
            cells = "".join(
                '<li>%s</li>' % self.picture(
                    i["src"], i.get("alt") or "",
                    "aspect-[4/3] w-full rounded-card object-cover", "(max-width: 640px) 50vw, 300px")
                for i in b["images"])
            columns = "sm:grid-cols-3" if len(b["images"]) > 4 else "sm:grid-cols-2"
            return ('<ul class="my-8 grid grid-cols-2 %s gap-3 list-none pl-0">%s</ul>'
                    % (columns, cells))

        if kind == "testimonial":
            avatar = ""
            if b.get("image"):
                avatar = self.picture(b["image"]["src"], "",
                                      "h-14 w-14 rounded-full object-cover", "56px")
            who = "".join(
                '<span class="block font-body text-[0.76rem] uppercase tracking-[0.14em] text-ink">%s</span>' % esc(b["name"])
                if b.get("name") else "") + (
                '<span class="block font-body text-[0.72rem] text-muted">%s</span>' % esc(b["role"])
                if b.get("role") else "")
            quote = ('<p class="font-heading text-[1.3rem] italic leading-[1.6]">%s</p>'
                     % md_to_html(b["markdown"], inline=True)) if b.get("markdown") else ""
            footer = ('<footer class="mt-5 flex items-center gap-4">%s<cite class="not-italic">%s</cite></footer>'
                      % (avatar, who)) if (avatar or who) else ""
            return ('<blockquote class="my-8 border-l-2 border-coral pl-6">%s%s</blockquote>'
                    % (quote, footer))

        if kind == "map":
            query = b["query"]
            return (
                '<div class="my-8 border border-rule bg-surface p-6">'
                '<p class="eyebrow">Location</p>'
                '<p class="mt-2 font-heading text-[1.3rem]">%s</p>'
                '<a href="https://www.google.com/maps/search/?api=1&amp;query=%s" '
                'target="_blank" rel="noopener" class="btn-ghost mt-4">View on Google Maps</a>'
                '</div>' % (esc(query), esc(query.replace(" ", "+")))
            )

        if kind == "social-icons":
            parts = []
            for i in b["items"]:
                network = (i.get("network") or "link").replace("-", " ")
                raw = (i.get("href") or "").strip()
                link = self.href(raw)
                if not link and network.startswith("skype") and raw:
                    link = "skype:%s?chat" % raw
                if not link:
                    self.warnings.append(
                        "%s: %s icon had %r in its URL field, not a link; dropped"
                        % (slug, network, raw))
                    continue
                external = link.startswith("http")
                parts.append(
                    '<a href="%s"%s class="btn-ghost">%s</a>'
                    % (esc(link),
                       ' target="_blank" rel="noopener"' if external else "",
                       esc(network)))
            if not parts:
                return ""
            return '<p class="my-6 flex flex-wrap gap-2">%s</p>' % "".join(parts)

        if kind == "form":
            return self.contact_card(b.get("form_id"), slug)

        if kind == "raw-html":
            override = self.override(slug)
            if override is not None:
                if slug in self._used_overrides:
                    return ""     # the override replaces all raw-html on the page
                self._used_overrides.add(slug)
                return ('<div class="prose prose-neutral max-w-none '
                        'prose-headings:font-heading text-[0.88rem]">%s</div>' % override)
            return ('<div class="prose prose-neutral max-w-none prose-headings:font-heading '
                    'text-[0.88rem]">%s</div>' % b["html"])

        if kind == "divider":
            return '<hr class="rule-accent my-10">'

        if kind == "anchor":
            return '<span id="%s" class="block scroll-mt-24"></span>' % esc(b["id"])

        if kind == "spacer":
            return '<div class="h-8"></div>'

        if kind == "icon":
            return ""

        self.warnings.append("%s: unrendered block type %r" % (slug, kind))
        return ""

    def video(self, b):
        if b.get("provider") == "hosted" and b.get("src"):
            return ""
        video_id = b.get("id")
        url = b.get("url") or ""
        if not video_id:
            # A channel link rather than a single video.
            return (
                '<div class="my-8 border border-rule bg-surface p-6">'
                '<p class="eyebrow">Video</p>'
                '<p class="mt-2 font-heading text-[1.3rem]">AgnesLink on YouTube</p>'
                '<a href="%s" target="_blank" rel="noopener" class="btn mt-4">Visit the channel</a>'
                '</div>' % esc(url)
            )
        poster = self.sizes["posters"].get(video_id)
        if not poster:
            return ('<p class="my-6"><a href="%s" target="_blank" rel="noopener" '
                    'class="btn">Watch on YouTube</a></p>' % esc(url))
        return (
            '<a href="%s" target="_blank" rel="noopener" '
            'class="play relative my-8 block overflow-hidden rounded-card no-underline shadow-lg">'
            '<img src="%s" alt="Play the video on YouTube" width="%d" height="%d" '
            'loading="lazy" decoding="async" class="w-full object-cover">'
            '<span class="sr-only">Opens YouTube in a new tab</span></a>'
            % (esc(url), relative(poster["path"], self.depth),
               poster["width"], poster["height"])
        )

    def contact_card(self, form_id, slug):
        email = ORDERS_EMAIL if slug in ORDERS_PAGES else GENERAL_EMAIL
        form = self.forms.get(str(form_id))
        note = ("Write to us directly &mdash; there is no form in between to break "
                "or be exploited.")
        mailto = "mailto:%s" % email
        if form and form["title"].lower().startswith("sponsor"):
            labels = [f["name"].replace("-", " ").replace("_", " ").title()
                      for f in form["fields"] if f["type"] not in ("hidden",)]
            body = "%0D%0A".join("%s:" % l for l in labels)
            mailto = "mailto:%s?subject=Sponsorship&body=%s" % (email, body)
            note = ("Send us these details and we will come back to you. "
                    "The old form collected exactly the same information.")
        extra = ""
        if slug == "contact":
            d = self.contact_details()
            rows = []
            if d.get("address"):
                rows.append('<div><p class="font-body text-[0.64rem] uppercase '
                            'tracking-[0.18em] text-muted">Post</p>'
                            '<address class="mt-1 not-italic text-[0.9rem] leading-[1.9]">%s'
                            '</address></div>'
                            % "<br>".join(esc(l) for l in d["address"]))
            if d.get("phone"):
                rows.append('<div><p class="font-body text-[0.64rem] uppercase '
                            'tracking-[0.18em] text-muted">Telephone</p>'
                            '<p class="mt-1 text-[0.9rem]"><a href="tel:%s" '
                            'class="no-underline text-ink hover:text-coral">%s</a></p></div>'
                            % (esc(d["phone"].replace(" ", "")), esc(d["phone"])))
            if rows:
                extra = ('<div class="mt-7 grid gap-6 border-t border-rule pt-6 sm:grid-cols-2">'
                         '%s</div>' % "".join(rows))

        return (
            '<div class="my-8 border border-rule bg-surface p-7">'
            '<p class="eyebrow">Get in touch</p>'
            '<p class="mt-3 max-w-[38rem] text-[0.9rem] leading-[1.85] text-muted">%s</p>'
            '<a href="%s" class="mt-5 inline-block border border-rule bg-paper px-6 py-4 no-underline">'
            '<span class="block font-body text-[0.64rem] uppercase tracking-[0.18em] text-muted">Email</span>'
            '<span class="mt-1 block font-heading text-[1.15rem] break-all text-coral">%s</span>'
            '</a>%s</div>' % (note, mailto, esc(email), extra)
        )

    # -- page assembly -------------------------------------------------
    def sections_html(self, page):
        by_section = defaultdict(lambda: defaultdict(list))
        for b in page["blocks"]:
            by_section[b.get("section", 0)][b.get("column", 0)].append(b)

        out = []
        for index, meta in enumerate(page["sections"]):
            columns = by_section.get(index)
            if not columns:
                continue
            column_keys = sorted(columns)
            rendered = []
            for col in column_keys:
                blocks = columns[col]
                body = "".join(self.block(b, page["slug"], len(column_keys)) for b in blocks)
                if body.strip():
                    rendered.append(body)
            if not rendered:
                continue

            if len(rendered) == 1:
                inner = '<div class="mx-auto max-w-3xl">%s</div>' % rendered[0]
            else:
                grid = {2: "md:grid-cols-2", 3: "md:grid-cols-3"}.get(
                    len(rendered), "md:grid-cols-2")
                inner = ('<div class="grid gap-8 %s items-start">%s</div>'
                         % (grid, "".join('<div>%s</div>' % r for r in rendered)))

            band = "bg-surface" if index % 2 else "bg-paper"
            out.append('<section class="%s"><div class="mx-auto max-w-5xl px-5 py-8 sm:px-8">'
                       '%s</div></section>' % (band, inner))
        return "\n".join(out)

    def nav_desktop(self, current):
        items = []
        for node in self.main_menu:
            branch = BRANCH_ROOTS.get(node.get("slug"), "general")
            active = node.get("slug") == current
            colour = {"ngo": "text-brand", "music": "text-plum"}.get(branch, "text-ink")
            aria = ' aria-current="page"' if active else ""
            link = ('<a href="%s"%s class="block py-3 no-underline %s hover:text-coral">%s</a>'
                    % (esc(relative(node["href"], self.depth)), aria,
                       colour if active else "text-ink", esc(node["label"])))
            sub = ""
            if node.get("children"):
                kids = "".join(
                    '<li><a href="%s"%s class="block px-4 py-1.5 font-body text-[0.73rem] '
                    'uppercase tracking-[0.1em] no-underline text-ink hover:text-coral">%s</a></li>'
                    % (esc(relative(c["href"], self.depth)),
                       ' aria-current="page"' if c.get("slug") == current else "",
                       esc(c["label"]))
                    for c in node["children"])
                sub = ('<ul class="invisible absolute left-0 top-full z-30 w-72 border '
                       'border-rule bg-surface py-2 opacity-0 shadow-xl transition '
                       'group-hover:visible group-hover:opacity-100 '
                       'group-focus-within:visible group-focus-within:opacity-100">%s</ul>' % kids)
            items.append('<li class="group relative" data-branch="%s">%s%s</li>'
                         % (branch, link, sub))
        return ('<nav aria-label="Main" class="hidden lg:block border-t border-rule">'
                '<ul class="flex flex-wrap items-stretch gap-x-7 gap-y-1 font-display '
                'uppercase text-[clamp(1.05rem,1.6vw,1.4rem)] tracking-[0.09em] list-none '
                'pl-0 m-0">%s</ul></nav>' % "".join(items))

    def nav_mobile(self, current):
        items = []
        for node in self.main_menu:
            branch = BRANCH_ROOTS.get(node.get("slug"), "general")
            colour = {"ngo": "text-brand", "music": "text-plum"}.get(branch, "text-ink")
            if node.get("children"):
                kids = "".join(
                    '<li><a href="%s"%s class="block py-1 no-underline text-muted">%s</a></li>'
                    % (esc(relative(c["href"], self.depth)),
                       ' aria-current="page"' if c.get("slug") == current else "",
                       esc(c["label"]))
                    for c in node["children"])
                items.append(
                    '<li><details><summary class="cursor-pointer list-none py-2 %s">%s</summary>'
                    '<ul class="list-none pl-4 pb-2 text-[0.72rem] normal-case tracking-normal">'
                    '%s</ul></details></li>' % (colour, esc(node["label"]), kids))
            else:
                items.append('<li><a href="%s"%s class="block py-2 no-underline %s">%s</a></li>'
                             % (esc(relative(node["href"], self.depth)),
                                ' aria-current="page"' if node.get("slug") == current else "",
                                colour, esc(node["label"])))
        return ('<details class="group lg:hidden border-t border-rule">'
                '<summary class="flex cursor-pointer list-none items-center justify-between '
                'py-3 font-display uppercase tracking-[0.12em] text-[1.35rem] text-ink">Menu'
                '<span class="text-coral transition group-open:rotate-45">+</span></summary>'
                '<ul class="list-none pl-0 pb-4 font-body text-[0.78rem] uppercase '
                'tracking-[0.1em]">%s</ul></details>' % "".join(items))

    def footer(self):
        links = "".join(
            '<li><a href="%s" class="no-underline text-muted hover:text-coral">%s</a></li>'
            % (esc(relative(n["href"], self.depth)), esc(n["label"]))
            for n in self.footer_menu)
        copyright_text = re.sub(r"\[year\]", str(date.today().year),
                                self.tokens["footer"].get("copyright") or "")
        return (
            '<footer class="border-t border-rule bg-paper">'
            '<div class="mx-auto max-w-5xl px-5 py-12 sm:px-8">'
            '<div class="flex flex-wrap items-start justify-between gap-8">'
            '<span class="font-display uppercase tracking-[0.06em] text-plum '
            'text-[clamp(2rem,6vw,2.6rem)] leading-none">Agnes Link</span>'
            '<nav aria-label="Footer"><ul class="flex flex-wrap gap-6 font-body '
            'text-[0.73rem] uppercase tracking-[0.14em] list-none pl-0 m-0">%s</ul></nav></div>'
            '<p class="mt-8 border-t border-rule pt-6 font-body text-[0.72rem] text-muted">%s</p>'
            '</div></footer>' % (links, esc(copyright_text))
        )

    def render(self, page):
        slug = page["slug"]
        branch = self.branch(slug)
        is_front = slug == self.front
        title = page["title"]
        seo = self.seo.get(slug, {})

        # A page may define its own full document title; when it does, the
        # site name is not appended.
        if seo.get("seo_title"):
            document_title = seo["seo_title"]
        elif is_front:
            document_title = "Agnes Link — Gospel singer & Agri-Connect Tanzania"
        else:
            document_title = "%s — Agnes Link" % title

        description = seo.get("seo_description") or DEFAULT_DESCRIPTION

        # The front page leads with the organisation's tagline rather than
        # its nav label.
        if is_front:
            head_band = (
                '<section class="border-b border-rule bg-surface" data-branch="general">'
                '<div class="mx-auto max-w-5xl px-5 py-14 sm:px-8">'
                '<h1 class="max-w-[22ch] text-[2.3rem] leading-[1.08] sm:text-[3.2rem]">%s</h1>'
                '<hr class="rule-accent mt-7"></div></section>' % esc(FRONT_HEADLINE)
            )
        else:
            head_band = (
                '<section class="border-b border-rule bg-surface" data-branch="%s">'
                '<div class="mx-auto max-w-5xl px-5 py-12 sm:px-8">'
                '<p class="eyebrow">%s</p>'
                '<h1 class="mt-3 text-[2.1rem] leading-[1.1] sm:text-[2.9rem]">%s</h1>'
                '<hr class="rule-accent mt-6"></div></section>'
                % (branch, esc(BRANCH_LABEL[branch]), esc(title))
            )

        return TEMPLATE.format(
            lang="en",
            document_title=esc(document_title),
            description=esc(description),
            branch=branch,
            nav_desktop=self.nav_desktop(slug),
            nav_mobile=self.nav_mobile(slug),
            head_band=head_band,
            body=self.sections_html(page),
            footer=self.footer(),
            home=relative("/", self.depth),
            root="../" * self.depth,
        )

    def build(self):
        os.makedirs(OUT, exist_ok=True)
        count = 0
        for entry in self.sitemap["pages"]:
            page = load(os.path.join(CONTENT, "pages", entry["slug"] + ".json"))
            is_front = entry["slug"] == self.front

            targets = [(os.path.join(OUT, entry["slug"], "index.html"), 1)]
            if is_front:
                # The front page is served at the root and keeps its own
                # /start/ URL, so old links survive. Each needs its own depth.
                targets.insert(0, (os.path.join(OUT, "index.html"), 0))

            for target, depth in targets:
                self.depth = depth
                self._used_overrides.discard(entry["slug"])
                markup = self.render(page)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(markup)
            count += 1
        self.depth = 0
        return count


TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{document_title}</title>
<meta name="description" content="{description}">
<link rel="icon" href="{root}favicon.ico" sizes="any">
<link rel="icon" type="image/png" href="{root}assets/icons/favicon-32.png" sizes="32x32">
<link rel="apple-touch-icon" href="{root}assets/icons/favicon-180.png">
<link rel="stylesheet" href="{root}assets/css/site.css">
</head>
<body class="antialiased" data-branch="{branch}">
<a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:bg-coral focus:px-4 focus:py-2 focus:text-white">Skip to content</a>
<header class="border-b border-rule bg-paper">
  <div class="mx-auto max-w-5xl px-5 sm:px-8">
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 pt-7 pb-4">
      <a href="{home}" class="block min-w-0 leading-[0.82] no-underline">
        <span class="block font-display uppercase tracking-[0.06em] text-plum text-[clamp(2.4rem,8.5vw,4.2rem)]">Agnes Link</span>
        <span class="mt-1 block font-body text-[0.6rem] uppercase tracking-[0.28em] text-muted">Kilimanjaro &middot; Tanzania</span>
      </a>
      <p class="hidden min-w-0 shrink lg:block max-w-[15rem] text-right font-body text-[0.7rem] leading-relaxed text-muted">Gospel singer, and founder of&nbsp;the youth organisation Agri-Connect&nbsp;Tanzania</p>
    </div>
{nav_desktop}
{nav_mobile}
  </div>
</header>
<main id="main">
{head_band}
{body}
</main>
{footer}
</body>
</html>
"""


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def pick_menu(menus, prefix):
    for name, nodes in menus.items():
        if name.lower().startswith(prefix):
            return nodes
    return []


def walk_slugs(node):
    if node.get("slug"):
        yield node["slug"]
    for child in node.get("children", []):
        yield from walk_slugs(child)


def md_to_html(text, inline=False):
    """The short Markdown strings stored on card widgets."""
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', text)
    if inline:
        return text.replace("\n\n", "<br><br>")
    return "".join("<p>%s</p>" % p for p in text.split("\n\n") if p.strip())


def main():
    builder = Builder()
    count = builder.build()
    print("built %d pages -> %s/" % (count, OUT))
    for slug in sorted(builder._overrides):
        if builder._overrides[slug] is not None:
            print("  using reviewed text from content/%s.html" % slug)
    for slug, rel in builder.documents:
        print("  staged document on /%s/: %s" % (slug, rel))
    for w in dict.fromkeys(builder.warnings):
        print("  !! %s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
