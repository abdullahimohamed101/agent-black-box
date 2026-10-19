#!/usr/bin/env python3
"""Claims check for the landing page export (Phase 20, brief section 9 `claims`; positioning brief sections 4 and 8).

usage: check_site_claims.py BUILT_DIR

Reads the visible text (and alt text) of index.html and fails on:
  * the positioning brief's banned words, "SaaS", "SOC", "stars", "pip install", "trusted by", exclamation marks, "<digits> tests";
  * a missing qualifier: "tested offline" beside LangGraph, "ask through the SDK guard" beside Policies;
  * the footer status line missing, or "--live" anywhere outside it;
  * claims that wait for publication (G2) in an export built with the release-claims flag OFF (the page says which in
    <meta name="abb-release-claims">): "open source", "self-hosted", "Source on GitHub", any link to the repository, the caption
    "Open source. Self-hosted.".
Stdlib only.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

BANNED = [
    "seamless", "powerful", "effortless", "intuitive", "enterprise-grade", "production-ready", "production ready", "battle-tested",
    "blazing", "revolutionary", "game-changing", "next-generation", "ai-powered", "intelligent", "smart", "magic", "supercharge", "10x",
    "complete visibility", "full observability", "end-to-end", "guardrails", "the only", "the best", "real-time", "saas", "soc 2", "soc2",
    "stars", "pip install", "trusted by", "oops", "please", "unlock", "leverage", "cutting-edge", "world-class",
]
GATED = ["open source", "open-source", "self-hosted", "source on github", "open source. self-hosted."]
STATUS = (
    "Early release. Single-node Docker Compose. No hosting guide yet (TLS, domain, real OIDC are documented as not covered). "
    "Real-model mode exists in two examples and has not been run by the project."
)


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.chunks: list[str] = []
        self.hrefs: list[str] = []
        self.meta: dict[str, str] = {}
        self.in_footer = 0
        self.footer: list[str] = []

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        a = dict(attrs)
        if tag in ("script", "style", "template"):
            self.skip += 1
        if tag == "footer":
            self.in_footer += 1
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"] or "")
        if tag == "meta" and a.get("name"):
            self.meta[a["name"] or ""] = a.get("content") or ""
        for k in ("alt", "aria-label", "title"):
            if a.get(k):
                self.chunks.append(a[k] or "")
                if self.in_footer:
                    self.footer.append(a[k] or "")

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in ("script", "style", "template") and self.skip:
            self.skip -= 1
        if tag == "footer" and self.in_footer:
            self.in_footer -= 1

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if not self.skip and data.strip():
            self.chunks.append(data)
            if self.in_footer:
                self.footer.append(data)


def main(argv: list[str]) -> int:
    site = Path(argv[0] if argv else "site/out").resolve()
    p = Text()
    p.feed((site / "index.html").read_text(encoding="utf-8"))
    text = re.sub(r"\s+", " ", " ".join(p.chunks))
    low = text.lower()
    problems: list[str] = []

    for w in BANNED:
        pattern = r"(?<![a-z0-9-])" + re.escape(w) + r"(?![a-z0-9-])"
        if re.search(pattern, low):
            problems.append(f"banned word or phrase: {w!r}")
    if "!" in text:
        problems.append("an exclamation mark in the copy")
    if re.search(r"\d[\d,]*\s+(unit |e2e )?tests\b", low):
        problems.append("a test count in the copy (D14)")
    if re.search(r"\b\d+\s*(ms|x)\s+(faster|overhead)", low):
        problems.append("an unmeasured performance number")

    for m in re.finditer(r"langgraph", low):
        window = low[max(0, m.start() - 160): m.end() + 200]
        if "tested offline" not in window:
            problems.append("LangGraph is not followed by the qualifier 'tested offline'")
            break
    for m in re.finditer(r"policies", low):
        window = low[m.start(): m.end() + 120]
        if "ask through the sdk guard" in window:
            break
    else:
        if "policies" in low:
            problems.append("'Policies' without 'ask through the SDK guard'")
        elif "approval" in low:
            problems.append("approvals are shown without the qualifier 'Policies apply to agents that ask through the SDK guard'")

    footer = re.sub(r"\s+", " ", " ".join(p.footer))
    if STATUS not in footer:
        problems.append("the footer status line is missing or altered")
    body_without_status = text.replace(STATUS, "")
    if "--live" in body_without_status or re.search(r"\blive mode\b|\breal-model\b|\breal model\b", body_without_status.lower()):
        problems.append("real-model mode is mentioned outside the footer status line")

    flag = p.meta.get("abb-release-claims")
    if flag not in ("0", "1"):
        problems.append("missing <meta name='abb-release-claims'> (the build flag state)")
    if flag == "0":
        for g in GATED:
            if g in low:
                problems.append(f"gated claim {g!r} in an export built with the release-claims flag off")
        repo_links = [h for h in p.hrefs if "github.com" in h]
        if repo_links:
            problems.append(f"links into the repository with the flag off: {repo_links[:3]}")

    for pr in problems:
        print(f"FAIL: {pr}")
    if problems:
        return 1
    print(f"claims check passed ({len(text.split())} words; release-claims flag {'ON' if flag == '1' else 'OFF'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
