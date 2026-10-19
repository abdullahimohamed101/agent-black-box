#!/usr/bin/env python3
"""Static release checks (Phase 20, D13). Driven by scripts/release-checklist.sh.

usage: release_checklist.py [--root DIR] [--release] CHECK [CHECK ...]
       CHECK: placeholders debug secrets charts routes network shots claims links | all

Every check reads git-tracked files (every file when ROOT is not a git checkout) and prints `ok`, `warn` or
`FAIL` lines. The secrets check prints a file, a line and a kind, never the matched text, so the checklist
cannot leak a secret into CI logs. Reviewed exceptions live in scripts/release-checklist-allow.txt as
`check | path-glob | needle | reason` (needle: a substring of the offending line, `*` for any line); an entry
that no longer matches anything is itself a failure, so the list cannot rot.

`--release` turns the gated markers ([CONFIRM ...], CONFIRM AFTER ..., [RELEASE_CLAIM ...]) from warnings
into failures. Stdlib only.

Why static instead of a browser crawl for routes and charts: the routes are file-system routes and every
link is built from `lib/routes.ts` or a literal path, so matching them against the page files catches a
broken link without a build, a database or a browser, in seconds, on every `quality.sh quick`. Rendering is
covered by the Playwright specs and `scripts/check-site.sh` (requests); the crawl against the demo-seeded
stack is not scripted here (see docs/KNOWN_ISSUES.md).
"""

from __future__ import annotations

import fnmatch
import html
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "out", "__pycache__", ".mypy_cache", ".ruff_cache"}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".pdf", ".docx",
    ".zip", ".gz", ".mp4", ".webm", ".mov",
}  # fmt: skip
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|e2e|fixtures)/|\.(test|spec)\.[jt]sx?$|(^|/)test_[^/]*\.py$|(^|/)conftest\.py$")


@dataclass(frozen=True)
class Hit:
    check: str
    path: str
    line: int
    detail: str
    gated: bool = False
    value: str = ""  # the matched secret-shaped text; never printed, only judged by `~synthetic` allow-list entries

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}"


class Tree:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._files: list[str] | None = None
        self._text: dict[str, list[str]] = {}

    def files(self) -> list[str]:
        if self._files is None:
            if (self.root / ".git").exists():
                out = subprocess.run(  # noqa: S603
                    ["git", "-C", str(self.root), "ls-files", "-z"],  # noqa: S607
                    capture_output=True, check=True, timeout=30,
                ).stdout.decode()  # fmt: skip
                names = [n for n in out.split("\0") if n and (self.root / n).is_file()]
            else:
                names = []
                for base, dirs, files in os.walk(self.root):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                    names += [Path(base, f).relative_to(self.root).as_posix() for f in files]
            self._files = sorted(names)
        return self._files

    def lines(self, rel: str) -> list[str]:
        if rel not in self._text:
            try:
                self._text[rel] = (self.root / rel).read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                self._text[rel] = []
        return self._text[rel]

    def select(self, *globs: str, exclude: tuple[str, ...] = (), tests: bool = False) -> list[str]:
        """Tracked text files matching any glob (fnmatch on the repo-relative path; `*` crosses `/`)."""
        found = []
        for rel in self.files():
            if Path(rel).suffix.lower() in BINARY_SUFFIXES:
                continue
            if not any(fnmatch.fnmatch(rel, g) for g in globs):
                continue
            if any(fnmatch.fnmatch(rel, e) for e in exclude):
                continue
            if not tests and TEST_PATH.search(rel):
                continue
            found.append(rel)
        return found


@dataclass
class Allow:
    check: str
    glob: str
    needle: str
    reason: str
    used: bool = False


def load_allow(root: Path) -> list[Allow]:
    path = root / "scripts" / "release-checklist-allow.txt"
    entries: list[Allow] = []
    if not path.exists():
        return entries
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) != 4 or not all(parts):
            raise SystemExit(f"{path}:{number}: expected 'check | path-glob | needle | reason'")
        entries.append(Allow(*parts))
    return entries


SYNTHETIC_NEEDLE = "~synthetic"
SYNTHETIC_MARKERS = re.compile(
    r"example|fake|dummy|sample|xxxx|aaaa|0000|1234|abc|redact|secret|s3cr3t|placeholder|not[-_]?a[-_]?key|bogus|canary|invalid|mock"
    r"|sentinel|hunter2|planted|leak|demo|tokenvalue|body|[{}$<>%]",
    re.I,
)


def looks_synthetic(value: str) -> bool:
    """A marker word, a template placeholder, or a value too repetitive to be a real credential."""
    return bool(SYNTHETIC_MARKERS.search(value)) or len(set(value)) <= 8 or re.search(r"(.)\1{5,}", value) is not None


def allowed(entries: list[Allow], hit: Hit, text: str) -> bool:
    for e in entries:
        if e.check != hit.check or not fnmatch.fnmatch(hit.path, e.glob):
            continue
        if e.needle == SYNTHETIC_NEEDLE:
            # A planted fake, not a leak: only a matched value that announces itself as synthetic is exempt, so a real key
            # pasted into the same file is still reported.
            if hit.value and looks_synthetic(hit.value):
                e.used = True
                return True
        elif e.needle == "*" or e.needle in text:
            e.used = True
            return True
    return False


# --- placeholders -------------------------------------------------------------------------------------------

PLACEHOLDER_SHIPPED = (
    "README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "NOTICE", "docs/*.md", "site/README.md",
    "site/src/*", "site/public/*", "apps/web/src/*", "apps/web/public/*", ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE.md",
)  # fmt: skip
# Internal tracking documents are not shipped copy: they legitimately discuss markers and TODOs.
PLACEHOLDER_EXCLUDE = (
    "docs/PROJECT_STATE.md", "docs/IMPLEMENTATION_PLAN.md", "docs/KNOWN_ISSUES.md", "docs/BUILD_PROMPT.md",
    "docs/DECISIONS.md", "docs/TESTING.md", "docs/OPERATIONS.md", "docs/RELIABILITY.md",
    "docs/plans/*", "docs/design/*", "docs/research/*",
)  # fmt: skip
PLACEHOLDER_RE = re.compile(
    r"\blorem ipsum\b|\bTODO\b|\bFIXME\b|\[NEEDS INPUT|\bTBD\b|placeholder text|\bchangeme\b|\byour[-_ ]name[-_ ]here\b|\bXXX\b",
    re.I,
)
# `confirm("AFTER ...")` is the site's source form of the [CONFIRM ...] marker (site/src/lib/confirm.ts): without it
# the checklist would pass while the Pages export, which renders the marker, fails.
GATED_RE = re.compile(r"\[CONFIRM\b|\bCONFIRM AFTER\b|\bconfirm\(\s*[\"']AFTER\b|\[RELEASE_CLAIM\b|\bRELEASE_CLAIM:")


def check_placeholders(tree: Tree) -> list[Hit]:
    hits = []
    for rel in tree.select(*PLACEHOLDER_SHIPPED, exclude=PLACEHOLDER_EXCLUDE):
        for number, line in enumerate(tree.lines(rel), 1):
            if GATED_RE.search(line):
                hits.append(Hit("placeholders", rel, number, "gated marker: " + _kind(line, GATED_RE), gated=True))
            if PLACEHOLDER_RE.search(line):
                hits.append(Hit("placeholders", rel, number, "placeholder text: " + _kind(line, PLACEHOLDER_RE)))
    return hits


def _kind(line: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(line)
    return match.group(0) if match else "?"


# --- debug logging ------------------------------------------------------------------------------------------

PY_SRC = (
    "apps/api/src/*.py", "packages/sdk-python/src/*.py", "packages/event-schema/src/*.py",
    "integrations/*/src/*.py",
)  # fmt: skip
WEB_SRC = ("apps/web/src/*.ts", "apps/web/src/*.tsx", "site/src/*.ts", "site/src/*.tsx")
PY_DEBUG = re.compile(r"(?<![\w.])print\(|\bbreakpoint\(\)|\bpdb\.set_trace\(|\bimport (?:i?pdb|pudb)\b")
PY_LOG_PAYLOAD = re.compile(r"\blog(?:ger)?\.debug\(.*\b(payload|body|content|prompt|messages?|attributes|headers)\b", re.I)
JS_DEBUG = re.compile(r"\bconsole\.(?:log|debug|trace)\(|(?<![\w\"'.])debugger\b\s*;?\s*$")


def check_debug(tree: Tree) -> list[Hit]:
    hits = []
    for rel in tree.select(*PY_SRC):
        for number, line in enumerate(tree.lines(rel), 1):
            code = line.split("#", 1)[0]
            if PY_DEBUG.search(code):
                hits.append(Hit("debug", rel, number, "debug output (print/breakpoint/pdb)"))
            elif PY_LOG_PAYLOAD.search(code):
                hits.append(Hit("debug", rel, number, "logger.debug with a payload-shaped argument"))
    for rel in tree.select(*WEB_SRC):
        for number, line in enumerate(tree.lines(rel), 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            if JS_DEBUG.search(line):
                hits.append(Hit("debug", rel, number, "console.log/debug/trace or debugger"))
    return hits


# --- secrets ------------------------------------------------------------------------------------------------

# The key shapes the SDK redactor already knows (packages/sdk-python/src/blackbox/redaction.py) plus the
# gitleaks-style generic ones. Bounded quantifiers: no catastrophic backtracking on hostile files.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(p))
    for kind, p in (
        ("private key block", r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----"),
        ("aws access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ("github token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
        ("github token", r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
        ("npm token", r"\bnpm_[A-Za-z0-9]{36}\b"),
        ("gitlab token", r"\bglpat-[A-Za-z0-9_-]{20,}"),
        ("google api key", r"\bAIza[0-9A-Za-z_-]{35}"),
        ("slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
        ("slack webhook", r"hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9/_-]{10,}"),
        ("api key", r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
        ("api key", r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"),
        ("abb api key", r"\babb_(?:live|test)_[A-Za-z0-9._-]{10,}"),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        ("credentials in a url", r"\b[a-z][a-z0-9+.-]{1,10}://[^\s/:@\"']{1,40}:[^\s/@\"']{6,60}@[A-Za-z0-9.-]+"),
    )
)  # fmt: skip
SECRET_SKIP = ("*pnpm-lock.yaml", "*uv.lock", "*package-lock.json", "*.svg", "*.min.js", "*.map")
SECRET_FILE_NAMES = re.compile(r"(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks)|id_(rsa|ed25519|ecdsa))$")
# A password inside a connection string that is an obvious local default is not a leak.
LOCAL_URL_PASSWORDS = {
    "abb_dev_password", "abb_runtime_dev_password", "abb_retention_dev_password", "password", "postgres", "changeme", "secret", "example",
}  # fmt: skip


def check_secrets(tree: Tree) -> list[Hit]:
    hits = []
    for rel in tree.files():
        name = rel.rsplit("/", 1)[-1]
        if SECRET_FILE_NAMES.search(rel) and name not in {".env.example"} and not name.endswith(".example"):
            hits.append(Hit("secrets", rel, 0, "tracked file that looks like a secret store (.env, key, certificate)"))
    for rel in tree.select("*", exclude=SECRET_SKIP, tests=True):
        for number, line in enumerate(tree.lines(rel), 1):
            if len(line) > 4000:
                line = line[:4000]
            for kind, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if kind == "credentials in a url":
                    password = re.split(r"[:@]", match.group(0).split("://", 1)[1])[1]
                    if password.lower() in LOCAL_URL_PASSWORDS or password.startswith(("$", "{", "<", "%")):
                        continue
                value = match.group(0)
                if kind == "private key block":  # the header alone says nothing; judge the body on the next lines
                    value = " ".join(tree.lines(rel)[number - 1 : number + 2])
                hits.append(Hit("secrets", rel, number, f"{kind}-shaped string", value=value))
                break
    return hits


# --- charts -------------------------------------------------------------------------------------------------

CHART_FILE_HINT = re.compile(r"<BarList\b|<DayColumns\b|bar-fill|<polyline\b|<svg\b|<Sparkline\b|<canvas\b")
NUMBER_ARRAY = re.compile(r"\[\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?){3,}\s*,?\s*\]")
LITERAL_POINT = re.compile(r"\b(?:value|y|count|total|cost)\s*:\s*-?\d+(?:\.\d+)?\s*[,}]")
EXPORT_FN = re.compile(r"^export function ([A-Z]\w*)\(\s*(\{?)\s*(\)?)", re.M)


def check_charts(tree: Tree) -> list[Hit]:
    """Charts derive from data: no randomness, no literal series, and every exported chart takes props."""
    hits = []
    for rel in tree.select("apps/web/src/*.ts", "apps/web/src/*.tsx", "site/src/*.ts", "site/src/*.tsx"):
        for number, line in enumerate(tree.lines(rel), 1):
            if re.search(r"Math\.random\(", line) and not line.lstrip().startswith(("//", "*")):
                hits.append(Hit("charts", rel, number, "Math.random in product source (nothing displayed may be invented)"))
    for rel in tree.select("apps/web/src/components/*.tsx", "apps/web/src/app/*.tsx", "site/src/components/*.tsx"):
        lines = tree.lines(rel)
        text = "\n".join(lines)
        if not (rel.endswith("/charts.tsx") or CHART_FILE_HINT.search(text)):
            continue
        for number, line in enumerate(lines, 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            if NUMBER_ARRAY.search(line) and "viewBox" not in line and "points" not in line:
                hits.append(Hit("charts", rel, number, "literal numeric series in a chart component"))
            elif LITERAL_POINT.search(line) and "style" not in line:
                hits.append(Hit("charts", rel, number, "literal data point in a chart component"))
        if rel.endswith("/charts.tsx"):
            for match in EXPORT_FN.finditer(text):
                if not match.group(2):  # `export function Chart()` with no props destructured
                    number = text[: match.start()].count("\n") + 1
                    hits.append(Hit("charts", rel, number, f"chart component {match.group(1)} takes no data props"))
    return hits


# --- routes -------------------------------------------------------------------------------------------------

APP_DIR = "apps/web/src/app/"
PATH_LITERAL = re.compile(r"""(?:["'`])(/(?:w/|login|invite|settings)[^"'`\s]*)(?:["'`])""")
TEMPLATE_HOLE = re.compile(r"\$\{[^}]*\}")
PARTIAL_HOLE = re.compile(r"(?<=[A-Za-z0-9_-])\$\{.*$")  # `alerts${tab ? ...}`: the rest of the string is built at run time


def normalise(path: str) -> str:
    """A written path as a page path: query and anchor dropped, a `${...}` segment becomes the wildcard `x`."""
    path = PARTIAL_HOLE.sub("", path.split("?")[0].split("#")[0])
    return TEMPLATE_HOLE.sub("x", path).rstrip("/") or "/"


def route_patterns(tree: Tree) -> list[re.Pattern[str]]:
    patterns = []
    for rel in tree.files():
        if not rel.startswith(APP_DIR) or not re.search(r"/page\.tsx$", rel):
            continue
        if "/api/" in rel:
            continue
        segments = [s for s in rel[len(APP_DIR) : -len("/page.tsx")].split("/") if s and not s.startswith("(")]
        if rel == APP_DIR + "page.tsx":
            segments = []
        regex = "".join("/" + ("[^/]+" if s.startswith("[") else f"(?:{re.escape(s)}|x)") for s in segments) or "/"
        patterns.append(re.compile("^" + regex + "$"))
    return patterns


def check_routes(tree: Tree) -> list[Hit]:
    """Every internal path written in product source resolves to a page file (dynamic segments match any one)."""
    patterns = route_patterns(tree)
    hits = []
    if not patterns:
        return [Hit("routes", APP_DIR, 0, "no page files found")]
    for rel in tree.select("apps/web/src/*.ts", "apps/web/src/*.tsx"):
        if rel.startswith(APP_DIR + "api/"):
            continue
        for number, line in enumerate(tree.lines(rel), 1):
            if line.lstrip().startswith(("//", "*", "/*")):
                continue
            for match in PATH_LITERAL.finditer(line):
                if match.group(1).endswith("/"):
                    continue  # a prefix test such as startsWith("/w/"), not a link
                path = normalise(match.group(1))
                if not any(p.match(path) for p in patterns):
                    hits.append(Hit("routes", rel, number, f"internal path {match.group(1)!r} matches no page file"))
    # Helpers in lib/routes.ts build paths from fragments; check the ones the top bar and Settings use.
    for rel in ("apps/web/src/lib/routes.ts", "apps/web/src/lib/settingsNav.ts"):
        for number, line in enumerate(tree.lines(rel), 1):
            for tmpl in re.findall(r"`(/w/[^`]*)`", line):
                path = normalise(tmpl)
                if "projectPath(" in tmpl or not path.startswith("/w/x/"):
                    continue
                if not any(p.match(path) for p in patterns):
                    hits.append(Hit("routes", rel, number, f"route helper path {tmpl!r} matches no page file"))
    return hits


# --- network ------------------------------------------------------------------------------------------------

EXTERNAL = r"(?:https?:)?//(?!localhost\b|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)[A-Za-z0-9.-]+\.[a-z]{2,}"
NETWORK_RES = (
    ("script/img/iframe/source src to another host", re.compile(rf"""<(?:script|img|iframe|source|video|audio)\b[^>]*\bsrc\s*=\s*["']{EXTERNAL}""", re.I)),
    ("stylesheet/preload link to another host", re.compile(rf"""<link\b[^>]*\bhref\s*=\s*["']{EXTERNAL}""", re.I)),
    ("css @import/url() to another host", re.compile(rf"""(?:@import\s+(?:url\()?|url\()\s*["']?{EXTERNAL}""", re.I)),
    ("fetch/XHR/WebSocket/EventSource to another host", re.compile(rf"""(?:fetch|WebSocket|EventSource|axios\.\w+|\.open)\(\s*["'`]{EXTERNAL.replace('https?:', 'wss?:|https?:')}""")),
    ("module import from a URL", re.compile(r"""(?:from|import)\s*\(?\s*["']https?://""")),
)  # fmt: skip


def check_network(tree: Tree) -> list[Hit]:
    """Static half of 'no third-party requests'. The runtime half (a request log) is `check-site.sh network`."""
    hits = []
    for rel in tree.select(
        "apps/web/src/*", "apps/web/public/*", "site/src/*", "site/public/*", "apps/web/next.config.ts", exclude=("*.map",)
    ):
        for number, line in enumerate(tree.lines(rel), 1):
            for what, pattern in NETWORK_RES:
                if pattern.search(line):
                    hits.append(Hit("network", rel, number, what))
                    break
    return hits


# --- screenshots --------------------------------------------------------------------------------------------

SHOT_DIR = "docs/screenshots/release"
SHOT_MAX_COUNT = 12
SHOT_MAX_BYTES = 400 * 1024


def check_shots(tree: Tree) -> list[Hit]:
    hits = []
    pngs = [f for f in tree.files() if f.startswith(SHOT_DIR + "/") and f.lower().endswith(".png")]
    if len(pngs) > SHOT_MAX_COUNT:
        hits.append(Hit("shots", SHOT_DIR, 0, f"{len(pngs)} PNGs exceed the budget of {SHOT_MAX_COUNT}"))
    for rel in pngs:
        size = (tree.root / rel).stat().st_size
        if size > SHOT_MAX_BYTES:
            hits.append(Hit("shots", rel, 0, f"{size // 1024} KB exceeds {SHOT_MAX_BYTES // 1024} KB"))
    return hits


# --- claims -------------------------------------------------------------------------------------------------

CLAIM_RULES = (
    ("a test count", re.compile(r"\b\d[\d,]{1,}\+?\s+(?:unit\s+|e2e\s+|python\s+)?(?:tests?|checks|assertions)\b(?! (?:are|is)\b)", re.I)),
    ("a phase table or phase count", re.compile(r"^\|\s*(?:Phase|\d+)\s*\|.*\|\s*(?:done|complete|built)\b|\b\d+\s+phases\b", re.I | re.M)),
    ("a claim the project cannot make", re.compile(r"\bproduction[- ]ready\b|\bSOC ?2\b|\benterprise[- ]grade\b|\bSaaS\b|\bhosted (?:version|service|offering)\b", re.I)),
)  # fmt: skip
CLAIM_FILES = ("README.md", "site/src/*", "docs/quickstart.md", "docs/deployment.md")


_INVISIBLE = re.compile("[\u00ad\u200b-\u200d\u2060\ufeff]")
_HYPHENS = re.compile("[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]")
LIVE_RE = re.compile(r"--live|real[\s-]*model[\s-]*mode", re.I)
LIVE_OK = re.compile(r"untested|not tested|(?:never|not) been run|your own key|your key", re.I)
ADAPTER_RE = re.compile(r"works with .*(langgraph|openai|anthropic|mcp)", re.I)
ADAPTER_OK = re.compile(r"offline|contract|conformance|not against", re.I)


def visible_text(text: str) -> str:
    """What a reader sees: entities decoded, NFKC, soft hyphens and zero-width characters gone, Unicode hyphens folded."""
    text = unicodedata.normalize("NFKC", html.unescape(text))
    return _HYPHENS.sub("-", _INVISIBLE.sub("", text))


def _paragraphs(lines: list[str]) -> list[tuple[int, list[str]]]:
    """(first line number, normalised lines) per blank-line-separated block."""
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 1
    for number, raw in enumerate(lines, 1):
        if raw.strip():
            if not current:
                start = number
            current.append(visible_text(raw))
        elif current:
            blocks.append((start, current))
            current = []
    if current:
        blocks.append((start, current))
    return blocks


def _unqualified(block: list[str], start: int, claim: re.Pattern[str], qualifier: re.Pattern[str], whole_block: bool) -> int | None:
    """The line of a claim whose qualifier is not in the same sentence (or, for `whole_block`, the same paragraph)."""
    text = " ".join(block)
    if whole_block:
        sentences = [text] if claim.search(text) else []
    else:
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", text) if claim.search(x)]
    for sentence in sentences:
        if not qualifier.search(sentence):
            for offset, line in enumerate(block):
                if claim.search(line):
                    return start + offset
            return start
    return None


def check_claims(tree: Tree) -> list[Hit]:
    """The README/site claims table (plan 'Claims to verify'): forbidden claims absent, qualified claims qualified."""
    hits = []
    for rel in tree.select(*CLAIM_FILES):
        lines = tree.lines(rel)
        text = visible_text("\n".join(lines))
        for what, pattern in CLAIM_RULES:
            for match in pattern.finditer(text):
                number = text[: match.start()].count("\n") + 1
                hits.append(Hit("claims", rel, number, f"README/site must not state {what}"))
        for start, block in _paragraphs(lines):
            number = _unqualified(block, start, LIVE_RE, LIVE_OK, whole_block=True)
            if number:
                hits.append(Hit("claims", rel, number, "real-model mode (KI-044) must be worded 'available, untested by the project' in the same paragraph"))
            number = _unqualified(block, start, ADAPTER_RE, ADAPTER_OK, whole_block=True)
            if number:
                hits.append(Hit("claims", rel, number, "adapter claim without the 'tested offline' qualifier in the same paragraph"))
    readme = tree.root / "README.md"
    if readme.exists() and re.search(r"open[- ]source", readme.read_text(encoding="utf-8"), re.I) and not (tree.root / "LICENSE").exists():
        hits.append(Hit("claims", "README.md", 0, "'open source' claimed but LICENSE is missing"))
    return hits


# --- links --------------------------------------------------------------------------------------------------


def check_links(tree: Tree) -> list[Hit]:
    """Relative links and anchors, via check_docs_links.py with its root rebound to this tree."""
    import contextlib
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("check_docs_links", HERE / "check_docs_links.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = tree.root
    paths = [p for p in ("README.md", "docs", "site", "CONTRIBUTING.md") if (tree.root / p).exists()]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = mod.main(paths)
    if code == 0:
        return []
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip() and not ln.startswith("checked ")]
    return [Hit("links", "docs", 0, ln) for ln in lines[:50]]


CHECKS = {
    "placeholders": check_placeholders,
    "debug": check_debug,
    "secrets": check_secrets,
    "charts": check_charts,
    "routes": check_routes,
    "network": check_network,
    "shots": check_shots,
    "claims": check_claims,
    "links": check_links,
}
# `links` shells out to check_docs_links.py (and resolves paths against the real root), `claims` is about the
# README text; both are part of `all`.


def run(root: Path, names: list[str], release: bool) -> int:
    tree = Tree(root)
    entries = load_allow(tree.root)
    failed = 0
    for name in names:
        hits = CHECKS[name](tree)
        fails, warns = [], []
        for hit in hits:
            text = tree.lines(hit.path)[hit.line - 1] if hit.line and (tree.root / hit.path).is_file() else hit.detail
            if allowed(entries, hit, text):
                continue
            (fails if (not hit.gated or release) else warns).append(hit)
        for hit in warns:
            print(f"warn {name} {hit} (gated: fails under --release)")
        for hit in fails:
            print(f"FAIL {name} {hit}")
        if not fails:
            print(f"ok   {name}" + (f" ({len(warns)} gated marker(s) pending)" if warns else ""))
        failed += bool(fails)
    stale = [e for e in entries if not e.used and e.check in names]
    if len(names) == len(CHECKS):  # a stale entry is only knowable when every check ran
        for e in stale:
            print(f"FAIL allow-list entry matches nothing: {e.check} | {e.glob} | {e.needle}")
            failed += 1
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    root, release, names = HERE.parent, False, []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--root":
            root = Path(args.pop(0))
        elif arg == "--release":
            release = True
        elif arg == "all":
            names += list(CHECKS)
        elif arg in CHECKS:
            names.append(arg)
        else:
            print(f"unknown argument {arg!r}; checks: {' '.join(CHECKS)} all", file=sys.stderr)
            return 2
    return run(root, names or list(CHECKS), release)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
