#!/usr/bin/env python3
"""Drive a headless check for horizontal overflow at every breakpoint.

Emits the JS payload used against the running preview; kept as a file so the
same probe can be re-run against site/ after the full build rather than being
retyped each time.

The only reliable way to test this is a real viewport resize -- constraining
`documentElement.style.width` does not reflow the way a resize does and reports
phantom overflow.
"""

WIDTHS = [320, 375, 414, 640, 768, 1024, 1280, 1536]

PROBE = """(() => {
  const w = window.innerWidth;
  const offenders = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (r.right > w + 1 && r.width > 0 && r.height > 0) {
      const cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.visibility === 'hidden') continue;
      offenders.push(el.tagName.toLowerCase() + '.' +
        String(el.className).split(' ').slice(0, 2).join('.') +
        ' [w=' + Math.round(r.width) + ' right=' + Math.round(r.right) + ']');
    }
  }
  const nav = document.querySelector('nav[aria-label="Main"]');
  const menu = document.querySelector('header details');
  const shown = el => el && el.getBoundingClientRect().height > 0;
  return JSON.stringify({
    width: w,
    overflow: document.documentElement.scrollWidth - w,
    desktopNav: shown(nav),
    mobileMenu: shown(menu),
    offenders: offenders.slice(0, 4)
  });
})()"""

if __name__ == "__main__":
    print("widths:", WIDTHS)
    print()
    print(PROBE)
