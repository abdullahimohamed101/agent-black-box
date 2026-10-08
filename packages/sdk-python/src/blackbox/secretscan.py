"""Secret values from the workspace, and shell-aware checks for commands that could print them.

A blocklist of command shapes can never be complete (`cat .env`, `cat<.env`, `bash -c`, `grep -r`,
`git show HEAD:.env` ...), so the design is to know the secret *values* and mask them wherever they
appear, however a command read them (ADR-031). Path-based withholding stays as defence in
depth, and is shell-aware: when a command could reach a secret file and we are unsure, its output is
withheld.

Best effort. It cannot find a secret that is in no sensitive file and not in the environment.
"""

import base64
import fnmatch
import json
import os
import re
import shlex
import subprocess
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    ".docker/config.json", ".kube/config", ".aws/credentials", ".aws/config",
)  # fmt: skip
_CREDENTIAL_FILES = (".pgpass", ".htpasswd", ".git-credentials", ".netrc", "_netrc")
_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", "dist", "build", ".next",
     ".mypy_cache", ".ruff_cache", ".pytest_cache", "target", ".cache", "site-packages"}
)  # fmt: skip
_PATH = os.environ.get("PATH", os.defpath)
MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 400
MAX_VISITED = 20_000
MAX_VALUES = 3000
MAX_VALUE_LEN = 4096
_SECRET_KEY = re.compile(
    r"key|token|secret|pass|pwd|credential|private|auth|dsn|cookie|session|signature|salt"
    r"|database_url|connection|bearer|(?:^|[_.-])pat(?:$|[_.-])",
    re.I,
)
_LINE = re.compile(r"^\s*(?:export\s+|set\s+)?([A-Za-z_][A-Za-z0-9_.\-/]*)\s*[=:]\s*(.*?)\s*$")
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(.*?)(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S
)
_URL_CRED = re.compile(r"://([^/\s:@]*):([^/\s@]+)@")
_CRED_ASSIGN = re.compile(
    r"(?i)(?:_authtoken|_auth|_password|password|passwd|token|secret)\s*[=:]\s*['\"]?([^\s'\"]+)"
)
_NETRC = re.compile(r"\b(?:password|login|account|token)\s+(\S+)")


def is_sensitive_path(path: str) -> bool:
    """Does this path name a file that holds secrets? Case-insensitive; also by directory."""
    lowered = path.replace("\\", "/").lower().strip()
    name = lowered.rsplit("/", 1)[-1]
    if any(fnmatch.fnmatchcase(name, pattern) for pattern in SENSITIVE_NAMES):
        return True
    return any(lowered == s or lowered.endswith("/" + s) for s in SENSITIVE_SUFFIXES)


def git_excludes() -> list[str]:
    """Pathspecs that keep secret-holding files out of a git command (any directory, any case)."""
    return [f":(exclude,icase,glob)**/{pattern}" for pattern in SENSITIVE_NAMES] + [
        f":(exclude,icase,glob)**/{suffix}" for suffix in SENSITIVE_SUFFIXES
    ]


def _looks_secret_value(value: str, key_is_secret: bool) -> bool:
    if len(value) > MAX_VALUE_LEN or "\n" in value:
        return False
    if key_is_secret:
        return len(value) >= 3
    # A value of a non-secret-looking key in a secret file ("localhost", "8000", "true") is not
    # worth masking everywhere; a token-looking one is.
    return len(value) >= 8 and any(c.isdigit() or c in "-_/+=:@." for c in value)


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"`":
        return raw[1:-1]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip().strip("'\"")


def _json_leaves(node: Any, secret_key: bool, out: set[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            _json_leaves(v, secret_key or bool(_SECRET_KEY.search(str(k))), out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _json_leaves(v, secret_key, out, depth + 1)
    elif isinstance(node, str) and _looks_secret_value(node, secret_key):
        out.add(node)


def extract_values(path: str, text: str) -> set[str]:
    """The secret values in one sensitive file's text."""
    values: set[str] = set()
    name = path.replace("\\", "/").lower().rsplit("/", 1)[-1]
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            _json_leaves(json.loads(stripped), False, values)
        except ValueError:
            pass
    for block in _PEM.findall(text):
        body = [line.strip() for line in block.splitlines() if line.strip()]
        values.update(line for line in body if len(line) >= 16)
        if body:
            values.add("".join(body)[:MAX_VALUE_LEN])
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            key, value = m.group(1), _unquote(m.group(2))
            if value and _looks_secret_value(value, bool(_SECRET_KEY.search(key))):
                values.add(value)
        values.update(v for v in _CRED_ASSIGN.findall(line) if len(v) >= 3)
        for cred in _URL_CRED.finditer(line):
            values.add(cred.group(2))
        if name in _CREDENTIAL_FILES and line.strip() and not line.lstrip().startswith("#"):
            token = line.strip()
            if len(token) >= 8:
                values.add(token)
            if name == ".pgpass":
                values.add(token.rsplit(":", 1)[-1])
            if name == ".htpasswd" and ":" in token:
                values.add(token.split(":", 1)[1])
        values.update(_NETRC.findall(line) if name in (".netrc", "_netrc") else [])
    return {v for v in values if v and len(v) <= MAX_VALUE_LEN}


def derived_forms(value: str) -> set[str]:
    """The value as it may appear encoded: base64 (both alphabets/no padding), URL-encoded."""
    forms = {value}
    raw = value.encode("utf-8", errors="ignore")
    if 6 <= len(raw) <= 1024:
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            b64 = encode(raw).decode()
            forms.update({b64, b64.rstrip("=")})
    quoted = urllib.parse.quote(value, safe="")
    if quoted != value:
        forms.add(quoted)
    return forms


@dataclass
class SecretFiles:
    """Finds sensitive files under a root and keeps the set of their secret values current."""

    root: Path
    paths: list[str] = field(default_factory=list)  # root-relative, posix
    _cache: dict[str, tuple[int, int, frozenset[str]]] = field(default_factory=dict)
    _git: frozenset[str] = frozenset()
    _git_state: str = ""

    def _walk(self) -> list[Path]:
        found: list[Path] = []
        visited = 0
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in filenames:
                visited += 1
                full = Path(dirpath) / filename
                rel = full.relative_to(self.root).as_posix()
                if is_sensitive_path(rel):
                    found.append(full)
            if visited > MAX_VISITED or len(found) >= MAX_FILES:
                break
        return found[:MAX_FILES]

    def _git_values(self, rels: Iterable[str]) -> set[str]:
        """Older versions of tracked secret files (HEAD, index, stashes): `git show HEAD:.env`."""
        values: set[str] = set()
        if not (self.root / ".git").exists():
            return values
        refs = ["HEAD:", ":"] + [f"stash@{{{i}}}:" for i in range(3)]
        budget = 24
        for rel in list(rels)[:8]:
            for ref in refs:
                if budget <= 0:
                    return values
                budget -= 1
                try:
                    done = subprocess.run(  # noqa: S603
                        ["git", "show", f"{ref}{rel}"],  # noqa: S607
                        cwd=self.root, capture_output=True, timeout=5, check=False,
                        stdin=subprocess.DEVNULL,
                        env={"PATH": _PATH, "GIT_TERMINAL_PROMPT": "0"},
                    )  # fmt: skip
                except (OSError, subprocess.SubprocessError):
                    continue
                if done.returncode == 0 and len(done.stdout) <= MAX_FILE_BYTES:
                    values |= extract_values(rel, done.stdout.decode("utf-8", errors="replace"))
        return values

    def refresh(self) -> tuple[str, ...]:
        """Rescan (cached by size and mtime) and return all secret values, longest first."""
        files = self._walk()
        self.paths = [f.relative_to(self.root).as_posix() for f in files]
        live: dict[str, tuple[int, int, frozenset[str]]] = {}
        all_values: set[str] = set()
        for full, rel in zip(files, self.paths, strict=True):
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
            all_values |= values
        self._cache = live
        state = ",".join(f"{r}:{live[r][0]}" for r in sorted(live))
        if state != self._git_state:  # history lookups only when a secret file changed
            self._git, self._git_state = frozenset(self._git_values(self.paths)), state
        all_values |= self._git
        expanded: set[str] = set()
        for value in all_values:
            expanded |= derived_forms(value)
            if len(expanded) > MAX_VALUES * 4:
                break
        return tuple(sorted((v for v in expanded if len(v) >= 3), key=len, reverse=True))[
            :MAX_VALUES
        ]


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
