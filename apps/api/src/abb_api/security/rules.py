"""Pure, bounded matchers behind the security detectors (ADR-131).

Everything here reads hostile text (commands, paths, URLs, SQL an agent produced) and answers a
yes/no question about it. The functions never return the text: callers get booleans, enum members
and short fixed identifiers, so a finding built from them cannot carry content.

Cost model, so that no input makes a detector slow (INV-4, ReDoS tests in `test_security_rules.py`):

- Input is cut to `MAX_TEXT_CHARS` (head and tail of the string) before anything else.
- The tokenizer is a single left-to-right pass; it never backtracks and never recurses beyond
  `MAX_SHELL_DEPTH` nested `sh -c` levels, and it stops after `MAX_TOKENS` tokens.
- Regular expressions are small, anchored on a literal keyword and use bounded quantifiers only;
  nothing here uses an unbounded `.*` or nested quantifiers.
- Detection is pattern based. An obfuscated command (`$(echo cm0= | base64 -d) -rf /`) is not seen;
  ADR-131 and KI-132 say so.
"""

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

MAX_TEXT_CHARS = 16 * 1024  # of a command or SQL text that is analysed (head + tail)
_TAIL_CHARS = 4 * 1024
MAX_TOKENS = 2000
MAX_SEGMENTS = 400
MAX_SHELL_DEPTH = 3
MAX_PATH_CHARS = 1024


def bounded(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """The head and the tail of a long text: a hostile string cannot hide a command behind padding
    without the tail still being read, and the work stays linear in `limit`."""
    if len(text) <= limit:
        return text
    return text[: limit - _TAIL_CHARS] + "\n" + text[-_TAIL_CHARS:]


# ----------------------------------------------------------------------------- shell tokenizer

_SEPARATORS = frozenset(";&|\n()`")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}=")
_WRAPPERS: dict[str, frozenset[str]] = {
    # wrapper -> options that take a value (skipped with it)
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "-S"}),
    "nohup": frozenset(),
    "command": frozenset(),
    "exec": frozenset(),
    "builtin": frozenset(),
    "time": frozenset({"-f", "-o"}),
    "nice": frozenset({"-n"}),
    "ionice": frozenset({"-c", "-n"}),
    "setsid": frozenset(),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
    "timeout": frozenset({"-s", "-k"}),
    "watch": frozenset({"-n", "-d"}),
    "xargs": frozenset({"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"}),
    "chroot": frozenset(),
}
_LEAD_WORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "fi", "done"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


@dataclass(frozen=True)
class Segment:
    """One simple command: its program (basename, lower case) and its arguments."""

    program: str
    args: tuple[str, ...]
    redirects_in: tuple[str, ...] = ()  # targets of `< file`
    redirects_out: tuple[str, ...] = ()  # targets of `> file` / `>> file`


def _tokens(text: str) -> list[list[str]]:
    """Split into simple commands of tokens. Quotes are removed, `\\` escapes one character.
    Unquoted `<` and `>` become their own tokens so that redirect targets can be found.
    One pass over at most `MAX_TEXT_CHARS`; `$(`, backticks and parentheses start a new command."""
    text = bounded(text)
    segments: list[list[str]] = []
    current: list[str] = []
    buf: list[str] = []
    in_token = False
    quote = ""
    total = 0

    def flush_token() -> None:
        nonlocal in_token, total
        if in_token:
            current.append("".join(buf))
            total += 1
        buf.clear()
        in_token = False

    def flush_segment() -> None:
        nonlocal current
        flush_token()
        if current:
            segments.append(current)
        current = []

    i, n = 0, len(text)
    while i < n and total < MAX_TOKENS and len(segments) < MAX_SEGMENTS:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"' and i + 1 < n and text[i + 1] in '"\\$`':
                buf.append(text[i + 1])
                i += 1
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            in_token = True
        elif ch == "\\" and i + 1 < n:
            buf.append(text[i + 1])
            in_token = True
            i += 1
        elif ch == "$" and i + 1 < n and text[i + 1] == "(":
            flush_segment()
            i += 1  # the parenthesis is skipped by the separator rule next round
        elif ch in _SEPARATORS:
            flush_segment()
        elif ch in " \t\r":
            flush_token()
        elif ch in "<>":
            flush_token()
            j = i
            while j + 1 < n and text[j + 1] == ch and j - i < 2:
                j += 1
            current.append(text[i : j + 1])
            total += 1
            i = j
        elif ch == "#" and not in_token:
            while i < n and text[i] != "\n":  # a comment runs to the end of the line
                i += 1
            continue
        else:
            buf.append(ch)
            in_token = True
        i += 1
    flush_segment()
    return segments


def _strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop `VAR=x`, `sudo -u root`, `env`, `time` ... in front of the real program."""
    rest = list(tokens)
    for _ in range(8):
        while rest and (rest[0] in _LEAD_WORDS or _ASSIGNMENT.match(rest[0])):
            rest.pop(0)
        if not rest:
            return rest
        name = rest[0].rsplit("/", 1)[-1].lower()
        value_options = _WRAPPERS.get(name)
        if value_options is None:
            return rest
        rest.pop(0)
        while rest and rest[0].startswith("-") and rest[0] != "--":
            option = rest.pop(0)
            if option in value_options and rest:
                rest.pop(0)
        if rest and rest[0] == "--":
            rest.pop(0)
        if (
            name in ("timeout", "nice", "ionice")
            and rest
            and re.fullmatch(r"[0-9.]+[smhd]?", rest[0])
        ):
            rest.pop(0)  # `timeout 5 cmd`
    return rest


def segments_of(command: str, _depth: int = 0) -> list[Segment]:
    """The simple commands of a shell command line, including those inside `sh -c '...'`."""
    found: list[Segment] = []
    for tokens in _tokens(command):
        tokens = _strip_wrappers(tokens)
        if not tokens:
            continue
        args: list[str] = []
        redirects_in: list[str] = []
        redirects_out: list[str] = []
        rest = tokens[1:]
        k = 0
        while k < len(rest):
            arg = rest[k]
            if arg in ("<", "<<", "<<<") and k + 1 < len(rest):
                if arg == "<":
                    redirects_in.append(rest[k + 1])
                k += 2
                continue
            if arg in (">", ">>", ">|") and k + 1 < len(rest):
                redirects_out.append(rest[k + 1])
                k += 2
                continue
            if arg in ("<", ">", ">>", "<<", "<<<", ">|"):
                k += 1
                continue
            args.append(arg)
            k += 1
        program = tokens[0].rsplit("/", 1)[-1].lower()
        segment = Segment(program, tuple(args), tuple(redirects_in), tuple(redirects_out))
        found.append(segment)
        if program in _SHELLS and _depth < MAX_SHELL_DEPTH:
            for index, arg in enumerate(segment.args):
                if re.fullmatch(r"-[a-z]*c[a-z]*", arg) and index + 1 < len(segment.args):
                    found.extend(segments_of(segment.args[index + 1], _depth + 1))
                    break
        if len(found) >= MAX_SEGMENTS:
            break
    return found


# ------------------------------------------------------------------------------ secret files

# Name parts that mark a file as a template, never as holding secrets.
_TEMPLATE_PARTS = frozenset(
    {
        "example",
        "sample",
        "template",
        "dist",
        "tpl",
        "defaults",
        "default",
        "schema",
        "pub",
        "baseline",
    }
)
_KEY_BASENAMES = frozenset(
    {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ecdsa_sk", "id_ed25519_sk"}
)
_KEY_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk")
_PUBLIC_CERT_PREFIXES = (
    "cacert",
    "ca-bundle",
    "ca-certificates",
    "ca_bundle",
    "fullchain",
    "chain",
)
_ALWAYS_SECRET_BASENAMES = frozenset(
    {
        ".netrc",
        "_netrc",
        ".pypirc",
        ".git-credentials",
        ".pgpass",
        ".htpasswd",
        ".dockercfg",
        "kubeconfig",
        "credentials.json",
        "application_default_credentials.json",
        "secring.gpg",
    }
)
_SECRETS_STEMS = frozenset({"secrets", "secret"})
_SECRETS_EXTENSIONS = frozenset(
    {"yml", "yaml", "json", "toml", "env", "txt", "ini", "properties", "conf", "cfg"}
)
_HOME_PREFIXES = ("~", "$home", "${home}", "/home/", "/users/", "/root")


def is_secret_path(path: str) -> bool:
    """Does this path name a file that usually holds secrets? Case-insensitive, name based.

    `.env` and `.env.<anything but a template word>`, private keys and certificates with a private
    key suffix, cloud and registry credential files, `secrets.*`. Template files
    (`.env.example`, `secrets.sample.yml`) are not secret files. A project `.npmrc` mostly holds
    registry settings, so only the one in a home directory (where tokens live) counts."""
    if not path or len(path) > 8 * MAX_PATH_CHARS:
        return False
    lowered = path[-MAX_PATH_CHARS:].lower().replace("\\", "/").strip().strip("'\"")
    parts = [p for p in lowered.split("/") if p not in ("", ".")]
    if not parts:
        return False
    name = parts[-1]
    parents = parts[:-1]
    if any(p in _TEMPLATE_PARTS for p in name.split(".")[1:]) and name != ".env":
        return False
    if name == ".env" or (name.startswith(".env.") and len(name) > 5):
        return True
    if name in _KEY_BASENAMES:
        return True
    if name.endswith(_KEY_SUFFIXES) and not name.startswith(_PUBLIC_CERT_PREFIXES):
        return True
    if name in _ALWAYS_SECRET_BASENAMES:
        return True
    if name == "credentials" and parents and parents[-1] == ".aws":
        return True
    if name == "config" and parents and parents[-1] == ".kube":
        return True
    if name == "config.json" and parents and parents[-1] == ".docker":
        return True
    if name == ".npmrc":
        return len(parts) <= 3 and lowered.startswith(_HOME_PREFIXES) and ".." not in parts
    if name.startswith("client_secret") and name.endswith(".json"):
        return True
    if name.startswith(("service-account", "service_account", "serviceaccount")) and name.endswith(
        ".json"
    ):
        return True
    if ".gnupg" in parents and name != "gpg.conf":
        return True
    stem, _, extension = name.partition(".")
    return stem in _SECRETS_STEMS and extension in _SECRETS_EXTENSIONS


_READERS = frozenset(
    """cat tac less more head tail bat nl od xxd hexdump strings base64 grep egrep fgrep rg ag awk
    sed cp scp rsync tar zip gzip source . sort cut diff vim vi nano emacs open code xclip pbcopy
    openssl gpg jq yq""".split()
)
_PATTERN_FIRST = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "sed", "awk", "jq", "yq"})
_PATTERN_OPTIONS = frozenset({"-e", "--regexp", "-f", "--file"})
_SOURCES_ONLY = frozenset({"cp", "scp", "rsync"})  # the last operand is where the copy goes
_UPLOADERS = frozenset({"curl", "wget", "http", "https", "xh"})


def _file_operands(segment: Segment) -> list[str]:
    """The arguments of a reader that name files it reads (patterns and destinations excluded)."""
    program = segment.program
    args = list(segment.args)
    operands: list[str] = []
    has_pattern_option = False
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if program in _PATTERN_FIRST and arg in _PATTERN_OPTIONS:
            has_pattern_option = True
            skip = True
            continue
        if not arg.startswith("-"):
            operands.append(arg)
    if program in _PATTERN_FIRST and not has_pattern_option and operands:
        operands = operands[1:]  # `grep PATTERN file`: the pattern is not a file
    if program in _SOURCES_ONLY and len(operands) >= 2:
        operands = operands[:-1]
    if program in ("tar", "zip") and len(operands) >= 2:
        operands = operands[1:]  # the first operand is the archive being written
    return operands


def reads_secret_file(command: str) -> bool:
    """Does the shell command read or ship a secret-bearing file (`cat .env`, `scp ~/.ssh/id_rsa
    host:`, `source .env`, `curl -d @.env`, `< .env`)? Listing, testing, removing or staging a file
    (`ls .env`, `git add .env`) is not reading it, and neither is copying a template onto it
    (`cp .env.example .env`)."""
    for segment in segments_of(command):
        if any(is_secret_path(target) for target in segment.redirects_in):
            return True
        if segment.program in _READERS and any(
            is_secret_path(operand) for operand in _file_operands(segment)
        ):
            return True
        if segment.program in _UPLOADERS:
            for arg in segment.args:
                at = arg.find("@")
                if at != -1 and is_secret_path(arg[at + 1 :]):
                    return True
    return False


# ---------------------------------------------------------------------------------- destructive


class Destructive(IntEnum):
    NONE = 0
    R3 = 3
    R4 = 4


_SYSTEM_DIRS = frozenset(
    {"/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64", "/boot", "/home", "/root", "/opt",
     "/sys", "/proc", "/dev", "/users", "/system", "/library", "/applications"}
)  # fmt: skip
_WIPE_TARGETS = frozenset(
    """/ /* ~ ~/ ~/* $home $home/ $home/* ${home} ${home}/ ${home}/* * ./* . ./ .. ../ ../*
    .*""".split()
)
_SQL_CLIENTS = frozenset(
    {"psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "sqlplus", "usql", "pgcli", "mycli",
     "clickhouse-client", "duckdb", "cockroach", "mongosh", "mongo", "redis-cli", "bq", "snowsql",
     "isql", "pg_dump", "mysqladmin", "dropdb", "createdb"}
)  # fmt: skip
_DROP_DATABASE = re.compile(r"\bdrop\s{1,16}(?:database|schema)\b")
_DROP_TABLE = re.compile(r"\bdrop\s{1,16}table\b")


def _flags(args: Iterable[str]) -> str:
    return "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))


def _norm_target(target: str) -> str:
    return target.strip().lower().rstrip("/") if len(target) > 1 else target


def _is_wipe_target(target: str) -> bool:
    lowered = target.lower()
    if lowered in _WIPE_TARGETS:
        return True
    stripped = lowered.rstrip("/")
    if stripped in _SYSTEM_DIRS or (stripped.endswith("/*") and stripped[:-2] in _SYSTEM_DIRS):
        return True
    return False


def has_sql_client(segments: list[Segment]) -> bool:
    """Is a database client invoked (anywhere in the line, `docker exec db psql` and pipes too)?"""
    return any(
        s.program in _SQL_CLIENTS or any(a.rsplit("/", 1)[-1] in _SQL_CLIENTS for a in s.args[:6])
        for s in segments
    )


def destructive_level(command: str) -> Destructive:
    """The strongest unambiguous destructive pattern in the command, else `NONE`.

    R4: recursive delete of `/`, `~`, `*`, `.`, `..` or a system directory; `mkfs`; `dd of=/dev/..`;
    a write to a block device; a fork bomb; `DROP DATABASE/SCHEMA` through a database client;
    `--no-preserve-root`. R3: `git clean` with force and ignored files (`-fdx`), `chmod -R` on `/`,
    `DROP TABLE` through a database client. A single file removed (`rm a.txt`), `rm -rf build` and
    `git reset --hard` are not in this set."""
    raw = bounded(command)
    lowered = raw.lower()
    level = Destructive.NONE
    if ":(){" in lowered.replace(" ", "") or re.search(r":\(\)\s{0,8}\{", lowered):
        return Destructive.R4
    segments = segments_of(raw)
    for segment in segments:
        program, args = segment.program, segment.args
        if "--no-preserve-root" in args:
            return Destructive.R4
        if program == "rm":
            flags = _flags(args)
            recursive = "r" in flags.lower() or "--recursive" in args
            positional = [a for a in args if not a.startswith("-")]
            if recursive and any(_is_wipe_target(t) for t in positional):
                return Destructive.R4
        elif program.startswith("mkfs") or program == "dropdb":
            return Destructive.R4
        elif program in ("wipefs", "shred") and any(a.startswith("/dev/") for a in args):
            return Destructive.R4
        elif program == "dd" and any(a.startswith("of=/dev/") for a in args):
            return Destructive.R4
        elif program == "git":
            rest = [a for a in args if not a.startswith("-")]
            if rest[:1] == ["clean"]:
                flags = _flags(a for a in args if a != "clean")
                if ("f" in flags or "--force" in args) and ("x" in flags or "X" in flags):
                    level = max(level, Destructive.R3)
        elif program in ("chmod", "chown", "chgrp"):
            flags = _flags(args)
            positional = [a for a in args if not a.startswith("-")]
            if "R" in flags and any(_norm_target(t) in ("/", "/*") for t in positional):
                level = max(level, Destructive.R3)
        if any(re.match(r"/dev/(sd|nvme|disk|hd|vd|xvd)", t) for t in segment.redirects_out):
            return Destructive.R4
    if has_sql_client(segments):
        if _DROP_DATABASE.search(lowered):
            return Destructive.R4
        if _DROP_TABLE.search(lowered):
            level = max(level, Destructive.R3)
    return level


# ------------------------------------------------------------------------------------------ git

PROTECTED_EXACT = frozenset({"main", "master", "production"})
PROTECTED_PREFIXES = ("release/",)
_HEX_SHA = re.compile(r"[0-9a-f]{7,40}")
_REWIND = re.compile(r"(?:head|@)(?:[~^][0-9]*)+")


def is_protected_branch(name: str) -> bool:
    lowered = name.strip().lower()
    if lowered.startswith("refs/heads/"):
        lowered = lowered[len("refs/heads/") :]
    lowered = lowered.removeprefix("origin/").removeprefix("upstream/")
    return lowered in PROTECTED_EXACT or lowered.startswith(PROTECTED_PREFIXES)


class GitRule(IntEnum):
    """Which risky-git rule matched; lower number = stronger, used to pick the stated rule."""

    FORCE_PUSH = 1
    PROTECTED_DELETE = 2
    PROTECTED_PUSH = 3
    HISTORY_REWRITE = 4


_GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _git_args(args: tuple[str, ...]) -> tuple[str, list[str]]:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in _GIT_GLOBAL_WITH_VALUE and rest:
            rest.pop(0)
    return (rest[0].lower(), rest[1:]) if rest else ("", [])


def git_rules(command: str) -> set[GitRule]:
    """Risky git operations in a shell command: force pushes, pushes to or deletes of a protected
    branch name, history rewrites (`filter-branch`, `reset --hard HEAD~n`, `rebase -i`)."""
    found: set[GitRule] = set()
    for segment in segments_of(command):
        if segment.program != "git":
            continue
        sub, rest = _git_args(segment.args)
        options = [a for a in rest if a.startswith("-")]
        positional = [a for a in rest if not a.startswith("-")]
        if sub == "push":
            force_flag = any(o in ("--force", "-f") for o in options) or any(
                re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", o) for o in options
            )
            lease = any(
                o == "--force-with-lease" or o.startswith("--force-with-lease=") for o in options
            )
            delete = any(o in ("--delete", "-d") for o in options)
            refspecs = positional[1:] if len(positional) >= 2 else []
            targets: list[str] = []
            plus = False
            colon_delete = False
            for spec in refspecs:
                if spec.startswith("+"):
                    plus = True
                    spec = spec[1:]
                if spec.startswith(":"):
                    colon_delete = True
                targets.append(spec.rsplit(":", 1)[-1])
            protected_target = any(is_protected_branch(t) for t in targets if t)
            if force_flag or plus:
                found.add(GitRule.FORCE_PUSH)
            elif lease and protected_target:
                found.add(GitRule.FORCE_PUSH)
            if (delete or colon_delete) and protected_target:
                found.add(GitRule.PROTECTED_DELETE)
            elif protected_target:
                found.add(GitRule.PROTECTED_PUSH)
        elif sub == "branch":
            if any(o in ("-d", "-D", "--delete") for o in options) and any(
                is_protected_branch(p) for p in positional
            ):
                found.add(GitRule.PROTECTED_DELETE)
        elif sub in ("filter-branch", "filter-repo"):
            found.add(GitRule.HISTORY_REWRITE)
        elif sub == "reflog" and positional[:1] == ["expire"]:
            found.add(GitRule.HISTORY_REWRITE)
        elif sub == "reset" and "--hard" in options:
            target = positional[0].lower() if positional else ""
            if _REWIND.fullmatch(target) or _HEX_SHA.fullmatch(target):
                found.add(GitRule.HISTORY_REWRITE)
        elif sub == "rebase" and any(o in ("-i", "--interactive") for o in options):
            found.add(GitRule.HISTORY_REWRITE)
    return found


def is_protected_push_target(push_target: str | None, branch: str | None) -> bool:
    """For a `git.push` event: `git.push_target` is "<remote> <branch>", `git.branch` the branch."""
    candidates = []
    if branch:
        candidates.append(branch)
    if push_target:
        candidates.append(push_target.strip().split(" ")[-1])
    return any(is_protected_branch(c) for c in candidates if c and len(c) <= 256)


# ------------------------------------------------------------------------------------------ SQL

_DDL = re.compile(
    r"\b(?:create|alter|drop|truncate)\s{1,16}(?:or\s{1,16}replace\s{1,16})?"
    r"(?:(?:temp|temporary|unique|materialized|external|global|local)\s{1,16}){0,2}"
    r"(?:table|database|schema|index|view|role|user|type|function|procedure|trigger|sequence|"
    r"extension|column|constraint|tablespace|domain|policy|publication|subscription)\b"
)
_TRUNCATE = re.compile(r"\btruncate\s{1,16}(?:table\s{1,16})?[a-z_\"`\[]")
_DELETE = re.compile(r"\bdelete\s{1,16}from\s{1,16}[\w.\"`\[\]]{1,128}")
_UPDATE = re.compile(r"\bupdate\s{1,16}[\w.\"`\[\]]{1,128}\s{1,16}set\b")
_WHERE = re.compile(r"\bwhere\b")
_STATEMENT_WINDOW = 4000


def _statement_after(lowered: str, end: int) -> str:
    stop = lowered.find(";", end, end + _STATEMENT_WINDOW)
    return lowered[end : stop if stop != -1 else end + _STATEMENT_WINDOW]


def sql_is_privileged(sql: str) -> bool:
    """DDL (CREATE/ALTER/DROP/TRUNCATE), or DELETE/UPDATE with no WHERE in the same statement."""
    lowered = bounded(sql).lower()
    if _DDL.search(lowered) or _TRUNCATE.search(lowered):
        return True
    for pattern in (_DELETE, _UPDATE):
        for match in pattern.finditer(lowered):
            if not _WHERE.search(_statement_after(lowered, match.end())):
                return True
    return False


_REDIS_FLUSH = re.compile(r"\bflush(?:all|db)\b|\bdropdatabase\s{0,4}\(")


def command_runs_privileged_sql(command: str) -> bool:
    """A database client in the command line together with privileged SQL (or a Redis flush)."""
    raw = bounded(command)
    segments = segments_of(raw)
    if not has_sql_client(segments):
        return False
    return sql_is_privileged(raw) or bool(_REDIS_FLUSH.search(raw.lower()))


def event_sql_is_privileged(operation: str | None, statement: str | None) -> bool:
    """For `db.*` events: `db.operation` names the verb; a statement attribute is read in full."""
    if operation:
        verb = operation.strip().split(" ", 1)[0].lower()[:32]
        if verb in ("create", "alter", "drop", "truncate"):
            return True
    return bool(statement) and sql_is_privileged(statement or "")


# ------------------------------------------------------------------------------------- hosts

_SCHEME_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,15})://([^\s/?#'\"<>]{1,300})")
# Schemes that name a network host. `file://`, `s3://bucket` and the like do not.
_NETWORK_SCHEMES = frozenset(
    {"http", "https", "ftp", "ftps", "sftp", "ssh", "git", "git+ssh", "ssh+git", "ws", "wss", "tcp",
     "udp", "postgres", "postgresql", "mysql", "redis", "rediss", "amqp", "amqps", "mongodb",
     "mongodb+srv", "smtp", "smtps"}
)  # fmt: skip
_HOST_ONLY = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?){0,10}"
)
_USER_AT_HOST = re.compile(r"(?:[A-Za-z0-9._-]{1,64}@)?([A-Za-z0-9.-]{1,253}):(?!//)")
_COMMON_TLDS = frozenset(
    """com net org io dev ai app co cloud sh xyz info biz edu gov us uk de fr nl eu ca au in jp cn
    ru br me tv cc ly so to run page tech site online store""".split()
)
_NETWORK_PROGRAMS = frozenset({"curl", "wget", "http", "https", "xh", "httpie", "aria2c"})
_CURL_VALUE_OPTIONS = frozenset(
    """-o -O -H -d -X -u -A -e -F -T -x -b -c -w -K -m -r -y -z --output --header --data --data-raw
    --data-binary --request --user --user-agent --referer --form --upload-file --proxy --cookie
    --cookie-jar --write-out --config --max-time --connect-timeout --retry --cacert --cert --key
    --output-dir -P -i -I --directory-prefix --post-data --post-file -t --tries -U -a
    --output-document""".split()
)
_SSH_VALUE_OPTIONS = frozenset(
    {"-i", "-p", "-l", "-o", "-F", "-J", "-L", "-R", "-D", "-b", "-c", "-m", "-S", "-W", "-E", "-P",
     "-e"}
)  # fmt: skip
# Programs whose URL arguments are destinations. An `echo` or a commit message that mentions a URL
# does not contact it.
_URL_PROGRAMS = frozenset(
    """curl wget http https xh httpie aria2c git pip pip3 uv poetry npm pnpm yarn docker podman helm
    kubectl gh nc ncat telnet ssh scp sftp rsync ftp lynx w3m socat""".split()
)
_GIT_REMOTE_SUBCOMMANDS = frozenset(
    {"clone", "fetch", "pull", "push", "remote", "submodule", "ls-remote"}
)
_ALWAYS_LOCAL_SUFFIXES = (".localhost",)
_LOCAL_NAMES = frozenset({"localhost", "0.0.0.0", "::1", "::"})  # noqa: S104 - compared, not bound


def normalize_host(raw: str) -> str | None:
    """A lower-case host name or IP from `user:pw@host:port`, or None if it is not a plain host
    (an unexpanded `$VAR` or a glob cannot be judged)."""
    host = raw.strip().lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if host.startswith("["):  # [::1]:8080
        end = host.find("]")
        host = host[1:end] if end != -1 else host[1:]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.rstrip(".")
    if not host or len(host) > 253:
        return None
    if ":" in host:  # IPv6 literal
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
        return host
    return host if _HOST_ONLY.fullmatch(host) else None


def is_local_host(host: str) -> bool:
    if host in _LOCAL_NAMES or host.endswith(_ALWAYS_LOCAL_SUFFIXES):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    """`host` is local or matches an allowlist entry: an exact name, or `*.example.com` for any
    subdomain of example.com (the apex needs its own entry)."""
    if is_local_host(host):
        return True
    for pattern in allowlist:
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]) and len(host) > len(pattern) - 1:
                return True
        elif host == pattern:
            return True
    return False


def _looks_like_bare_host(token: str) -> str | None:
    """`example.com/path` as curl accepts it, but not `out.txt`: the last label must be a TLD."""
    head = re.split(r"[/?#]", token, maxsplit=1)[0]
    host = normalize_host(head)
    if host is None or "." not in host:
        return None
    if host.rsplit(".", 1)[1] in _COMMON_TLDS or re.fullmatch(r"[0-9.]+", host):
        return host
    return None


def hosts_in_url(url: str) -> list[str]:
    """Hosts of the URLs in a string attribute (`http.url`)."""
    hosts: list[str] = []
    for match in _SCHEME_URL.finditer(url[:4096]):
        if match.group(1).lower() not in _NETWORK_SCHEMES:
            continue
        host = normalize_host(match.group(2))
        if host:
            hosts.append(host)
        if len(hosts) >= 8:
            break
    return hosts


def hosts_in_command(command: str) -> list[str]:
    """Hosts a shell command contacts or names as a remote: URLs in any argument, plus the
    positional host of curl/wget, ssh/scp/rsync/sftp (`[user@]host`, `host:path`), `nc`/`telnet`,
    and `git@host:org/repo` remotes. Environment variables and globs are not hosts."""
    hosts: list[str] = []
    for segment in segments_of(command):
        program, args = segment.program, segment.args
        if program == "git":
            if _git_args(args)[0] not in _GIT_REMOTE_SUBCOMMANDS:
                continue
        elif program not in _URL_PROGRAMS:
            continue
        for arg in args:
            hosts.extend(hosts_in_url(arg))
        if program in _NETWORK_PROGRAMS:
            skip = False
            for arg in args:
                if skip:
                    skip = False
                    continue
                if arg in _CURL_VALUE_OPTIONS:
                    skip = True
                    continue
                if arg.startswith("-") or "://" in arg:
                    continue
                bare = _looks_like_bare_host(arg)
                if bare:
                    hosts.append(bare)
        elif program in ("ssh", "scp", "sftp", "rsync", "ssh-copy-id"):
            skip = False
            seen_host = False
            for arg in args:
                if skip:
                    skip = False
                    continue
                if arg in _SSH_VALUE_OPTIONS:
                    skip = True
                    continue
                if arg.startswith("-") or arg.startswith(("/", ".", "~")):
                    continue
                if program in ("ssh", "sftp", "ssh-copy-id"):
                    if not seen_host:
                        host = normalize_host(arg.split(":", 1)[0] if "@" not in arg else arg)
                        if host:
                            hosts.append(host)
                        seen_host = True
                    continue
                match = _USER_AT_HOST.match(arg)
                if match and ("@" in arg or "." in match.group(1)):
                    host = normalize_host(match.group(1))
                    if host:
                        hosts.append(host)
        elif program in ("nc", "ncat", "netcat", "telnet", "socat"):
            positional = [a for a in args if not a.startswith("-")]
            if positional and program != "socat":
                host = normalize_host(positional[0])
                if host and not positional[0].isdigit():
                    hosts.append(host)
        elif program == "git":
            for arg in args:
                match = _USER_AT_HOST.match(arg)
                if match and "@" in arg and "://" not in arg:
                    host = normalize_host(match.group(1))
                    if host:
                        hosts.append(host)
        if len(hosts) >= 64:
            break
    return hosts
