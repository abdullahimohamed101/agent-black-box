#!/usr/bin/env python3
"""Static checks for the landing page in site/ (Phase 20, D12, D16). Stdlib only.

usage: check_site.py BUILT_DIR [--repo-url URL] [--budget-kb N] [--release]

Stack-agnostic: BUILT_DIR is any static output directory with an index.html (plain HTML, a Next.js export, ...).

Fails (exit 1) on:
  * HTML that is not well formed (unclosed or mismatched tags, duplicate ids);
  * <iframe>, <object>, <embed> (scripts are allowed when they are same-site relative files);
  * a resource reference (src, srcset, poster, <link href>, CSS url(), @import) that is not a relative path;
  * an <a href> that leaves the site for anywhere other than the repository URL;
  * a root-absolute path (starts with "/"), which breaks under a GitHub Pages project sub-path;
  * a relative link or asset that does not resolve to a file under the site directory, or a #fragment with no id;
  * a total size over --budget-kb (default 250, fonts excluded; pass 0 to disable).
`[CONFIRM]` markers are listed as warnings; with --release they fail.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
FORBIDDEN_TAGS = {"iframe", "object", "embed", "frame", "frameset"}
RESOURCE_ATTRS = {"src", "poster", "data", "srcset", "action", "formaction", "background"}
BUDGET = 250 * 1024
URL_RE = re.compile(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)\s]*))\s*\)")
IMPORT_RE = re.compile(r"@import\b")


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.ids: dict[str, int] = {}
        self.refs: list[tuple[int, str, str, str]] = []  # line, tag, attr, value
        self.tags: list[tuple[int, str]] = []

    def err(self, msg: str) -> None:
        self.errors.append(f"line {self.getpos()[0]}: {msg}")

    def handle_starttag(self, tag, attrs, selfclosing=False):  # type: ignore[no-untyped-def]
        line = self.getpos()[0]
        self.tags.append((line, tag))
        names = [n for n, _ in attrs]
        if len(set(names)) != len(names):
            self.err(f"<{tag}> repeats an attribute")
        for name, value in attrs:
            value = value or ""
            if name == "id":
                if value in self.ids:
                    self.err(f"duplicate id {value!r}")
                self.ids[value] = line
            elif name in RESOURCE_ATTRS:
                self.refs.append((line, tag, name, value))
            elif name == "href":
                self.refs.append((line, tag, "href", value))
        if tag not in VOID and not selfclosing:
            self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):  # type: ignore[no-untyped-def]
        self.handle_starttag(tag, attrs, selfclosing=True)

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in VOID:
            self.err(f"</{tag}> closes a void element")
            return
        if not self.stack or self.stack[-1][0] != tag:
            top = self.stack[-1] if self.stack else None
            self.err(f"</{tag}> does not match open {top[0] + ' from line ' + str(top[1]) if top else 'nothing'}")
            # recover: pop to the matching tag if there is one
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break
            return
        self.stack.pop()


def is_external(value: str) -> bool:
    parts = urlsplit(value)
    return bool(parts.scheme) or value.startswith("//")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    release = "--release" in argv
    repo_url = ""
    if "--repo-url" in argv:
        repo_url = argv[argv.index("--repo-url") + 1].rstrip("/")
        args = [a for a in args if a != repo_url]
    budget = BUDGET
    if "--budget-kb" in argv:
        val = argv[argv.index("--budget-kb") + 1]
        budget = int(val) * 1024
        args = [a for a in args if a != val]
    site = Path(args[0] if args else Path(__file__).resolve().parents[1] / "site").resolve()
    problems: list[str] = []
    warnings: list[str] = []

    index = site / "index.html"
    if not index.is_file():
        print(f"missing {index}")
        return 1
    text = index.read_text(encoding="utf-8")
    page = Page()
    page.feed(text)
    page.close()
    problems += [f"index.html {e}" for e in page.errors]
    problems += [f"index.html line {s[1]}: <{s[0]}> is never closed" for s in page.stack]
    for line, tag in page.tags:
        if tag in FORBIDDEN_TAGS:
            problems.append(f"index.html line {line}: <{tag}> is not allowed")

    def check_ref(origin: str, line: int, where: str, value: str, base: Path, anchor_ok: bool) -> None:
        value = value.strip()
        if not value:
            return
        if where == "srcset":
            for candidate in value.split(","):
                check_ref(origin, line, "src", candidate.strip().split()[0], base, False)
            return
        if value.startswith("#"):
            if anchor_ok and value[1:] and value[1:] not in page.ids:
                problems.append(f"{origin} line {line}: #{value[1:]} has no matching id")
            return
        if is_external(value):
            if where == "a-href" and repo_url and (value == repo_url or value.startswith(repo_url + "/") or value.startswith(repo_url + ".")):
                return
            problems.append(f"{origin} line {line}: off-site reference {value!r}")
            return
        if value.startswith("/"):
            problems.append(f"{origin} line {line}: root-absolute path {value!r} breaks under a project sub-path")
            return
        parts = urlsplit(value)
        target = (base / unquote(parts.path)).resolve() if parts.path else base
        try:
            target.relative_to(site)
        except ValueError:
            problems.append(f"{origin} line {line}: {value!r} leaves the site directory")
            return
        if parts.path in ("", "./") and not parts.fragment:
            return
        if parts.path.endswith("/") or parts.path in ("", "."):
            target = target / "index.html"
        if not target.exists():
            problems.append(f"{origin} line {line}: {value!r} does not resolve to a file")
        elif parts.fragment and anchor_ok and target == index and parts.fragment not in page.ids:
            problems.append(f"{origin} line {line}: #{parts.fragment} has no matching id")

    for line, tag, attr, value in page.refs:
        where = "a-href" if (attr == "href" and tag == "a") else attr
        if tag == "link" and attr == "href" and value.startswith("#"):
            continue
        if attr == "href" and tag not in ("a", "link", "area"):
            continue
        check_ref("index.html", line, where, value, index.parent, True)

    css_files = sorted(site.rglob("*.css"))
    if not css_files:
        problems.append("no stylesheet found")
    for css in css_files:
        body = css.read_text(encoding="utf-8")
        if IMPORT_RE.search(body):
            problems.append(f"{css.name}: @import is not allowed")
        for m in URL_RE.finditer(body):
            ref = next(g for g in m.groups() if g is not None)
            line = body.count("\n", 0, m.start()) + 1
            check_ref(css.name, line, "css-url", ref, css.parent, False)

    # Size budget over everything except fonts.
    total = sum(p.stat().st_size for p in site.rglob("*") if p.is_file() and "fonts" not in p.relative_to(site).parts)
    if budget and total > budget:
        problems.append(f"site is {total} bytes excluding fonts; the budget is {budget}")

    for p in [index, *css_files]:
        for n, content in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "[CONFIRM]" in content:
                warnings.append(f"{p.name} line {n}: {content.strip()[:110]}")

    for w in warnings:
        print(f"warning: [CONFIRM] {w}")
    if release and warnings:
        problems.append(f"{len(warnings)} [CONFIRM] marker(s) left (--release)")
    for pr in problems:
        print(f"FAIL: {pr}")
    if problems:
        return 1
    print(f"static checks passed: {len(page.tags)} tags, {len(page.refs)} references, {total} bytes excluding fonts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
