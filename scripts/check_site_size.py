#!/usr/bin/env python3
"""Transfer-size budgets for the landing page export (brief section 9 `size`). Stdlib only.

usage: check_site_size.py BUILT_DIR

Sums what a modern browser downloads for the page, gzip -9 as GitHub Pages serves it (brotli is smaller): the scripts index.html
loads (not the `noModule` polyfill chunk, which only legacy browsers fetch; it is printed, not counted), the stylesheets, the
font files, the favicon. Fails over:
  JavaScript total 150 KB (framework 110 + islands 25 + runtime 15, printed), CSS 25 KB, fonts 3 files and 70 KB,
  initial files (HTML + CSS + JS + fonts + favicon) 400 KB, each WebP 120 KB, each PNG 400 KB, social card 200 KB.
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

KB = 1024


def gz(p: Path) -> int:
    return len(gzip.compress(p.read_bytes(), 9))


def main(argv: list[str]) -> int:
    site = Path(argv[0] if argv else "site/out").resolve()
    html = (site / "index.html").read_text(encoding="utf-8")
    problems: list[str] = []

    def local(url: str) -> Path | None:
        m = re.search(r"/_next/.*|/assets/.*|/fonts/.*", url)
        return (site / m.group(0).lstrip("/")) if m else None

    scripts: list[tuple[Path, bool]] = []
    for tag in re.findall(r"<script\b[^>]*>", html):
        src = re.search(r'src="([^"]+)"', tag)
        if src and (p := local(src.group(1))) and p.exists():
            scripts.append((p, "noModule" in tag or "nomodule" in tag))
    modern = {p for p, legacy in scripts if not legacy}
    # chunks preloaded or named in the bootstrap payload are loaded too
    for m in re.findall(r"static/chunks/[A-Za-z0-9_.-]+\.js", html):
        p = site / "_next" / m
        legacy = any(p == q and l for q, l in scripts)
        if p.exists() and not legacy:
            modern.add(p)
    legacy_total = sum(gz(p) for p, l in scripts if l)
    js = sum(gz(p) for p in modern)
    css_files = sorted(site.glob("_next/static/**/*.css"))
    css = sum(gz(p) for p in css_files)
    fonts = sorted(site.glob("_next/static/media/*.woff2"))
    font_total = sum(p.stat().st_size for p in fonts)
    icon = site / "assets/favicon.svg"
    initial = len(gzip.compress(html.encode(), 9)) + js + css + font_total + (gz(icon) if icon.exists() else 0)

    print(f"JavaScript   {js / KB:7.1f} KB gzip in {len(modern)} files (limit 150)   [legacy-only polyfill chunk, not loaded by modern browsers: {legacy_total / KB:.1f} KB]")
    biggest = sorted(((gz(p), p.name) for p in modern), reverse=True)[:3]
    print("             largest: " + ", ".join(f"{n} {b / KB:.1f} KB" for b, n in biggest))
    print(f"CSS          {css / KB:7.1f} KB gzip in {len(css_files)} file(s) (limit 25)")
    print(f"Fonts        {font_total / KB:7.1f} KB in {len(fonts)} files (limit 70, three files)")
    print(f"Initial view {initial / KB:7.1f} KB (HTML + CSS + JS + fonts + favicon; limit 400)")
    if js > 150 * KB:
        problems.append(f"JavaScript is {js / KB:.1f} KB gzip, over 150 KB")
    if css > 25 * KB:
        problems.append(f"CSS is {css / KB:.1f} KB gzip, over 25 KB")
    if len(fonts) != 3 or font_total > 70 * KB:
        problems.append(f"fonts: {len(fonts)} files, {font_total / KB:.1f} KB (three files, 70 KB)")
    if initial > 400 * KB:
        problems.append(f"the initial view is {initial / KB:.1f} KB, over 400 KB")

    worst_webp = worst_png = 0
    for p in sorted((site / "assets").rglob("*")):
        if p.suffix == ".webp" and "video" not in p.parts:
            worst_webp = max(worst_webp, p.stat().st_size)
            if p.stat().st_size > 120 * KB:
                problems.append(f"{p.relative_to(site)} is {p.stat().st_size / KB:.0f} KB (WebP limit 120)")
        elif p.suffix == ".png":
            limit = 200 if p.name == "social-card.png" else 400
            worst_png = max(worst_png, p.stat().st_size)
            if p.stat().st_size > limit * KB:
                problems.append(f"{p.relative_to(site)} is {p.stat().st_size / KB:.0f} KB (PNG limit {limit})")
    print(f"Images       largest WebP {worst_webp / KB:.0f} KB (limit 120), largest PNG {worst_png / KB:.0f} KB (limit 400)")
    for pr in problems:
        print(f"FAIL: {pr}")
    if problems:
        return 1
    print("size check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
