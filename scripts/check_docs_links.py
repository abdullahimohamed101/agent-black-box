#!/usr/bin/env python3
"""Fail when a relative link or anchor in the Markdown docs does not resolve (Phase 20, D14).

usage: check_docs_links.py [--external] [PATH ...]
       (default PATH list: README.md docs site CONTRIBUTING.md; a missing path is skipped)

Checked: inline links `[text](target)`, images, and reference definitions `[id]: target`, outside fenced
code blocks and inline code spans.
  * `path`, `path#anchor`, `#anchor`: the file or directory must exist (relative to the linking file, or to
    the repository root for a leading `/`), and the anchor must match a heading of the target Markdown
    file (GitHub's slug rules, with `-1`, `-2` suffixes for repeated headings) or an explicit `id=`/`name=`.
  * `http(s)://`, `mailto:` and other schemes are NOT fetched (CI must not depend on the network).
    `--external` fetches `http(s)` links with a HEAD request (10 s timeout, bounded) and reports failures;
    it is for a maintainer's occasional run, never for CI.
Stdlib only. Exit 0 when nothing is broken, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = ["README.md", "docs", "site", "CONTRIBUTING.md"]

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE = re.compile(r"(`+)(?:.+?)\1")
INLINE_LINK = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REF_DEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
HTML_ID = re.compile(r"""\b(?:id|name)\s*=\s*["']([^"']+)["']""")


def slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, drop markup and punctuation, spaces to hyphens."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # links keep their text
    text = re.sub(r"[`*~]", "", text).strip().lower()
    out = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    return "".join(out)


def strip_code(text: str, *, keep_inline: bool = False) -> list[tuple[int, str]]:
    """(line number, line) for every line outside a fenced block; inline code spans blanked unless kept."""
    result: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if fence is None and match:
            fence = match.group(1)[0] * 3
            continue
        if fence is not None:
            if match and match.group(1).startswith(fence):
                fence = None
            continue
        if keep_inline:
            result.append((number, line))
        else:
            result.append((number, INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)))
    return result


_anchor_cache: dict[Path, set[str]] = {}


def anchors(path: Path) -> set[str]:
    cached = _anchor_cache.get(path)
    if cached is not None:
        return cached
    found: set[str] = set()
    seen: dict[str, int] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for _, line in strip_code(text, keep_inline=True):
        heading = HEADING.match(line)
        if heading:
            base = slug(heading.group(2))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if count == 0 else f"{base}-{count}")
        found.update(HTML_ID.findall(line))
    _anchor_cache[path] = found
    return found


def targets(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    result: list[tuple[int, str]] = []
    for number, line in strip_code(text):
        for match in INLINE_LINK.finditer(line):
            result.append((number, match.group(1)))
        ref = REF_DEF.match(line)
        if ref:
            result.append((number, ref.group(1)))
    return [(n, t[1:-1] if t.startswith("<") and t.endswith(">") else t) for n, t in result]


def check_file(path: Path, external: list[tuple[Path, int, str]]) -> list[str]:
    problems: list[str] = []
    for number, target in targets(path):
        if not target:
            continue
        parts = urlsplit(target)
        where = f"{path.relative_to(ROOT)}:{number}"
        if parts.scheme in ("http", "https"):
            external.append((path, number, target))
            continue
        if parts.scheme or target.startswith("//"):
            continue  # mailto:, tel:, ...
        raw_path = unquote(parts.path)
        if raw_path == "":
            dest = path
        elif raw_path.startswith("/"):
            dest = ROOT / raw_path.lstrip("/")
        else:
            dest = (path.parent / raw_path)
        try:
            dest = dest.resolve()
            dest.relative_to(ROOT)
        except (ValueError, OSError):
            problems.append(f"{where}: link leaves the repository: {target}")
            continue
        if not dest.exists():
            problems.append(f"{where}: no such file: {target}")
            continue
        if parts.fragment:
            fragment = unquote(parts.fragment).lower()
            if dest.is_dir() or dest.suffix.lower() not in (".md", ".markdown"):
                continue  # anchors into code or HTML are not verifiable here
            if fragment not in {a.lower() for a in anchors(dest)}:
                problems.append(f"{where}: no heading '#{parts.fragment}' in {dest.relative_to(ROOT)}")
    return problems


def check_external(items: list[tuple[Path, int, str]]) -> list[str]:
    problems: list[str] = []
    done: dict[str, str | None] = {}
    for path, number, url in items:
        if url not in done:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "abb-docs-links"})
            try:
                with urllib.request.urlopen(request, timeout=10):
                    done[url] = None
            except urllib.error.HTTPError as exc:
                done[url] = None if exc.code in (401, 403, 405, 429) else f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                done[url] = f"{type(exc).__name__}"
        if done[url]:
            problems.append(f"{path.relative_to(ROOT)}:{number}: {url}: {done[url]}")
    return problems


def markdown_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for name in paths:
        base = (ROOT / name) if not Path(name).is_absolute() else Path(name)
        if base.is_dir():
            files.extend(sorted(p for p in base.rglob("*.md") if "node_modules" not in p.parts))
        elif base.is_file():
            files.append(base)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=DEFAULT_PATHS)
    parser.add_argument("--external", action="store_true", help="also HEAD-check http(s) links (never in CI)")
    args = parser.parse_args(argv)
    external: list[tuple[Path, int, str]] = []
    problems: list[str] = []
    files = markdown_files(args.paths)
    for path in files:
        problems.extend(check_file(path, external))
    if args.external:
        problems.extend(check_external(external))
    for problem in problems:
        print(problem)
    print(f"checked {len(files)} Markdown files: {len(problems)} broken link(s)"
          f"{'' if args.external else f' ({len(external)} external links not fetched)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
