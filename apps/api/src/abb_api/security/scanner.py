"""Deterministic, bounded secret scanner: the server's second layer of redaction (ADR-130).

The SDK redacts best effort before sending (ADR-031, KI-032). This scanner runs again on the
server before anything is stored, so a secret the SDK missed is not kept. It has the same kinds as
the SDK (`tests/test_security_scanner_parity.py` keeps them in step) but its patterns are written
for hostile input:

- Every quantifier that can be followed by a failing token is bounded, so no input makes a pattern
  rescan a long run from many start positions (the SDK's patterns have unbounded stars that are
  quadratic on adversarial text; the server cannot assume a friendly client, INV-5).
- Whitespace runs before a value are possessive, so a "not already redacted" check after them
  cannot be satisfied by giving a space back: scanning redacted text changes nothing.
- Text is scanned in windows of at most `WINDOW` characters cut at a newline or a separator, only
  where `_seam_is_safe` says no secret straddles the cut, so one pattern call is never longer than
  that, and the time budget is checked between windows.
- The whole scan has a size budget and a time budget. Beyond either it raises, and the caller drops
  the payload (fail closed): `ScanTooLarge` or `ScanFailed`.
- It is deterministic: the same text always yields the same output (unless the time budget runs
  out, which depends on host load: ADR-130 Amendment 2), and scanning already-redacted text changes
  nothing and counts nothing (every pattern ignores `[REDACTED...`, bare or quoted), which is what
  makes at-least-once redelivery idempotent.

It never logs and never returns a secret: results carry the redacted text, a count and kind names.
Detection is best effort and cannot find every secret (KI-032); it narrows the gap, it does not
close it.
"""

import re
import time
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

WINDOW = 64 * 1024  # characters per pattern call
MAX_UNIT_CHARS = 256 * 1024  # one string handed to `scan_text`
DEFAULT_MAX_SECONDS = 2.0

_Q = 2048  # longest quoted value a pattern will look for before giving up on the quote
_NAME = 64  # longest identifier part before a keyword in an assignment

# What names an assignment as a credential (review P2-1). The old patterns matched the keyword
# anywhere in a name (`MONKEY`, `primary_key`, `PWD`, `"path"` contains `pat`), which rewrote
# ordinary text in every project with no opt-out. A name is secret-shaped when it ENDS with:
#   - secret, token, password, passwd, passphrase, credential(s), after any prefix (`dbpassword`);
#   - dsn, pass, as a whole name segment (`SENTRY_DSN`, `DB_PASS`, but not `bypass`, `passed`);
#   - pat, pwd, as a segment that has a prefix (`GH_PAT`, `ROOT_PWD`; a bare `PWD` is the shell's
#     working directory);
#   - key, only after api/access/private/secret/auth/encryption/signing/master/license
#     (`AWS_SECRET_ACCESS_KEY`, `apiKey`; not `primary_key`, `cache_key`, `MONKEY`).
# A segment starts after a non-alphanumeric or at a lower-to-upper case change (`clientSecret`).
# The segment assertions use scoped case-sensitive groups inside the case-insensitive pattern.
_SEG = r"(?:(?<![A-Za-z0-9])|(?-i:(?<=[a-z0-9])(?=[A-Z])))"
_PREFIXED = r"(?:(?<=[A-Za-z0-9][_-])|(?-i:(?<=[a-z0-9])(?=[A-Z])))"
_QUALIFIER = r"(?:api|access|private|secret|auth|encryption|signing|master|license)"
_STRONG = rf"[A-Za-z0-9_]{{0,{_NAME}}}(?:secret|token|password|passwd|passphrase|credentials?)"
_N_ASSIGN = rf"(?:\b{_STRONG}|{_SEG}dsn|{_SEG}{_QUALIFIER}[_-]?key)"
_N_SHORT = rf"(?:{_SEG}pass|{_PREFIXED}(?:pat|pwd))"
# The same names inside quotes (JSON, YAML, Python dicts): the closing quote ends the name, and
# a few more whole-word keys are credentials there (`"auth"`, `"cookie"`).
_N_QUOTED = (
    rf"[A-Za-z0-9_.-]{{0,{_NAME}}}(?:(?:secret|token|password|passwd|passphrase|credentials?)"
    rf"|{_SEG}(?:dsn|pass|auth|authorization|cookie)|{_SEG}{_QUALIFIER}[_-]?key"
    rf"|{_PREFIXED}(?:pat|pwd))"
)
_NOT_MARKED = r"(?![\"']?\[REDACTED)"  # an SDK or earlier marker, bare or inside quotes


class ScanError(Exception):
    """The scan could not complete. The caller must not store the unscanned text."""


class ScanTooLarge(ScanError):
    """The input exceeds the size budget (or has a line that cannot be split safely)."""


class ScanFailed(ScanError):
    """The scanner errored or ran out of time."""


_PATTERNS: tuple[tuple[str, "re.Pattern[str]", str], ...] = tuple(
    (kind, re.compile(pattern), repl)
    for kind, pattern, repl in (
        ("aws_access_key", r"(?:AKIA|ASIA)[0-9A-Z]{16}", "[REDACTED:aws_access_key]"),
        ("github_token", r"gh[pousr]_[A-Za-z0-9]{30,}", "[REDACTED:github_token]"),
        ("github_token", r"github_pat_[A-Za-z0-9_]{20,}", "[REDACTED:github_token]"),
        ("npm_token", r"npm_[A-Za-z0-9]{36}", "[REDACTED:npm_token]"),
        ("gitlab_token", r"glpat-[A-Za-z0-9_-]{20,}", "[REDACTED:gitlab_token]"),
        ("google_api_key", r"AIza[0-9A-Za-z_-]{35}", "[REDACTED:google_api_key]"),
        ("google_oauth", r"ya29\.[0-9A-Za-z_-]{20,}", "[REDACTED:google_oauth]"),
        (
            "slack_webhook",
            r"hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9/_-]+",
            "hooks.slack.com/[REDACTED:slack_webhook]",
        ),
        ("slack_token", r"xox[abprs]-[A-Za-z0-9-]{10,}", "[REDACTED:slack_token]"),
        ("api_key", r"(?<![A-Za-z0-9])sk-(?:ant-)?[A-Za-z0-9_-]{20,}", "[REDACTED:api_key]"),
        ("api_key", r"[rs]k_(?:live|test)_[A-Za-z0-9]{16,}", "[REDACTED:api_key]"),
        ("abb_api_key", r"\babb_(?:live|test)_[A-Za-z0-9._-]{10,}", "[REDACTED:abb_api_key]"),
        (
            # The first two segments are bounded: a failure after a long run would otherwise be
            # repeated from every "eyJ" that follows a '-' inside the run.
            "jwt",
            r"\beyJ[A-Za-z0-9_-]{8,512}\.[A-Za-z0-9_-]{8,16384}\.[A-Za-z0-9_-]{8,}",
            "[REDACTED:jwt]",
        ),
        ("bearer_token", r"(?i)\b(bearer\s++)[A-Za-z0-9._~+/=-]{16,}", r"\g<1>[REDACTED:bearer]"),
        (
            "authorization",
            r"(?i)(\bauthorization\s*+[:=]\s*+[\"']?(?:(?:basic|bearer|token|digest)\s++)?)"
            r"(?!\[REDACTED|(?:basic|bearer|token|digest)\s++\[REDACTED)[^\s\"',;]{6,}",
            r"\g<1>[REDACTED:authorization]",
        ),
        ("basic_auth", r"(?i)\b(basic\s++)[A-Za-z0-9+/]{12,}={0,2}", r"\g<1>[REDACTED:basic_auth]"),
        (
            "cli_user",
            # Only a `user:password` argument (curl -u alice:pw). `python -u`, `sort -u`,
            # `pip install -U`, `docker run -u 1000` and uid:gid pairs are ordinary (review P1-1).
            rf"(?i)((?:^|\s)(?:-u|--user|--proxy-user)(?:\s++|=)[\"']?)"
            rf"(?!\[REDACTED|\d{{1,10}}:\d{{1,10}}(?![^\s\"']))[^\s\"':]{{0,{_NAME}}}+:[^\s\"']+",
            r"\g<1>[REDACTED:cli_user]",
        ),
        (
            "cli_secret_flag",
            r"(?i)(\s--(?:password|passwd|pass|token|secret|api[-_]?key|access[-_]?key|"
            rf"client[-_]secret|auth[-_]?token|private[-_]?key)(?:\s++|=)){_NOT_MARKED}"
            rf"(?:\"[^\"]{{0,{_Q}}}\"|'[^']{{0,{_Q}}}'|[^\s]+)",
            r"\g<1>[REDACTED:cli_secret]",
        ),
        (
            "quoted_key_value",  # "password": "x", 'api_key': 'x' (JSON, YAML, Python dicts)
            rf"(?i)([\"']{_N_QUOTED}[\"']\s*+(?::|=>)\s*+){_NOT_MARKED}"
            rf"(?:\"[^\"]{{0,{_Q}}}\"|'[^']{{0,{_Q}}}'|[^\s,}}\]]+)",
            r"\g<1>[REDACTED:credential]",
        ),
        (
            "db_cli_password",  # mysql -phunter2, sshpass -p x, docker login -p x
            r"(?i)(\b(?:mysql|mysqldump|mysqladmin|mysqlcheck|mariadb|sshpass|docker\s++login|"
            r"podman\s++login|helm\s++registry\s++login)\b[^\n|;&]{0,512}?\s-p)\s*+(?!\[REDACTED)"
            r"[^\s-]\S*",
            r"\g<1>[REDACTED:cli_secret]",
        ),
        (
            "npm_config_token",
            r"(?i)(//\S{1,256}/:_auth(?:token)?[=\s]++|"
            r"npm\s++config\s++set\s++\S{0,128}(?:auth|token|password)\S{0,128}\s++)"
            r"(?!\[REDACTED)\S+",
            r"\g<1>[REDACTED:npm_auth]",
        ),
        (
            "signed_url_param",
            r"(?i)([?&](?:sig|signature|x-amz-signature|x-amz-credential|x-amz-security-token|"
            r"x-goog-signature|access_token|id_token|token|api[_-]?key|apikey|key)=)"
            r"(?!\[REDACTED)[^&\s\"']+",
            r"\g<1>[REDACTED:url_param]",
        ),
        (
            "cookie_header",
            r"(?i)\b((?:set-)?cookie\s*+:\s*+)(?!\[REDACTED)[^\r\n]+",
            r"\g<1>[REDACTED:cookie]",
        ),
        (
            "url_credentials",
            r"(://)[^/\s:@]*:[^/\s@]+@",
            r"\g<1>[REDACTED:url_credentials]@",
        ),
        (
            # A long userinfo is only a token when it looks like one: a long user name
            # (`my-long-service-account@host`) is lower-case words joined by hyphens (review P3-7).
            "url_token_user",
            r"(://)(?=[A-Za-z0-9_%-]*[0-9_]|(?=[A-Za-z0-9_%-]*[a-z])[A-Za-z0-9_%-]*[A-Z])"
            r"[A-Za-z0-9_%-]{16,}@",
            r"\g<1>[REDACTED:url_credentials]@",
        ),
        (
            "secret_assignment",  # NAME=value, NAME ending in a secret word (AWS_SECRET_ACCESS_KEY)
            rf"(?i)({_N_ASSIGN}\s*+(?::=|[=:](?![=>~]))\s*+){_NOT_MARKED}"
            rf"(?:\"[^\"]{{0,{_Q}}}\"|'[^']{{0,{_Q}}}'|[^\s,;&]+)",
            r"\g<1>[REDACTED:credential]",
        ),
        (
            "secret_assignment_short",  # GH_PAT=, DB_PASS=, ROOT_PWD=: a whole name segment
            rf"(?i)({_N_SHORT}\s*+(?::=|[=:](?![=>~]))\s*+){_NOT_MARKED}"
            rf"(?:\"[^\"]{{0,{_Q}}}\"|'[^']{{0,{_Q}}}'|[^\s,;&]+)",
            r"\g<1>[REDACTED:credential]",
        ),
        (
            "secret_comparison_literal",  # `password == "x"`: keep the code, hide the literal
            rf"(?i)(\b(?:[A-Za-z0-9_]{{0,{_NAME}}}(?:secret|token|password|passwd|passphrase)"
            rf"|{_SEG}{_QUALIFIER}[_-]?key)\s*+===?\s*+){_NOT_MARKED}"
            rf"(?:\"[^\"]{{1,{_Q}}}\"|'[^']{{1,{_Q}}}')",
            r"\g<1>[REDACTED:credential]",
        ),
        (
            # Bare names; the earlier assignment patterns catch these first, and a bare `pwd` is
            # the shell's working directory, so it is not a credential name.
            "credential",
            r"(?i)(\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
            rf"\s*+(?::=|[=:](?![=>~]))\s*+){_NOT_MARKED}"
            rf"(?:\"[^\"]{{0,{_Q}}}\"|'[^']{{0,{_Q}}}'|[^\s,;&]+)",
            r"\g<1>[REDACTED:credential]",
        ),
    )
)

# Cheap necessary conditions, one per pattern (same order as _PATTERNS): a pattern can only match a
# window that contains one of its needles, so prose and logs skip most of the 29 passes. Only used
# for ASCII windows (the needles are compared after `lower()`, which is exact there; case folding
# of other scripts is not, e.g. a dotless i matches `i` under IGNORECASE).
_NEEDLES: tuple[tuple[str, ...], ...] = (
    ("akia", "asia"),
    ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"),
    ("github_pat_",),
    ("npm_",),
    ("glpat-",),
    ("aiza",),
    ("ya29.",),
    ("hooks.slack.com",),
    ("xox",),
    ("sk-",),
    ("rk_", "sk_"),
    ("abb_",),
    ("eyj",),
    ("bearer",),
    ("authorization",),
    ("basic",),
    ("-u",),
    ("--",),
    (":", "=>"),
    ("mysql", "mariadb", "sshpass", "login"),
    ("/:_auth", "npm"),
    ("=",),
    ("cookie",),
    ("://",),
    ("://",),
    (":", "="),
    (":", "="),
    ("==",),
    (":", "="),
)
assert len(_NEEDLES) == len(_PATTERNS)

# A private key spans many lines, so it is found with plain string search before the line-bounded
# patterns (an unterminated key is redacted to the end of the text, as the SDK does).
_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_KEY_END = re.compile(r"-----END [A-Z ]{0,40}PRIVATE KEY(?: BLOCK)?-----")
_PRIVATE_KEY_MARK = "[REDACTED:private_key]"

# The SDK's cheap pre-check: plain prose skips the regex pass. A run of 16+ letters is kept in
# because some credentials (AKIA... access keys) are letters only.
_MAYBE_SECRET = re.compile(r"[-_=:@.]|\d|[A-Za-z]{16}")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_BREAKS = frozenset(" \t,;&\"'")

PATTERN_KINDS: frozenset[str] = frozenset({kind for kind, _, _ in _PATTERNS} | {"private_key"})
# Server-only: the value under a denied key of a structured payload (`password`, `token` ...).
DENIED_KEY_KIND = "denied_key"
KINDS: frozenset[str] = PATTERN_KINDS | {DENIED_KEY_KIND}
# Kinds whose shape is specific to a credential (a provider token, a private key). The other kinds
# are assignment- or syntax-shaped (`token = ...`, `-u user:pw`, `Cookie: ...`) and also match
# ordinary code, so a consumer that raises an alarm (the Phase 13 detectors) should rely on these.
HIGH_CONFIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "private_key", "aws_access_key", "github_token", "npm_token", "gitlab_token",
        "google_api_key", "google_oauth", "slack_webhook", "slack_token", "api_key",
        "abb_api_key", "jwt",
    }
)  # fmt: skip


class ScanBudget:
    """Characters and seconds one unit of work (an event, an artifact) may spend."""

    def __init__(
        self,
        max_chars: int,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_chars = max_chars
        self._clock = clock
        self._deadline = clock() + max_seconds
        self.used = 0

    def charge(self, chars: int) -> None:
        self.used += chars
        if self.used > self.max_chars:
            raise ScanTooLarge("scan size budget exceeded")
        self.check_time()

    def check_time(self) -> None:
        if self._clock() > self._deadline:
            raise ScanFailed("scan time budget exceeded")


@dataclass
class _Tally:
    count: int = 0
    kinds: set[str] = field(default_factory=set)

    def add(self, kind: str, n: int = 1) -> None:
        if n:
            self.count += n
            self.kinds.add(kind)


@dataclass(frozen=True)
class ScanResult:
    """The redacted text and what was found. `kinds` are names only, sorted, never values."""

    text: str
    count: int = 0
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class BytesScanResult:
    data: bytes
    count: int = 0
    kinds: tuple[str, ...] = ()


def normalize(text: str) -> str:
    """NUL and terminal escape sequences removed: they can split a secret from its pattern."""
    if "\x00" in text:
        text = text.replace("\x00", "")
    if "\x1b" in text:
        text = _ANSI.sub("", text)
    return text


# Zero-width and soft-hyphen code points that split a secret from its pattern without changing how
# it reads. They, and compatibility forms such as full-width letters, are folded away for
# DETECTION only (below); the stored text is never rewritten by the fold itself.
_INVISIBLE = {ord(c): None for c in "\u00ad\u180e\u200b\u200c\u200d\u2060\ufeff"}


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text.translate(_INVISIBLE))


def _redact_hidden_secrets(text: str, tally: _Tally, budget: "ScanBudget") -> str:
    """Redact whole lines whose secret shows only after folding look-alike characters.

    The patterns run on the folded line; a match cannot be mapped back to the original characters,
    so the line is replaced by the markers of what was found. A line the fold does not change, or
    in which the fold finds nothing, is stored exactly as it was.
    """
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if line.isascii():
            continue
        folded = _fold(line)
        if folded == line:
            continue
        found = _Tally()
        for window in _windows(folded, budget):
            budget.check_time()
            _scan_window(window, found)
        if found.count:
            kinds = sorted(found.kinds)
            lines[index] = "".join(f"[REDACTED:{kind}]" for kind in kinds)
            for kind in kinds:
                tally.add(kind)
    return "\n".join(lines)


def _redact_private_keys(text: str, tally: _Tally) -> str:
    if "PRIVATE KEY" not in text:
        return text
    parts: list[str] = []
    position = 0
    while (begin := _KEY_BEGIN.search(text, position)) is not None:
        end = _KEY_END.search(text, begin.end())
        stop = end.end() if end is not None else len(text)
        parts.append(text[position : begin.start()])
        parts.append(_PRIVATE_KEY_MARK)
        tally.add("private_key")
        position = stop
    parts.append(text[position:])
    return "".join(parts)


def _scan_window(window: str, tally: _Tally, *, gated: bool = True) -> str:
    if len(window) < 8 or _MAYBE_SECRET.search(window) is None:
        return window
    lower = window.lower() if gated and window.isascii() else None
    for (kind, pattern, repl), needles in zip(_PATTERNS, _NEEDLES, strict=True):
        if lower is not None and not any(needle in lower for needle in needles):
            continue
        changed = 0

        def replace(match: "re.Match[str]", repl: str = repl) -> str:
            nonlocal changed
            new = match.expand(repl)
            if new != match.group(0):  # redacting a redaction is not a finding
                changed += 1
            return new

        window = pattern.sub(replace, window)
        tally.add(kind, changed)
    return window


_SEAM = 4096  # characters either side of a cut that are compared with and without the cut
_CUT_TRIES = 64  # candidate cuts examined before the line is refused


def _seam_is_safe(text: str, cut: int) -> bool:
    """Would scanning `text` in two pieces at `cut` redact exactly what one piece would?

    A match that straddles the cut (a quoted value with spaces, `Bearer ` then the token, `Cookie:`
    then its value) would leave its second half stored in clear. The two ends of the seam are cut
    at the same places in both runs, so any difference is caused by the cut itself.
    """
    left, right = text[max(0, cut - _SEAM) : cut], text[cut : cut + _SEAM]
    whole = _scan_window(left + right, _Tally())
    return whole == _scan_window(left, _Tally()) + _scan_window(right, _Tally())


def _windows(text: str, budget: "ScanBudget | None" = None) -> Iterator[str]:
    """Pieces of at most WINDOW characters, cut after a newline, else after a separator.

    A cut is only made where `_seam_is_safe` says no secret straddles it. A piece without such a
    cut raises ScanTooLarge: storing the text unscanned is the only alternative.
    """
    size = len(text)
    start = 0
    floor = WINDOW * 3 // 4
    while start < size:
        end = min(start + WINDOW, size)
        if end < size:
            end = _safe_cut(text, start, end, floor, budget)
        yield text[start:end]
        start = end


def _safe_cut(text: str, start: int, end: int, floor: int, budget: "ScanBudget | None") -> int:
    tries = 0
    newline = text.rfind("\n", start, end)
    while newline > start and tries < _CUT_TRIES:
        if budget is not None:
            budget.check_time()
        if _seam_is_safe(text, newline + 1):
            return newline + 1
        newline = text.rfind("\n", start, newline)
        tries += 1
    for i in range(end, start + floor, -1):
        if text[i - 1] in _BREAKS:
            if budget is not None:
                budget.check_time()
            if _seam_is_safe(text, i):
                return i
            tries += 1
            if tries >= _CUT_TRIES:
                break
    raise ScanTooLarge("a line is too long to scan safely")


class SecretScanner:
    """Stateless; safe to share between threads."""

    def scan_text(
        self, text: str, budget: ScanBudget, *, unit_limit: int = MAX_UNIT_CHARS
    ) -> ScanResult:
        """Redact one string (an attribute, a payload value)."""
        if len(text) > unit_limit:
            raise ScanTooLarge("value exceeds the per-value scan limit")
        return self._guarded(self._scan, text, budget)

    def scan_bytes(self, data: bytes, budget: ScanBudget) -> BytesScanResult:
        """Redact a document (terminal output, a diff). Invalid UTF-8 round-trips unchanged."""
        budget.charge(len(data))  # before decoding: an oversized body is never copied
        text = data.decode("utf-8", "surrogateescape")
        result = self._guarded(self._scan, text, budget, charged=True)
        return BytesScanResult(
            result.text.encode("utf-8", "surrogateescape"), result.count, result.kinds
        )

    @staticmethod
    def _guarded(
        function: Callable[..., ScanResult], text: str, budget: ScanBudget, **kwargs: bool
    ) -> ScanResult:
        try:
            return function(text, budget, **kwargs)
        except ScanError:
            raise
        except Exception as exc:
            # Only the type: the message of a regex or decoding error may quote the input.
            raise ScanFailed(type(exc).__name__) from None

    @staticmethod
    def _scan(text: str, budget: ScanBudget, *, charged: bool = False) -> ScanResult:
        if not charged:
            budget.charge(len(text))
        tally = _Tally()
        text = _redact_private_keys(normalize(text), tally)
        out: list[str] = []
        for window in _windows(text, budget):
            budget.check_time()
            out.append(_scan_window(window, tally))
        redacted = "".join(out)
        if not redacted.isascii():
            redacted = _redact_hidden_secrets(redacted, tally, budget)
        return ScanResult(redacted, tally.count, tuple(sorted(tally.kinds)))
