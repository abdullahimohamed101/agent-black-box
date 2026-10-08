"""Coding-agent capture (ADR-031): file, git and shell events with secret-safe output.

`CodingRecorder` wraps the operations a coding agent performs on a working directory and records
them as typed events. Contents never go into events: diffs and terminal output are redacted and
uploaded as artifacts (ADR-030). The environment is never recorded, commands run with a minimal
allowlisted environment, and the host's own secret-looking values are masked wherever they appear
in captured output. Command risk classes (spec §83) are observed, never enforced.
"""

import difflib
import fnmatch
import hashlib
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from blackbox.client import BlackBox, Run

# -- command risk classification (spec §27, §83) -----------------------------------------------

READ_ONLY, MODIFY_FILES, NETWORK = "READ_ONLY", "MODIFY_FILES", "NETWORK"
PACKAGE_INSTALL, PROCESS_CONTROL, DESTRUCTIVE = "PACKAGE_INSTALL", "PROCESS_CONTROL", "DESTRUCTIVE"


@dataclass(frozen=True)
class Classification:
    risk_class: str  # R0..R4
    category: str
    reasons: tuple[str, ...] = ()

    @property
    def level(self) -> int:
        return int(self.risk_class[1:])


Classifier = Callable[[str], "Classification | None"]

_R0_COMMANDS = frozenset(
    """cat ls pwd echo head tail wc grep egrep fgrep rg which whoami date uname stat file diff du df
    sort uniq tree less more basename dirname realpath true false test [ type id hostname ps
    printenv env cut tr nl od xxd md5sum sha256sum shasum column jq""".split()
)
_R1_RUNNERS = frozenset(
    """python python3 pytest node npm npx pnpm yarn make cargo go ruby perl bash sh zsh uv pip pip3
    tox nox mypy ruff eslint prettier tsc javac java mvn gradle cmake""".split()
)
_GIT_READ = frozenset(
    "status diff log show rev-parse blame ls-files describe remote shortlog grep cat-file".split()
)
_GIT_LOCAL = frozenset(
    """add commit checkout switch restore stash merge rebase tag cherry-pick apply init mv am
    revert fetch pull clone worktree config""".split()
)
_INSTALL = {
    "pip": {"install", "uninstall"},
    "pip3": {"install", "uninstall"},
    "npm": {"install", "i", "add", "ci", "uninstall"},
    "pnpm": {"install", "i", "add", "remove"},
    "yarn": {"install", "add", "remove"},
    "cargo": {"install", "add"},
    "go": {"get", "install"},
    "gem": {"install"},
    "apt": {"install", "remove"},
    "apt-get": {"install", "remove"},
    "brew": {"install", "uninstall"},
}
# Whole-command patterns that are dangerous however the command is spelled.
_RAW_RULES: tuple[tuple[re.Pattern[str], int, str, str], ...] = tuple(
    (re.compile(p, re.I), level, cat, why)
    for p, level, cat, why in (
        (r"\bdrop\s+(database|schema)\b", 4, DESTRUCTIVE, "SQL DROP DATABASE/SCHEMA"),
        (r"\bdrop\s+table\b", 3, DESTRUCTIVE, "SQL DROP TABLE"),
        (r"\btruncate\s+(table\s+)?\w", 3, DESTRUCTIVE, "SQL TRUNCATE"),
        (r"\bdelete\s+from\s+\w+\s*(;|$|\")", 3, DESTRUCTIVE, "SQL DELETE without WHERE"),
        (r"\bmkfs(\.\w+)?\b", 4, DESTRUCTIVE, "formats a filesystem"),
        (r"\bdd\b[^|;&]*\bof=/dev/", 4, DESTRUCTIVE, "writes to a device"),
        (r">\s*/dev/(sd|nvme|disk)", 4, DESTRUCTIVE, "writes to a device"),
        (r":\(\)\s*\{\s*:\s*\|", 4, PROCESS_CONTROL, "fork bomb"),
        (r"--no-preserve-root", 4, DESTRUCTIVE, "disables the root safeguard"),
        (r"\b(shutdown|reboot|halt|poweroff)\b", 4, PROCESS_CONTROL, "stops the machine"),
        (
            r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b",
            3,
            NETWORK,
            "pipes a download to a shell",
        ),
    )
)
_OPERATORS = frozenset({"&&", "||", "|", ";", "&", "|&", "\n"})


def _bump(best: Classification | None, level: int, category: str, why: str) -> Classification:
    if best is None or level > best.level:
        return Classification(f"R{level}", category, (why,))
    if level == best.level:
        return Classification(best.risk_class, best.category, (*best.reasons, why))
    return best


def _rm_level(args: list[str]) -> tuple[int, str]:
    flags = "".join(a.lstrip("-") for a in args if a.startswith("-") and not a.startswith("--"))
    long = [a for a in args if a.startswith("--")]
    targets = [a for a in args if not a.startswith("-")]
    recursive = "r" in flags.lower() or "--recursive" in long
    if recursive and any(t in ("/", "/*", "~", "~/", "$HOME", "*", ".", "..") for t in targets):
        return 4, "recursive delete of a root, home or wildcard target"
    return 3, "deletes files" if not recursive else "recursive delete"


def _git_level(args: list[str]) -> tuple[int, str, str]:
    sub = next((a for a in args if not a.startswith("-")), "")
    rest = args[args.index(sub) + 1 :] if sub in args else []
    flat = " ".join(args)
    if sub == "push":
        forced = any(a in ("-f", "--force", "--force-with-lease", "--delete", "-d") for a in rest)
        forced = forced or any(a.startswith("+") for a in rest) or "--mirror" in rest
        return (3, NETWORK, "force/delete push") if forced else (2, NETWORK, "git push")
    if sub == "reset" and "--hard" in rest:
        return 3, DESTRUCTIVE, "git reset --hard discards work"
    if sub == "clean" and any(a.startswith("-") and "f" in a for a in rest):
        return 3, DESTRUCTIVE, "git clean removes untracked files"
    if sub == "branch" and any(a in ("-D", "-d", "--delete") for a in rest):
        return 3, DESTRUCTIVE, "deletes a branch"
    if sub in ("checkout", "restore") and ("--" in rest or "." in rest) and "-b" not in rest:
        return 3, DESTRUCTIVE, "discards working-tree changes"
    if sub == "branch":
        listing = not [a for a in rest if not a.startswith("-")]
        return (
            (0, READ_ONLY, "lists branches") if listing else (1, MODIFY_FILES, "creates a branch")
        )
    if sub in _GIT_READ and not (sub == "remote" and any(a in ("add", "set-url") for a in rest)):
        return 0, READ_ONLY, f"git {sub} is read-only"
    if sub in ("fetch", "pull", "clone"):
        return 2, NETWORK, f"git {sub} contacts a remote"
    if sub in _GIT_LOCAL or sub == "remote":
        return 1, MODIFY_FILES, f"git {sub} changes local state"
    return 1, MODIFY_FILES, f"unrecognised git command ({flat[:40]})"


def _classify_argv(argv: list[str], depth: int) -> tuple[int, str, str]:
    args = list(argv)
    while args and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", args[0]):
        args.pop(0)  # leading VAR=value
    elevated = False
    while args and args[0] in ("sudo", "doas", "nohup", "time", "command", "exec", "nice", "env"):
        elevated = elevated or args[0] in ("sudo", "doas")
        args.pop(0)
        while args and (args[0].startswith("-") or re.fullmatch(r"\w+=.*", args[0])):
            args.pop(0)
    if not args:
        level, cat, why = 0, READ_ONLY, "no command"
    else:
        exe = os.path.basename(args[0])
        rest = args[1:]
        level, cat, why = _classify_exe(exe, rest, depth)
    if elevated and level < 3:
        return 3, PROCESS_CONTROL if cat == READ_ONLY else cat, "runs with elevated privileges"
    return level, cat, why


def _classify_exe(exe: str, rest: list[str], depth: int) -> tuple[int, str, str]:
    if exe in ("bash", "sh", "zsh") and "-c" in rest and depth < 3:
        i = rest.index("-c")
        if i + 1 < len(rest):
            inner = classify_command(rest[i + 1], _depth=depth + 1)
            return (
                max(inner.level, 1),
                inner.category,
                f"shell -c: {inner.reasons[0] if inner.reasons else ''}",
            )
    if exe == "git":
        return _git_level(rest)
    if exe in ("rm", "rmdir", "shred", "unlink"):
        level, why = _rm_level(rest)
        return level, DESTRUCTIVE, why
    if exe in ("chmod", "chown", "chgrp"):
        recursive = any(a in ("-R", "-r", "--recursive") for a in rest)
        return (
            (3, DESTRUCTIVE, "recursive permission change") if recursive else (1, MODIFY_FILES, exe)
        )
    if exe in ("kill", "pkill", "killall"):
        return 2, PROCESS_CONTROL, "signals processes"
    if exe in ("systemctl", "service", "launchctl", "mount", "umount", "iptables", "crontab"):
        return 3, PROCESS_CONTROL, f"{exe} changes system state"
    if exe in _INSTALL and rest and rest[0] in _INSTALL[exe]:
        return 2, PACKAGE_INSTALL, f"{exe} {rest[0]} fetches and installs packages"
    if (exe == "uv" and rest[:2] in (["pip", "install"], ["add"], ["sync"])) or (
        exe == "uv" and rest[:1] == ["add"]
    ):
        return 2, PACKAGE_INSTALL, "uv installs packages"
    if exe in ("curl", "wget", "http", "https"):
        sends = any(
            a
            in (
                "-d",
                "--data",
                "--data-raw",
                "--data-binary",
                "-F",
                "--form",
                "-T",
                "--upload-file",
            )
            or (a in ("-X", "--request") and i + 1 < len(rest) and rest[i + 1].upper() != "GET")
            for i, a in enumerate(rest)
        )
        return (
            (2, NETWORK, "sends data to a remote host")
            if sends
            else (1, NETWORK, "downloads from a remote host")
        )
    if exe in (
        "ssh",
        "scp",
        "rsync",
        "sftp",
        "nc",
        "ncat",
        "telnet",
        "ftp",
        "gh",
        "docker",
        "kubectl",
    ):
        return 2, NETWORK, f"{exe} talks to an external system"
    if exe == "find":
        if any(a in ("-delete", "-exec", "-execdir", "-ok", "-fprint") for a in rest):
            return 3, DESTRUCTIVE, "find with -delete/-exec"
        return 0, READ_ONLY, "find"
    if exe == "sed" and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in rest):
        return 1, MODIFY_FILES, "edits files in place"
    if exe in ("tee", "touch", "mkdir", "cp", "mv", "ln", "install", "truncate", "patch"):
        return 1, MODIFY_FILES, f"{exe} writes files"
    if exe in _R0_COMMANDS or exe in ("sed", "awk"):
        return 0, READ_ONLY, f"{exe} only reads"
    if exe in _R1_RUNNERS:
        return 1, MODIFY_FILES, f"{exe} runs project code"
    return 1, MODIFY_FILES, "unrecognised command (assumed to modify local files)"


def classify_command(command: str, *, _depth: int = 0) -> Classification:
    """Deterministic command risk class R0-R4 and spec §27 category. Compound commands take the
    highest segment. Heuristic by nature: a shell string is not fully parseable (see ADR-031)."""
    if not isinstance(command, str) or not command.strip():
        return Classification("R0", READ_ONLY, ("empty command",))
    best: Classification | None = None
    for pattern, level, cat, why in _RAW_RULES:
        if pattern.search(command):
            best = _bump(best, level, cat, why)
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return _bump(best, 1, MODIFY_FILES, "unparseable command (assumed to modify local files)")
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _OPERATORS or (set(token) <= set("&|;") and token):
            segments.append([])
        else:
            segments[-1].append(token)
    if "$(" in command or "`" in command:
        best = _bump(best, 1, MODIFY_FILES, "command substitution")
    for argv in segments:
        if argv:
            level, cat, why = _classify_argv(argv, _depth)
            best = _bump(best, level, cat, why)
    return best or Classification("R0", READ_ONLY, ("empty command",))


# -- test result parsing (spec §82.3) --------------------------------------------------------------


@dataclass(frozen=True)
class TestSummary:
    __test__ = False  # not a pytest class

    framework: str
    total: int
    passed: int
    failed: int
    skipped: int
    failing: tuple[str, ...] = ()


_UT_RAN = re.compile(r"^Ran (\d+) tests? in [\d.]+s", re.M)
_UT_FAILED = re.compile(r"^FAILED \((.*)\)\s*$", re.M)
_UT_ID = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([\w.]+)\)", re.M)
_PT_LINE = re.compile(r"^(?:=+ )?((?:\d+ [a-z ]+?(?:, )?)+) in [\d.]+s.*$", re.M)
_PT_COUNT = re.compile(
    r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)"
)
_PT_ID = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.M)
_MAX_FAILING = 20


def _count(text: str, key: str) -> int:
    m = re.search(rf"{key}=(\d+)", text)
    return int(m.group(1)) if m else 0


def parse_test_output(output: str) -> TestSummary | None:
    """Recognise a `unittest` or `pytest` summary; None when the runner is not recognised."""
    ran = _UT_RAN.search(output)
    if ran:
        total = int(ran.group(1))
        failed = skipped = 0
        verdict = _UT_FAILED.search(output)
        tail = output[ran.end() :]
        if verdict:
            failed = _count(verdict.group(1), "failures") + _count(verdict.group(1), "errors")
            skipped = _count(verdict.group(1), "skipped")
        else:
            ok = re.search(r"^OK(?: \((.*)\))?\s*$", tail, re.M)
            if ok is None:
                return None
            skipped = _count(ok.group(1) or "", "skipped")
        ids = tuple(
            dict.fromkeys(
                m.group(2)
                if m.group(2).endswith("." + m.group(1))
                else f"{m.group(2)}.{m.group(1)}"
                for m in _UT_ID.finditer(output)
            )
        )[:_MAX_FAILING]  # Python < 3.11 prints the class only; keep the method name

        return TestSummary(
            "unittest", total, max(total - failed - skipped, 0), failed, skipped, ids
        )
    lines = _PT_LINE.findall(output)
    if lines:
        counts: dict[str, int] = {}
        for number, word in _PT_COUNT.findall(lines[-1]):
            counts[word.rstrip("s") if word.startswith("error") else word] = int(number)
        failed = counts.get("failed", 0) + counts.get("error", 0)
        passed, skipped = counts.get("passed", 0), counts.get("skipped", 0)
        ids = tuple(dict.fromkeys(m.group(1) for m in _PT_ID.finditer(output)))[:_MAX_FAILING]
        return TestSummary("pytest", passed + failed + skipped, passed, failed, skipped, ids)
    return None


# -- secret-safe capture (ADR-031) -------------------------------------------------------------

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIAL|PRIVATE", re.I)
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "TZ", "USER", "LOGNAME")
_SENSITIVE_PATHS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".npmrc",
    ".netrc", "credentials*", "*.keystore", ".pypirc", "secrets.*",
)  # fmt: skip


def secret_env_values(
    environ: Mapping[str, str] | None = None, extra: tuple[str, ...] = (), min_len: int = 8
) -> tuple[str, ...]:
    """Values of secret-looking environment variables (longest first), for exact masking."""
    source = os.environ if environ is None else environ
    values = {v for k, v in source.items() if _SECRET_NAME.search(k) and len(v) >= min_len}
    values.update(v for v in extra if isinstance(v, str) and len(v) >= min_len)
    return tuple(sorted(values, key=len, reverse=True))


def mask_values(text: str, values: tuple[str, ...]) -> str:
    for value in values:
        if value in text:
            text = text.replace(value, "[REDACTED:env]")
    return text


def safe_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The only environment a recorded command sees: no inherited credentials."""
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra or {})
    return env


def is_sensitive_path(path: str) -> bool:
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, pattern) for pattern in _SENSITIVE_PATHS)


_LANGUAGES = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".json": "json", ".md": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".sh": "shell", ".toml": "toml", ".html": "html",
    ".css": "css", ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby", ".sql": "sql",
}  # fmt: skip


def language_of(path: str) -> str | None:
    return _LANGUAGES.get(os.path.splitext(path)[1].lower())


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str  # redacted: the agent never sees secrets either
    stderr: str
    duration_ms: float
    classification: Classification
    timed_out: bool = False
    test: TestSummary | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        return self.stdout + (("\n" + self.stderr) if self.stderr else "")


def _diff_text(path: str, before: str | None, after: str | None) -> str:
    a = (before or "").splitlines(keepends=True)
    b = (after or "").splitlines(keepends=True)
    diff = difflib.unified_diff(
        a, b, "/dev/null" if before is None else f"a/{path}",
        "/dev/null" if after is None else f"b/{path}",
    )  # fmt: skip
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff
    )


def _count_changes(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


MAX_DIFF_BYTES = 1024 * 1024


class CodingRecorder:
    """Records the file, git and shell operations of a coding agent working under `root`."""

    def __init__(
        self,
        bb: BlackBox,
        run: Run,
        root: str | Path,
        *,
        root_label: str = "/workspace",
        classifier: Classifier | None = None,
        capture_limit: int = 1024 * 1024,
        timeout: float = 120.0,
    ) -> None:
        self.bb, self.run = bb, run
        self.root = Path(root).resolve()
        self.root_label = root_label.rstrip("/")
        self._classifier = classifier
        self._limit = capture_limit
        self._timeout = timeout
        extra = (bb.config.api_key,) if bb.config.api_key else ()
        self._secrets = secret_env_values(extra=extra)

    # -- paths and text ----------------------------------------------------------------------------

    def resolve(self, path: str) -> Path:
        """A path inside the root. A model-chosen path must never escape the working directory."""
        full = (self.root / path).resolve()
        if full != self.root and self.root not in full.parents:
            raise ValueError(f"path escapes the workspace: {path!r}")
        return full

    def rel(self, full: Path) -> str:
        return full.relative_to(self.root).as_posix() if full != self.root else "."

    def sanitize(self, text: str) -> str:
        return self.bb.redact_text(mask_values(text, self._secrets))

    # -- files -------------------------------------------------------------------------------------

    def read_file(self, path: str, max_bytes: int = 256 * 1024) -> str:
        full = self.resolve(path)
        data = full.read_bytes()
        self.run.event(
            "file.read",
            {
                "file.path": self.rel(full), "file.operation": "read", "file.size_after": len(data),
                "file.hash_after": _sha(data), **self._lang(path),
            },
        )  # fmt: skip
        return data[:max_bytes].decode("utf-8", errors="replace")

    @staticmethod
    def _lang(path: str) -> dict[str, str]:
        lang = language_of(path)
        return {"file.language": lang} if lang else {}

    def write_file(self, path: str, content: str) -> bool:
        """Write a file; records created/modified with a diff artifact. False if nothing changed."""
        full = self.resolve(path)
        rel = self.rel(full)
        old = full.read_bytes() if full.exists() else None
        new = content.encode("utf-8")
        if old == new:
            return False
        full.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=full.parent, prefix=".abb-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
        os.replace(tmp, full)
        attrs: dict[str, object] = {
            "file.path": rel, "file.operation": "created" if old is None else "modified",
            "file.size_after": len(new), "file.hash_after": _sha(new), **self._lang(path),
        }  # fmt: skip
        if old is not None:
            attrs.update({"file.size_before": len(old), "file.hash_before": _sha(old)})
        before = None if old is None else old.decode("utf-8", errors="replace")
        self._attach_diff(attrs, rel, before, content, len(old or b"") + len(new))
        self.run.event("file.created" if old is None else "file.modified", attrs)
        return True

    def delete_file(self, path: str) -> bool:
        full = self.resolve(path)
        if not full.is_file():
            return False
        old = full.read_bytes()
        full.unlink()
        rel = self.rel(full)
        attrs: dict[str, object] = {
            "file.path": rel, "file.operation": "deleted", "file.size_before": len(old),
            "file.hash_before": _sha(old), **self._lang(path),
        }  # fmt: skip
        self._attach_diff(attrs, rel, old.decode("utf-8", errors="replace"), None, len(old))
        self.run.event("file.deleted", attrs)
        return True

    def _attach_diff(
        self, attrs: dict[str, object], rel: str, before: str | None, after: str | None, size: int
    ) -> None:
        if is_sensitive_path(rel):
            attrs["diff.withheld"] = "sensitive_path"  # hashes and sizes only, never content
            return
        if size > MAX_DIFF_BYTES:
            attrs["diff.withheld"] = "too_large"
            return
        diff = _diff_text(rel, before, after)
        added, removed = _count_changes(diff)
        attrs["file.lines_added"], attrs["file.lines_removed"] = added, removed
        artifact = self.bb.upload_artifact(diff, kind="diff", name=rel, run=self.run)
        if artifact:
            attrs["diff.artifact"] = f"artifact://{artifact}"

    # -- commands ----------------------------------------------------------------------------------

    def classify(self, command: str) -> Classification:
        if self._classifier is not None:
            try:
                custom = self._classifier(command)
            except Exception:
                custom = None
            if custom is not None:
                return custom
        return classify_command(command)

    def run_command(
        self,
        command: str,
        *,
        cwd: str = ".",
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run a shell command with a minimal environment; record it; return sanitized output."""
        work = self.resolve(cwd)
        label = self.root_label + ("" if work == self.root else "/" + self.rel(work))
        verdict = self.classify(command)
        span = self.run.span(
            command, kind="shell",
            attributes={
                "shell.cwd": label, "shell.risk_class": verdict.risk_class,
                "shell.category": verdict.category,
            },
        )  # fmt: skip
        span.start()
        limit = timeout if timeout is not None else self._timeout
        started = time.monotonic()
        code, out, err, total_out, total_err, timed_out = self._execute(command, work, limit, env)
        duration = (time.monotonic() - started) * 1000
        stdout, stderr = self.sanitize(out), self.sanitize(err)
        summary = parse_test_output(stdout + "\n" + stderr)
        attrs: dict[str, object] = {
            "shell.exit_code": code, "shell.duration_ms": round(duration, 3),
            "shell.stdout_bytes": total_out, "shell.stderr_bytes": total_err,
            "shell.output_truncated": total_out > self._limit or total_err > self._limit,
        }  # fmt: skip
        for stream, text, size in (("stdout", stdout, total_out), ("stderr", stderr, total_err)):
            if size:
                artifact = self.bb.upload_artifact(
                    text, kind=stream, name=command[:120], run=self.run
                )
                if artifact:
                    attrs[f"shell.{stream}_artifact"] = f"artifact://{artifact}"
        if summary is not None:
            attrs.update(
                {
                    "test.framework": summary.framework, "test.suite": cwd,
                    "test.total": summary.total, "test.passed": summary.passed,
                    "test.failed": summary.failed, "test.skipped": summary.skipped,
                    "test.failing": list(summary.failing),
                }
            )  # fmt: skip
        span.set_attributes(attrs)
        if timed_out:
            self.run.event(
                "timeout.occurred",
                {"timeout.operation": command[:200], "timeout.limit_ms": limit * 1000},
            )
        span.end("success" if code == 0 else "error")
        return CommandResult(command, code, stdout, stderr, duration, verdict, timed_out, summary)

    def _execute(
        self, command: str, cwd: Path, timeout: float, extra_env: Mapping[str, str] | None
    ) -> tuple[int, str, str, int, int, bool]:
        try:
            proc = subprocess.Popen(  # noqa: S602  (a shell is the point: the agent runs shell commands)
                command, shell=True, cwd=cwd, env=safe_environment(extra_env),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )  # fmt: skip
        except OSError as exc:
            return 127, "", f"could not start the command: {type(exc).__name__}", 0, 0, False
        kept: dict[str, bytearray] = {"out": bytearray(), "err": bytearray()}
        totals = {"out": 0, "err": 0}

        def drain(stream: IO[bytes], key: str) -> None:
            read = getattr(stream, "read1", None) or stream.read
            while True:
                chunk = read(65536)
                if not chunk:
                    return
                totals[key] += len(chunk)
                room = self._limit - len(kept[key])
                if room > 0:
                    kept[key].extend(chunk[:room])

        threads = [
            threading.Thread(target=drain, args=(proc.stdout, "out"), daemon=True),
            threading.Thread(target=drain, args=(proc.stderr, "err"), daemon=True),
        ]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            code = proc.wait()
        for thread in threads:
            thread.join(2.0)
        return (
            code, kept["out"].decode("utf-8", errors="replace"),
            kept["err"].decode("utf-8", errors="replace"), totals["out"], totals["err"], timed_out,
        )  # fmt: skip

    # -- git ---------------------------------------------------------------------------------------

    def _git(self, *args: str, cwd: str = ".") -> CommandResult:
        return self.run_command("git " + shlex.join(args), cwd=cwd)

    def _head(self) -> str | None:
        probe = self._quiet("git rev-parse HEAD")
        return probe or None

    def _quiet(self, command: str) -> str:
        """An unrecorded read used to fill git attributes (no secrets: hashes and names only)."""
        try:
            done = subprocess.run(  # noqa: S602
                command, shell=True, cwd=self.root, env=safe_environment(), capture_output=True,
                timeout=30, check=False, stdin=subprocess.DEVNULL, text=True,
            )  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    def git_branch(self, name: str) -> CommandResult:
        base = self._head()
        result = self._git("checkout", "-b", name)
        if result.ok:
            attrs: dict[str, object] = {"git.branch": name}
            if base:
                attrs["git.base_commit"] = base
            self.run.event("git.branch_created", attrs)
        return result

    def git_diff(self) -> str:
        """The working-tree diff; recorded as a git.diff event with a diff artifact."""
        result = self._git("diff", "--no-color")
        names = self._quiet("git diff --name-only")
        files = [n for n in names.splitlines() if n]
        attrs: dict[str, object] = {
            "git.changed_files": len(files),
            "git.diff_stat_files": len(files),
        }
        if result.stdout:
            artifact = self.bb.upload_artifact(
                result.stdout, kind="diff", name="git diff", run=self.run
            )
            if artifact:
                attrs["diff.artifact"] = f"artifact://{artifact}"
        self.run.event("git.diff", attrs)
        return result.stdout

    def git_commit(self, message: str) -> CommandResult:
        self._git("add", "-A")
        result = self._git("commit", "-m", message)
        if result.ok:
            head = self._head()
            files = [n for n in self._quiet("git show --name-only --format=").splitlines() if n]
            attrs: dict[str, object] = {"git.changed_files": len(files)}
            if head:
                attrs["git.commit_hash"] = attrs["git.head_commit"] = head
            branch = self._quiet("git rev-parse --abbrev-ref HEAD")
            if branch:
                attrs["git.branch"] = branch
            self.run.event("git.commit", attrs)
        return result

    def git_push(self, remote: str, branch: str) -> CommandResult:
        result = self._git("push", "-u", remote, branch)
        if result.ok:
            self.run.event(
                "git.push", {"git.push_target": f"{remote} {branch}", "git.branch": branch}
            )
        return result
