"""Secret values from the workspace, and shell-aware checks for commands that could print them.

A blocklist of command shapes can never be complete (`cat .env`, `cat<.env`, `bash -c`, `grep -r`,
`git show HEAD:.env` ...), so the design is to know the secret *values* and mask them wherever they
appear, however a command read them (ADR-031). Path-based withholding stays as defence in
depth, and is shell-aware: when a command could reach a secret file and we are unsure, its output is
withheld.

Best effort. It cannot find a secret that is in no sensitive file and not in the environment, and it
cannot undo a transform a command applies before printing (hex, rot13, splitting ...).
"""

import base64
import fnmatch
import html
import json
import math
import os
import re
import shlex
import subprocess
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

# Matched case-insensitively against the base name.
SENSITIVE_NAMES: tuple[str, ...] = (
    ".env", ".env*", "*.env", ".envrc", "env.local", "*.tfstate", "*.tfstate.backup", "*.tfvars",
    "id_dsa*", "id_ecdsa*", "id_ed25519*", "id_rsa*", "*.jks", "*.keystore", "*.ppk", "*.gpg",
    "*.pem", "*.key", "*.p12", "*.pfx", "kubeconfig", ".netrc", "_netrc", ".npmrc", ".pypirc",
    ".pgpass", ".htpasswd", ".git-credentials", "credentials", "credentials.*", "secrets.*",
    "*secret*.json", "*secret*.yml", "*secret*.yaml", "*secret*.toml", "*secrets*.json",
    "*secrets*.yml", "*secrets*.yaml", "*secrets*.toml",
)  # fmt: skip
# Sensitive by where they live, whatever their name.
SENSITIVE_SUFFIXES: tuple[str, ...] = (
    ".docker/config.json", ".kube/config", ".aws/credentials", ".aws/config", ".git/config",
)  # fmt: skip
_CREDENTIAL_FILES = (".pgpass", ".htpasswd", ".git-credentials", ".netrc", "_netrc")
_RAW_TOKEN_FILES = ("*.key", "*.ppk", "*.gpg", "master.key", "*.jks", "*.keystore", "id_*")
_TEMPLATE_FILE = re.compile(r"(?i)\.(example|sample|template|dist|defaults?|tmpl|skel)(\.|$)")
# Only real package trees are skipped (secrets in build/ or dist/ are found).
_PRUNE_DIRS = frozenset({".git", "node_modules", "site-packages", "__pycache__"})
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_FILES = 5000
MAX_VALUES = 6000
MAX_REMEMBERED = 50_000  # values kept for the recorder's lifetime; oldest dropped above this
SCAN_BUDGET_SECONDS = 2.0
_PATH = os.environ.get("PATH", os.defpath)
_SECRET_KEY = re.compile(
    r"key|token|secret|pass|pwd|credential|private|auth|dsn|cookie|session|signature|salt"
    r"|database_url|connection|bearer|(?:^|[_.-])pat(?:$|[_.-])",
    re.I,
)
_LINE = re.compile(r"^\s*(?:export\s+|set\s+)?([A-Za-z_][A-Za-z0-9_.\-/]*)\s*[=:]\s*(.*?)\s*$")
# The identifier may only start at a boundary: without the look-behind a long run of identifier
# characters is re-scanned from every position inside it (quadratic: 80,000 characters took 36 s).
_QUOTED = re.compile(
    r"""(?<![A-Za-z0-9_.\-])([A-Za-z_][A-Za-z0-9_.\-]*)\s*[=:]\s*"""
    r"""("(?:[^"\\]|\\.)*"|'[^']*')""",
    re.S,
)
_BLOCK = re.compile(r"^(\s*)([\w.\-]+)\s*:\s*[|>][+-]?\s*$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.+?)\s*$")
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(.*?)(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S
)
_URL_CRED = re.compile(r"://([^/\s:@]*):([^/\s@]+)@")
_URL_USER = re.compile(r"://([^/\s:@]{8,})@")
_CRED_ASSIGN = re.compile(
    r"(?i)(?:_authtoken|_auth|_password|password|passwd|token|secret)\s*[=:]\s*['\"]?([^\s'\"]+)"
)
_NETRC = re.compile(r"\b(?:password|login|account|token)\s+(\S+)")

# Words that are not secrets even when a sample file puts them next to a secret-looking key.
_COMMON = frozenset(
    """
    secret password passwd pass test testing tests changeme change-me example sample true
    false null none nil yes no on off admin root user username localhost dev development
    production prod staging debug default token key apikey api_key secretkey secret_key
    your-secret-here xxx xxxx foo bar baz todo tbd dummy fake demo guest public private
    local info warning error utf-8 utf8 http https postgres mysql redis sqlite memory
    console json html text
    """.split()
)
_PLACEHOLDER = re.compile(
    r"(?i)^(x+|\*+|\.+|-+|_+|<.*>|\$\{.*\}|\$\w+|%\(.*\)s|your[-_ ].*|change.?me.*|replace.*|"
    r"insert.*|dummy.*|fake.*|sample.*|example.*|placeholder.*|1234+|12345.*|abc+|test\d*|secret\d*|password\d*)$"
)


def _unquote_git(path: str) -> str:
    """Undo git's C-style quoting of a path in a header (`"a\\tb"`, octal escapes)."""
    path = path.strip()
    if len(path) < 2 or path[0] != '"' or path[-1] != '"':
        return path
    body, out, i = path[1:-1], bytearray(), 0
    simple = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in "01234567":
                digits = re.match(r"[0-7]{1,3}", body[i + 1 :])
                assert digits
                out.append(int(digits.group(0), 8) & 0xFF)
                i += 1 + len(digits.group(0))
                continue
            out.append(simple.get(nxt, ord(nxt) & 0xFF))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def is_sensitive_path(path: str) -> bool:
    """Does this path name a file that holds secrets? Case-insensitive; also by directory."""
    lowered = _unquote_git(path).lower().strip()
    name = lowered.rsplit("/", 1)[-1]
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in SENSITIVE_NAMES):
        return True
    return any(lowered == s or lowered.endswith("/" + s) for s in SENSITIVE_SUFFIXES)


def git_excludes() -> list[str]:
    """Pathspecs that keep secret-holding files out of a git command (any directory, any case)."""
    return [f":(exclude,icase,glob)**/{pattern}" for pattern in SENSITIVE_NAMES] + [
        f":(exclude,icase,glob)**/{suffix}"
        for suffix in SENSITIVE_SUFFIXES
        if suffix != ".git/config"
    ]


def _entropy(value: str) -> float:
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def high_entropy(value: str) -> bool:
    """Looks like a generated token, not a word: long enough, varied enough."""
    classes = sum(
        any(f(c) for c in value)
        for f in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum())
    )
    return len(value) >= 12 and classes >= 2 and _entropy(value) >= 3.0


def _is_placeholder(value: str) -> bool:
    lowered = value.lower().strip()
    if lowered in _COMMON or _PLACEHOLDER.match(lowered):
        return True
    return lowered.isdigit() and len(lowered) < 10  # ports, timeouts, counts


def _worth_learning(value: str, key_is_secret: bool, by_name: bool, template: bool) -> bool:
    if "\n" in value or not value:
        return False
    if template:  # `.env.example`: only values that look generated; "secret" and "changeme" are not
        return high_entropy(value) and not _is_placeholder(value)
    if _is_placeholder(value):
        return False
    if key_is_secret:
        return len(value) >= 6
    return by_name and len(value) >= 4


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"`":
        return raw[1:-1]
    return raw


def _json_leaves(node: Any, secret_key: bool, out: list[tuple[str, bool]], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _json_leaves(v, secret_key or bool(_SECRET_KEY.search(str(k))), out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _json_leaves(v, secret_key, out, depth + 1)
    elif isinstance(node, str):
        out.append((node, secret_key))


def extract_values(path: str, text: str) -> set[str]:
    """The secret values in one sensitive file's text."""
    name = _unquote_git(path).lower().rsplit("/", 1)[-1]
    by_name = is_sensitive_path(path)
    template = bool(_TEMPLATE_FILE.search(name))
    candidates: list[tuple[str, bool]] = []  # (value, key looks secret)
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            _json_leaves(json.loads(stripped), False, candidates)
        except ValueError:
            pass
    pem_found = False
    for block in _PEM.findall(text):
        pem_found = True
        body = [line.strip() for line in block.splitlines() if line.strip()]
        candidates += [(line, True) for line in body if len(line) >= 16]
        if body:
            candidates.append(("".join(body), True))
    if not pem_found and any(fnmatch.fnmatchcase(name, p) for p in _RAW_TOKEN_FILES):
        # master.key, *.ppk, *.gpg ...: the whole file is the secret (and each of its lines)
        if stripped and "\n" not in stripped:
            candidates.append((stripped, True))
        candidates += [(ln.strip(), True) for ln in text.splitlines() if len(ln.strip()) >= 6]
        if stripped:
            candidates.append((stripped, True))
    for q in _QUOTED.finditer(text):  # multi-line quoted values: all lines
        inner = q.group(2)[1:-1]
        if "\n" in inner:
            secret = bool(_SECRET_KEY.search(q.group(1)))
            candidates.append((inner.strip(), secret))
            candidates += [
                (ln.strip(), secret) for ln in inner.splitlines() if len(ln.strip()) >= 6
            ]
    lines = text.splitlines()
    block_indent: tuple[int, bool] | None = None
    for line in lines:
        if block_indent is not None:  # YAML block scalar body
            indent = len(line) - len(line.lstrip())
            if line.strip() and indent > block_indent[0]:
                candidates.append((line.strip(), True))
                continue
            block_indent = None
        blk = _BLOCK.match(line)
        if blk:
            block_indent = (len(blk.group(1)), bool(_SECRET_KEY.search(blk.group(2))))
            continue
        kv = _LINE.match(line)
        if kv:
            key, raw = kv.group(1), _unquote(kv.group(2))
            secret = bool(_SECRET_KEY.search(key))
            if raw:
                candidates.append((raw, secret))
                pre = re.split(r"\s+#", raw, maxsplit=1)[0].strip().strip("'\"")
                if pre != raw:  # `value #comment`: learn the text with and without the comment
                    candidates.append((pre, secret))
        item = _LIST_ITEM.match(line)
        if item:
            candidates.append((_unquote(item.group(1)), False))
        candidates += [(v, True) for v in _CRED_ASSIGN.findall(line) if len(v) >= 3]
        candidates += [(c.group(2), True) for c in _URL_CRED.finditer(line)]
        candidates += [(c.group(1), True) for c in _URL_USER.finditer(line)]
        if name in _CREDENTIAL_FILES and line.strip() and not line.lstrip().startswith("#"):
            token = line.strip()
            candidates.append((token, True))
            if name == ".pgpass":
                candidates.append((token.rsplit(":", 1)[-1], True))
            if name == ".htpasswd" and ":" in token:
                candidates.append((token.split(":", 1)[1], True))
        if name in (".netrc", "_netrc"):
            candidates += [(v, True) for v in _NETRC.findall(line)]
    values: set[str] = set()
    for value, secret in candidates:
        value = value.strip()
        if _worth_learning(value, secret, by_name, template):
            values.add(value)
    return values


def _b64_alignments(raw: bytes) -> set[str]:
    """Base64 of `raw` as it appears at each of the three byte alignments inside longer text."""
    forms: set[str] = set()
    for offset, skip in ((0, 0), (1, 2), (2, 3)):
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            enc = encode(b"\x00" * offset + raw).decode()
            body = enc[skip:].rstrip("=")
            if offset or len(body) % 4:
                body = body[:-2]  # the last chars depend on what follows the value
            if len(body) >= 8:
                forms.add(body)
    return forms


def derived_forms(value: str) -> set[str]:
    """The value as it may appear escaped or encoded, so a command that escapes it still masks."""
    forms = {value}
    if len(value) >= 24:  # a long value: its head and tail alone are enough to recognise it
        forms.update({value[:12], value[-12:]})
    if len(value) > 512:
        return forms
    raw = value.encode("utf-8", errors="ignore")
    if len(raw) >= 6:
        forms |= _b64_alignments(raw)
        forms.update({raw.hex(), raw.hex().upper()})
    quoted = urllib.parse.quote(value, safe="")
    forms.update(
        {
            quoted,
            urllib.parse.quote_plus(value),
            urllib.parse.quote(value),
            re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), quoted),
            json.dumps(value)[1:-1],
            json.dumps(value, ensure_ascii=False)[1:-1],
            json.dumps(value)[1:-1].replace("/", "\\/"),
            shlex.quote(value),
            repr(value)[1:-1],
            html.escape(value),
            html.escape(value, quote=False),
            xml_escape(value),
            re.sub(r"([^\w])", r"\\\1", value),
            value.replace("\\", "\\\\"),
        }
    )
    return {f for f in forms if len(f) >= 4 or f == value}


@lru_cache(maxsize=8)
def _compile_masker(values: tuple[str, ...]) -> "re.Pattern[str] | None":
    if not values:
        return None
    long_part = [re.escape(v) for v in values if len(v) >= 8]
    short_part = [re.escape(v) for v in values if len(v) < 8]
    parts = list(long_part)
    if short_part:  # short values only count as whole words, never inside identifiers or numbers
        parts.append(r"(?<![A-Za-z0-9_])(?:" + "|".join(short_part) + r")(?![A-Za-z0-9_])")
    return re.compile("|".join(parts))


def mask_text(text: str, values: tuple[str, ...], marker: str) -> str:
    """Replace every occurrence of every value (longest first) with `marker`."""
    if not values or not text:
        return text
    if len(values) <= 24:  # the common case: plain substring replacement is fastest
        for value in values:
            if len(value) < 8:
                text = re.sub(
                    r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])", marker, text
                )
            elif value in text:
                text = text.replace(value, marker)
        return text
    pattern = _compile_masker(values)
    return pattern.sub(marker, text) if pattern else text


@dataclass
class SecretFiles:
    """Finds sensitive files under a root and keeps the set of their secret values current.

    Values are remembered for the lifetime of the object: moving or deleting a secret file does not
    make its content printable.
    """

    root: Path
    paths: list[str] = field(default_factory=list)  # root-relative, posix; secret files now
    history_paths: list[str] = field(default_factory=list)  # secret files that ever existed
    incomplete: bool = False  # the scan hit its time budget: some secret files may be unknown
    _cache: dict[str, tuple[int, int, frozenset[str]]] = field(default_factory=dict)
    _learned: "OrderedDict[str, None]" = field(default_factory=OrderedDict)
    _git_state: str = ""
    _source_blob: str | None = None
    _expanded: tuple[str, ...] = ()
    _expanded_for: int = -1

    def _has_sensitive_entry(self, directory: str) -> bool:
        try:
            return any(is_sensitive_path(e.name) for e in os.scandir(directory))
        except OSError:
            return False

    def _walk(self, deadline: float) -> list[Path]:
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            if time.monotonic() > deadline:
                self.incomplete = True
                break
            keep = []
            for d in dirnames:
                full = os.path.join(dirpath, d)
                pruned = d in _PRUNE_DIRS or os.path.exists(os.path.join(full, "pyvenv.cfg"))
                if pruned:  # a package tree is not walked, but a secret file at its top is found
                    if self._has_sensitive_entry(full):
                        found += [
                            Path(full) / e.name
                            for e in os.scandir(full)
                            if is_sensitive_path(e.name)
                        ]
                else:
                    keep.append(d)
            dirnames[:] = keep
            for filename in filenames:
                full_path = Path(dirpath) / filename
                if is_sensitive_path(full_path.relative_to(self.root).as_posix()):
                    found.append(full_path)
            if len(found) >= MAX_FILES:
                self.incomplete = True
                break
        git_config = self.root / ".git" / "config"
        if git_config.is_file():
            found.append(git_config)
        return found[:MAX_FILES]

    def _git(self, *args: str, limit: int = 4 * 1024 * 1024, timeout: float = 10.0) -> str:
        try:
            done = subprocess.run(  # noqa: S603
                ["git", *args],  # noqa: S607
                cwd=self.root, capture_output=True, timeout=timeout, check=False,
                stdin=subprocess.DEVNULL, env={"PATH": _PATH, "GIT_TERMINAL_PROMPT": "0"},
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout[:limit].decode("utf-8", errors="replace") if done.returncode == 0 else ""

    def _history(self, rels: Iterable[str]) -> tuple[set[str], list[str]]:
        """Secret values and file names from git history, including files deleted long ago."""
        values: set[str] = set()
        if not (self.root / ".git").exists():
            return values, []
        names = {
            n.strip()
            for n in self._git(
                "log",
                "--all",
                "--name-only",
                "--format=",
                "--max-count=2000",
                limit=2 * 1024 * 1024,
            ).splitlines()
            if n.strip() and is_sensitive_path(n)
        }
        specs = [f":(icase,glob)**/{p}" for p in SENSITIVE_NAMES]
        patch = self._git(
            "log", "--all", "-p", "--no-color", "--format=", "--max-count=500", "--", *specs,
            limit=8 * 1024 * 1024, timeout=20.0,
        )  # fmt: skip
        for section in re.split(r"(?m)^diff --git ", patch)[1:]:
            head, _, body = section.partition("\n")
            path = head.rsplit(" b/", 1)[-1] if " b/" in head else head
            changed = "\n".join(
                ln[1:]
                for ln in body.splitlines()
                if ln[:1] in "+-" and ln[:3] not in ("+++", "---")
            )
            values |= extract_values(path, changed)
        refs = ["HEAD:", ":"] + [f"stash@{{{i}}}:" for i in range(3)]
        for rel in list(rels)[:8]:
            for ref in refs:
                values |= extract_values(
                    rel, self._git("show", f"{ref}{rel}", limit=MAX_FILE_BYTES)
                )
        return values, sorted(names)

    def _source_text(self) -> str:
        """Lower-cased text of ordinary source files, to tell identifiers from secrets."""
        if self._source_blob is None:
            parts, total = [], 0
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [
                    d for d in dirnames if d not in _PRUNE_DIRS and not d.startswith(".")
                ]
                for filename in filenames:
                    full = Path(dirpath) / filename
                    if is_sensitive_path(filename) or full.suffix.lower() not in _SOURCE_EXT:
                        continue
                    try:
                        if full.stat().st_size > 512 * 1024:
                            continue
                        parts.append(full.read_text(errors="replace").lower())
                    except OSError:
                        continue
                    total += len(parts[-1])
                    if total > 4 * 1024 * 1024:
                        break
            self._source_blob = "\n".join(parts)
        return self._source_blob

    def _remember(self, values: Iterable[str]) -> None:
        for value in values:
            if value in self._learned:
                self._learned.move_to_end(value)
                continue
            # A word that is also an identifier in the repo's own source is not masked, unless it
            # looks generated: masking `session` everywhere would break the agent.
            if not high_entropy(value) and value.lower() in self._source_text():
                continue
            self._learned[value] = None
        while len(self._learned) > MAX_REMEMBERED:
            self._learned.popitem(last=False)

    def refresh(self) -> tuple[str, ...]:
        """Rescan (cached by size and mtime) and return all secret values, longest first."""
        self.incomplete = False
        self._source_blob = None
        deadline = time.monotonic() + SCAN_BUDGET_SECONDS
        files = self._walk(deadline)
        self.paths = [
            f.relative_to(self.root).as_posix()
            for f in files
            if f.name != "config" or ".git" not in f.parts
        ]
        live: dict[str, tuple[int, int, frozenset[str]]] = {}
        fresh: set[str] = set()
        for full in files:
            rel = full.relative_to(self.root).as_posix()
            try:
                st = full.stat()
            except OSError:
                continue
            cached = self._cache.get(rel)
            if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                values = cached[2]
            elif st.st_size > MAX_FILE_BYTES:
                values = frozenset()
            else:
                try:
                    text = full.read_bytes().decode("utf-8", errors="replace")
                except OSError:
                    continue
                values = frozenset(extract_values(rel, text))
            live[rel] = (st.st_mtime_ns, st.st_size, values)
            fresh |= values
        self._cache = live
        head = self._git("rev-parse", "HEAD", limit=100).strip()
        refs = self._git("for-each-ref", "--format=%(objectname)", limit=200_000)
        state = f"{head}:{hash(refs)}:{','.join(sorted(self.paths))}:{len(self._learned)}"
        if (
            state != self._git_state
        ):  # history lookups only when the repository or a secret file changed
            history_values, history_names = self._history(self.paths)
            fresh |= history_values
            self.history_paths = history_names
            self._git_state = state
        self._remember(fresh)
        if self._expanded_for != len(self._learned) or fresh:
            expanded: set[str] = set()
            for value in self._learned:
                expanded |= derived_forms(value)
            ordered = sorted(expanded, key=lambda v: -len(v))
            self._expanded = tuple(ordered[:MAX_VALUES])
            self._expanded_for = len(self._learned)
        return self._expanded


_SOURCE_EXT = frozenset(
    """
        .py .js .ts .tsx .jsx .go .rs .java .rb .php .c .h .cpp .cs .sh .md .rst .txt .html
        .css .sql
    """.split()
)


# -- shell-aware command check ------------------------------------------------------

_INLINE = {"python": "-c", "python3": "-c", "node": "-e", "ruby": "-e", "perl": "-e", "php": "-r"}
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_RECURSIVE_READERS = frozenset(
    {"rg", "ag", "ack", "tar", "zip", "rsync", "cpio", "xargs", "fd", "find"}
)
_DYNAMIC_ENUM = re.compile(
    r"walk|glob|listdir|scandir|rglob|iterdir|readdir|find\b|ls\b|readFileSync|fs\.|Dir\.|Find",
    re.I,
)


def _words(command: str) -> list[str] | None:
    out, quote, escaped = [], "", False
    for ch in command:  # unquoted newlines separate commands
        if escaped:
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "\n":
            ch = " ; "
        out.append(ch)
    try:
        lexer = shlex.shlex("".join(out), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _word_is_sensitive(word: str) -> bool:
    candidates = {word, word.rsplit(":", 1)[-1], word.rsplit("=", 1)[-1], word.rsplit("<", 1)[-1]}
    return any(is_sensitive_path(c.strip("'\"")) for c in candidates if c)


def command_may_reach_secrets(
    command: str, sensitive: list[str], cwd_rel: str = ".", *, _depth: int = 0
) -> bool:
    """True if running `command` could print the contents of a secret file. When unsure, True.

    `sensitive` are the root-relative secret files that exist now. A command that names one
    literally, uses a glob that matches one, or reads recursively or dynamically while one exists,
    is treated as reaching it.
    """
    if _depth > 4:
        return bool(sensitive)
    # Words inside substitutions and backticks are commands too.
    nested = [a or b for a, b in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command)]
    if any(command_may_reach_secrets(n, sensitive, cwd_rel, _depth=_depth + 1) for n in nested):
        return True
    words = _words(command)
    if words is None:  # unbalanced quotes: look at the raw text
        words = re.split(r"[\s;|&<>()]+", command)
    words = [w for w in words if not w.startswith(":(exclude")]  # our own pathspec excludes
    if any(_word_is_sensitive(w) for w in words if ("." in w or "/" in w or "env" in w.lower())):
        return True
    if not sensitive:
        return False
    names = {s.lower() for s in sensitive} | {s.rsplit("/", 1)[-1].lower() for s in sensitive}
    for w in words:  # a glob that matches a secret file in the tree
        if any(c in w for c in "*?[") and not w.startswith(":(exclude"):
            pattern = w.lower().lstrip("./")
            if any(
                fnmatch.fnmatchcase(n, pattern)
                or fnmatch.fnmatchcase(n, pattern.rsplit("/", 1)[-1])
                for n in names
            ):
                return True
    if nested or "`" in command or "<(" in command:
        return True  # dynamic content: we cannot see what it names
    argv: list[str] = []
    for token in [*words, ";"]:
        if token and (
            token in ("&&", "||", "|", ";", "&", "|&", "(", ")") or set(token) <= set("&|;()")
        ):
            if argv and _argv_reaches(argv, sensitive, command, cwd_rel, _depth):
                return True
            argv = []
        else:
            argv.append(token)
    return False


def _argv_reaches(
    argv: list[str], sensitive: list[str], command: str, cwd_rel: str, depth: int
) -> bool:
    args = [a for a in argv if not re.fullmatch(r"[A-Za-z_]\w*=.*", a)] or argv
    exe = os.path.basename(args[0]) if args else ""
    rest = args[1:]
    if exe in _SHELLS:
        for i, a in enumerate(rest):
            if (a == "-c" or re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a)) and i + 1 < len(rest):
                return command_may_reach_secrets(rest[i + 1], sensitive, cwd_rel, _depth=depth + 1)
        return True  # a script file: we cannot see inside
    if exe in ("eval", "source", ".", "exec", "xargs", "env"):
        return True
    if exe in _INLINE and _INLINE[exe] in rest:
        body = " ".join(rest[rest.index(_INLINE[exe]) + 1 :])
        return any(_word_is_sensitive(w) for w in re.findall(r"[\w.\-/~]+", body)) or bool(
            _DYNAMIC_ENUM.search(body)
        )
    if exe in _RECURSIVE_READERS or (
        exe == "find" and any(a in ("-exec", "-execdir", "-ok") for a in rest)
    ):
        return True
    if exe in ("grep", "egrep", "fgrep", "ack") and any(
        a in ("-r", "-R", "--recursive", "-d") or re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", a)
        for a in rest
    ):
        return True
    if exe == "git":
        sub = next((a for a in rest if not a.startswith("-")), "")
        if sub in ("grep", "archive", "cat-file", "bundle", "fast-export", "ls-tree"):
            return True
    if exe == "cat" and any(c in "".join(rest) for c in "*?["):
        return True
    return False


# -- diffs ---------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


_SECTION = re.compile(r"^(?:diff (?:--git|--cc|--combined) .*|--- \S.*(?=\n\+\+\+ ))$", re.M)


def withhold_sensitive_hunks(text: str) -> str:
    """Replace the body of every per-file section of a diff whose path holds secrets.

    Understands git (`diff --git`, `--cc`, `--combined`, any prefix, none) and plain `---/+++`
    diffs. ANSI colour is removed first so a coloured header cannot hide a path. Masking of the
    values is the main defence; this is the second.
    """
    text = _ANSI.sub("", text)
    heads = list(_SECTION.finditer(text))
    if not heads:
        return text
    out, last = [text[: heads[0].start()]], 0
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[m.start() : end]
        header = body.split("\n@@", 1)[0]  # header lines only: ---, +++, rename, index ...
        paths = re.findall(
            r"[^\s]+",
            re.sub(
                r"^(?:diff \S+|---|\+\+\+|rename from|rename to|copy from|copy to)",
                " ",
                header,
                flags=re.M,
            ),
        )
        if any(_word_is_sensitive(p) for p in paths):
            out.append(m.group(0) + "\n[content withheld: sensitive path]\n")
        else:
            out.append(body)
        last = end
    out.append(text[last:])
    return "".join(out) if out else text
