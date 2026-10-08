"""Coding-agent capture (ADR-031): file, git and shell events with secret-safe output.

`CodingRecorder` wraps the operations a coding agent performs on a working directory and records
them as typed events. Contents never go into events: diffs and terminal output are redacted and
uploaded as artifacts (ADR-030). The environment is never recorded, commands run with a minimal
allowlisted environment, and the host's own secret-looking values are masked wherever they appear
in captured output. Command risk classes (spec §83) are observed, never enforced.
"""

import difflib
import hashlib
import logging
import os
import re
import shlex
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import IO

from blackbox.client import BlackBox, Run
from blackbox.secretscan import (
    SecretFiles,
    command_may_reach_secrets,
    git_excludes,
    is_sensitive_path,
    mask_text,
    strip_ansi,
    withhold_sensitive_hunks,
)

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


MAX_REASONS = 8
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
    """status diff log show rev-parse blame ls-files describe shortlog grep cat-file ls-tree
    rev-list""".split()
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
        (
            r"\bdelete\s+from\s+[\w.\"`]+\s*(;|$|[\"'`)]|\\n)",
            3,
            DESTRUCTIVE,
            "SQL DELETE without WHERE",
        ),
        (r"\bmkfs(\.\w+)?\b", 4, DESTRUCTIVE, "formats a filesystem"),
        (r"/dev/(tcp|udp)/", 3, NETWORK, "network access through /dev/tcp"),
        (r"\bdd\b[^|;&]*\bof=/dev/", 4, DESTRUCTIVE, "writes to a device"),
        (r">\s*/dev/(sd|nvme|disk)", 4, DESTRUCTIVE, "writes to a device"),
        (r":\(\)\s*\{\s*:\s*\|", 4, PROCESS_CONTROL, "fork bomb"),
        (r"--no-preserve-root", 4, DESTRUCTIVE, "disables the root safeguard"),
        (r"\b(shutdown|reboot|halt|poweroff)\b", 4, PROCESS_CONTROL, "stops the machine"),
        (
            r"\b(curl|wget|fetch)\b[^|]*\|\s*(sudo\s+)?(env\s+)?(ba|z|da|k)?sh\b"
            r"|\b(curl|wget|fetch)\b[^|]*\|\s*(sudo\s+)?(python[0-9.]*|perl|ruby|node|php|lua)\b",
            3,
            NETWORK,
            "pipes a download to an interpreter",
        ),
        (
            r"\brm\s+(-[a-zA-Z]+\s+|--[a-z-]+\s+)*(--\s+)?(/|~|\$HOME|\*)(\s|$|;|\"|'|\))",
            4,
            DESTRUCTIVE,
            "recursive or forced delete of a root, home or wildcard target",
        ),
        (r"\bchmod\s+(-[a-zA-Z]+\s+)*[0-7]{3,4}\s+/(\s|$)", 3, DESTRUCTIVE, "permissions on /"),
        (
            r"\b(shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs)|rmtree|FileUtils\.(rm_rf|rm_r|"
            r"rm_f|remove\w*)"
            r"|File\.delete|unlinkSync|rmSync|rmdirSync|Files\.delete)\b",
            3,
            DESTRUCTIVE,
            "code that deletes files",
        ),
        (r">>?\s*/proc/|\btee\b[^|;&]*\s/proc/", 3, PROCESS_CONTROL, "writes to /proc"),
        (
            r"(>>?|\btee\b(\s+-a)?|\b(cp|mv|install|sed\s+-i)\b[^|;&]*)\s*\S*authorized_keys",
            3,
            DESTRUCTIVE,
            "changes SSH authorized_keys",
        ),
        (
            r"\b(sh|bash|zsh|source|\.)\s+<\(",
            3,
            NETWORK,
            "runs dynamic content from a process substitution",
        ),
        (
            r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|ksh|python[0-9.]*|node|ruby|perl|php)"
            r"(?:\s+-)?\s*(?:$|[;&|)\n])",
            3,
            PROCESS_CONTROL,
            "pipes content into an interpreter",
        ),
    )
)
_OPERATORS = frozenset({"&&", "||", "|", ";", "&", "|&", ";;"})
_REDIRECT = re.compile(r"^[0-9]*(>>?|>\||&>>?|<>)$|^>&$")
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})
_INTERPRETER_EVAL = {
    "python": "-c", "python3": "-c", "node": "-e", "perl": "-e", "ruby": "-e", "php": "-r",
}  # fmt: skip
# Words that only introduce a command: `if cmd`, `then cmd`, `while cmd`, `do cmd` ...
_LEAD_WORDS = frozenset("if then else elif do while until ! { time".split())
_SKIP_WORDS = frozenset("fi done esac }".split())
# Wrapper commands run their argument: value-taking options are skipped with their value.
_WRAPPERS: dict[str, frozenset[str]] = {
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}),
    "doas": frozenset({"-u", "-C"}),
    "env": frozenset({"-u", "-C", "-S"}),
    "nohup": frozenset(), "command": frozenset(), "exec": frozenset(), "builtin": frozenset(),
    "time": frozenset({"-f", "-o"}), "nice": frozenset({"-n"}), "ionice": frozenset({"-c", "-n"}),
    "setsid": frozenset(), "stdbuf": frozenset({"-i", "-o", "-e"}), "chronic": frozenset(),
    "timeout": frozenset({"-s", "-k"}), "watch": frozenset({"-n", "-d"}),
    "xargs": frozenset({"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"}),
}  # fmt: skip
_GIT_GLOBAL_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)
_MAX_DEPTH = 4


def _bump(best: Classification | None, level: int, category: str, why: str) -> Classification:
    if best is None or level > best.level:
        return Classification(f"R{level}", category, (why,))
    if level == best.level and why not in best.reasons and len(best.reasons) < MAX_REASONS:
        # Informational; copying a growing tuple per segment was quadratic on huge commands.
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


def _flag_cluster(arg: str, letters: str) -> bool:
    """Is `arg` a short-flag cluster (-fu, -xdf) containing any of `letters`?"""
    return bool(re.fullmatch(r"-[A-Za-z]+", arg)) and any(c in arg[1:] for c in letters)


_GIT_EXEC_CONFIG = re.compile(
    r"(?i)^(core\.(pager|fsmonitor|sshcommand|editor|hookspath|askpass|gitproxy)|alias\.|"
    r"diff\.external|"
    r"gpg\.program|credential\.helper|filter\.|merge\.tool|difftool\.|pager\.|sequence\.editor|"
    r"http\.proxy|url\..*insteadof|protocol\.)"
)


def _git_level(args: list[str]) -> tuple[int, str, str]:
    rest_args = list(args)
    runs_config = False
    while rest_args and rest_args[0].startswith(
        "-"
    ):  # git's own options come before the subcommand
        opt = rest_args.pop(0)
        value = ""
        if opt in _GIT_GLOBAL_WITH_VALUE and rest_args:
            value = rest_args.pop(0)
        elif opt.startswith("-c") and len(opt) > 2:
            value = opt[2:]
        if (opt == "-c" or opt.startswith("-c")) and _GIT_EXEC_CONFIG.match(value.split("=", 1)[0]):
            runs_config = True
    sub = rest_args[0] if rest_args else ""
    rest = rest_args[1:]
    if runs_config:
        return 3, MODIFY_FILES, "git -c sets configuration that runs a program"
    if any(a.startswith("--output") for a in rest) and sub in (
        "diff",
        "log",
        "show",
        "format-patch",
        "blame",
    ):
        return 1, MODIFY_FILES, f"git {sub} --output writes a file"
    if sub == "grep" and any(
        a in ("-O", "--open-files-in-pager")
        or a.startswith("-O")
        or a.startswith("--open-files-in-pager=")
        for a in rest
    ):
        return 3, MODIFY_FILES, "git grep -O runs a pager command"
    if sub == "push":
        forced = any(a in ("--force", "--force-with-lease", "--delete", "--mirror") for a in rest)
        forced = forced or any(_flag_cluster(a, "fd") for a in rest)
        forced = forced or any(a.startswith(("+", ":")) and len(a) > 1 for a in rest)
        forced = forced or any(a.startswith("--force-with-lease=") for a in rest)
        return (3, NETWORK, "force/delete push") if forced else (2, NETWORK, "git push")
    if sub == "reset" and "--hard" in rest:
        return 3, DESTRUCTIVE, "git reset --hard discards work"
    if sub == "clean" and any(_flag_cluster(a, "f") or a == "--force" for a in rest):
        return 3, DESTRUCTIVE, "git clean removes untracked files"
    if sub == "rm":
        forced = any(a == "--force" or _flag_cluster(a, "f") or _flag_cluster(a, "r") for a in rest)
        return (
            (3, DESTRUCTIVE, "git rm -f/-r deletes files")
            if forced
            else (1, MODIFY_FILES, "git rm deletes tracked files")
        )
    if sub == "tag" and any(a == "--delete" or _flag_cluster(a, "d") for a in rest):
        return 3, DESTRUCTIVE, "deletes a tag"
    if sub == "tag" and (
        not [a for a in rest if not a.startswith("-")] or any(a in ("-l", "--list") for a in rest)
    ):
        return 0, READ_ONLY, "lists tags"
    if sub == "rebase" and any(
        a in ("-x", "--exec") or a.startswith("--exec=") or (a.startswith("-x") and len(a) > 2)
        for a in rest
    ):
        return 3, MODIFY_FILES, "git rebase --exec runs a command"
    if sub == "tag" and any(a == "--force" or _flag_cluster(a, "f") for a in rest):
        return 2, MODIFY_FILES, "git tag -f moves an existing tag"
    if sub == "branch" and any(a == "--force" or _flag_cluster(a, "fM") for a in rest):
        return 2, MODIFY_FILES, "git branch -f/-M overwrites a branch"
    if sub in ("checkout", "switch") and "--discard-changes" in rest:
        return 3, DESTRUCTIVE, "discards working-tree changes"
    if sub == "worktree" and rest[:1] in (["remove"], ["prune"]):
        forced = any(a == "--force" or _flag_cluster(a, "f") for a in rest)
        return (
            (3, DESTRUCTIVE, "worktree removal with --force")
            if forced
            else (2, DESTRUCTIVE, "removes a worktree")
        )
    if sub == "remote":
        action = next((a for a in rest if not a.startswith("-")), "")
        if action in ("prune", "update", "show"):
            return 2, NETWORK, f"git remote {action} contacts the remote"
        if action in (
            "remove",
            "rm",
            "rename",
            "set-head",
            "set-branches",
            "set-url",
            "add",
            "get-url",
        ):
            return (
                (0, READ_ONLY, "reads a remote URL")
                if action == "get-url"
                else (2, MODIFY_FILES, f"git remote {action} edits repository configuration")
            )
        return 0, READ_ONLY, "lists remotes"
    if sub == "branch" and any(a in ("--delete",) or _flag_cluster(a, "dD") for a in rest):
        return 3, DESTRUCTIVE, "deletes a branch"
    if sub == "branch" and any(
        a
        in (
            "--unset-upstream",
            "--set-upstream-to",
            "--move",
            "--copy",
            "--force",
            "--edit-description",
            "--track",
            "--no-track",
        )
        or a.startswith("--set-upstream-to=")
        or _flag_cluster(a, "mMcCfu")
        for a in rest
    ):
        return 1, MODIFY_FILES, "git branch changes branches or their configuration"
    if sub in ("checkout", "restore", "switch") and (
        "--" in rest or "." in rest or any(_flag_cluster(a, "f") for a in rest) or "--force" in rest
    ):
        if "-b" not in rest and "-c" not in rest:
            return 3, DESTRUCTIVE, "discards working-tree changes"
    if sub in ("filter-branch", "filter-repo", "replace") or (
        sub == "stash" and rest[:1] in (["drop"], ["clear"])
    ):
        return 3, DESTRUCTIVE, f"git {sub} rewrites or drops history"
    if (sub == "reflog" and "expire" in rest) or (sub == "gc" and any("prune" in a for a in rest)):
        return 3, DESTRUCTIVE, "git removes recoverable objects"
    if sub == "update-ref" and any(a in ("-d", "--delete") for a in rest):
        return 3, DESTRUCTIVE, "deletes a ref"
    if sub == "branch":
        listing = not [a for a in rest if not a.startswith("-")]
        return (
            (0, READ_ONLY, "lists branches") if listing else (1, MODIFY_FILES, "creates a branch")
        )
    if sub in _GIT_READ:
        return 0, READ_ONLY, f"git {sub} is read-only"
    if sub in ("fetch", "pull", "clone"):
        return 2, NETWORK, f"git {sub} contacts a remote"
    if sub in _GIT_LOCAL:
        return 1, MODIFY_FILES, f"git {sub} changes local state"
    return 2, MODIFY_FILES, f"unrecognised git command ({' '.join(args)[:40]})"


def _unwrap(argv: list[str]) -> tuple[list[str], bool]:
    """Strip VAR=value prefixes and wrapper commands; True if one of them elevates privileges."""
    args, elevated = list(argv), False
    while args:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", args[0]):
            args.pop(0)
            continue
        wrapper = os.path.basename(args[0])
        if wrapper not in _WRAPPERS:
            break
        elevated = elevated or wrapper in ("sudo", "doas")
        valued = _WRAPPERS[wrapper]
        args.pop(0)
        while args and (args[0].startswith("-") or re.fullmatch(r"\w+=.*", args[0])):
            opt = args.pop(0)
            if wrapper == "env" and (
                opt in ("-S", "--split-string") or opt.startswith(("--split-string=", "-S"))
            ):
                value = (
                    opt.split("=", 1)[1]
                    if opt.startswith("--split-string=")
                    else (
                        opt[2:]
                        if opt.startswith("-S") and len(opt) > 2
                        else (args.pop(0) if args else "")
                    )
                )
                try:  # env -S 'rm -rf x' runs the string as a command line
                    args = shlex.split(value) + args
                except ValueError:
                    args = [value, *args]
                continue
            if opt in valued and args:
                args.pop(0)
        if wrapper == "timeout" and args:
            args.pop(0)  # the duration
    return args, elevated


def _classify_argv(argv: list[str], depth: int) -> tuple[int, str, str]:
    args, elevated = _unwrap(argv)
    if not args:
        level, cat, why = (
            (0, READ_ONLY, "no command") if not elevated else (3, PROCESS_CONTROL, "sudo")
        )
    else:
        level, cat, why = _classify_exe(os.path.basename(args[0]), args[1:], depth)
    if elevated and level < 3:
        return 3, PROCESS_CONTROL if cat == READ_ONLY else cat, "runs with elevated privileges"
    return level, cat, why


def _inner(command: str, depth: int) -> tuple[int, str, str] | None:
    if depth >= _MAX_DEPTH:
        return 3, MODIFY_FILES, "nested commands too deep to classify (assumed risky)"
    inner = classify_command(command, _depth=depth + 1)
    return inner.level, inner.category, f"nested: {inner.reasons[0] if inner.reasons else ''}"


_CLOUD_DESTROY = {
    "terraform": (("destroy",), 4, "terraform destroy removes infrastructure"),
    "tofu": (("destroy",), 4, "tofu destroy removes infrastructure"),
    "pulumi": (("destroy",), 4, "pulumi destroy removes infrastructure"),
}
_SAFE_SCRIPTS = frozenset(
    "test tests lint build check typecheck type-check format fmt compile coverage docs".split()
)
_TEST_BUILD = {
    "pytest": None, "tox": None, "nox": None, "mypy": None, "ruff": ("check", "format"),
    "eslint": None, "tsc": None, "javac": None, "cmake": ("--build", "-B", "."),
    "npm": ("test", "ci", "t", "lint", "build", "start"),
    "pnpm": ("test", "lint", "build"),
    "yarn": ("test", "lint", "build"),
    "cargo": ("test", "check", "build", "fmt", "clippy", "bench", "doc"),
    "go": ("test", "build", "vet", "fmt", "mod"),
    "make": ("test", "check", "lint", "build", "all", "fmt", "format", "typecheck"),
    "mvn": ("test", "verify", "compile", "package"), "gradle": ("test", "build", "check"),
}  # fmt: skip
_PY_TOOLS = frozenset(
    {"unittest", "pytest", "mypy", "ruff", "black", "isort", "flake8", "coverage", "tox"}
)
_DYNAMIC_CODE = re.compile(
    r"open\s*\(|write|remove|unlink|rmtree|rmdir|system|exec|eval|subprocess|popen|rename|"
    r"mkdir|chmod"
    r"|requests|urllib|socket|__import__|compile|fs\.|child_process|FileUtils|File\.|Dir\.|IO\.|`",
    re.I,
)


def _method_of(rest: list[str]) -> str | None:
    """The HTTP method a curl-like command asks for (`-XDELETE`, `-X DELETE`, `--request=POST`)."""
    for i, a in enumerate(rest):
        if a in ("-X", "--request") and i + 1 < len(rest):
            return rest[i + 1].upper()
        if a.startswith("--request="):
            return a.split("=", 1)[1].upper()
        if a.startswith("-X") and len(a) > 2:
            return a[2:].upper()
    return None


def _sed_level(rest: list[str]) -> tuple[int, str, str] | None:
    if any(
        a == "--in-place"
        or a.startswith("--in-place=")
        or re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*\S*", a)
        for a in rest
    ):
        return 1, MODIFY_FILES, "sed edits files in place"
    scripts = [a for a in rest if not a.startswith("-")]
    program = scripts[0] if scripts else ""
    for a in rest:  # `--expression=PROGRAM` / `-e PROGRAM` / `--file=F` carry the program too
        if a.startswith("--expression="):
            program += "\n" + a.split("=", 1)[1]
    for flag, value in pairwise(rest):
        if flag in ("-e", "--expression"):
            program += "\n" + value
    if re.search(r"(^|[;{}\n])\s*[0-9$,/]*\s*[eE]\b", program) or re.search(
        r"s(.).*\1.*\1[a-z0-9]*e", program
    ):
        return 3, MODIFY_FILES, "sed runs commands (e)"
    if re.search(r"(^|[;{}\n])\s*[0-9$,/]*\s*[wW]\s*\S|s(.).*\2.*\2[a-z0-9]*w\s*\S", program):
        return 1, MODIFY_FILES, "sed writes files (w)"
    return None


def _read_only_with_flags(exe: str, rest: list[str]) -> tuple[int, str, str] | None:
    """Allowlisted read-only commands become writers with certain flags; None if truly read-only."""
    positional = [a for a in rest if not a.startswith("-")]
    flags = [a for a in rest if a.startswith("-")]
    if exe == "sort" and any(
        f in ("-o", "--output") or f.startswith(("-o", "--output=")) for f in flags
    ):
        return 1, MODIFY_FILES, "sort -o writes a file"
    if exe == "uniq" and len(positional) > 1:
        return 1, MODIFY_FILES, "uniq writes its second operand"
    if exe == "sort" and any(f.startswith("--compress-program") for f in flags):
        return 3, MODIFY_FILES, "sort --compress-program runs a program"
    if exe == "tree" and any(f == "-o" or f.startswith(("-o", "--output")) for f in flags):
        return 1, MODIFY_FILES, "tree -o writes a file"
    if exe == "less" and any(
        f in ("-o", "-O") or f.startswith(("-o", "-O", "--log-file", "--LOG-FILE")) for f in flags
    ):
        return 1, MODIFY_FILES, "less logs its input to a file"
    if exe == "file" and any(f in ("-C", "--compile") for f in flags):
        return 1, MODIFY_FILES, "file -C writes a compiled magic file"
    if exe == "xxd" and any(f == "-r" or _flag_cluster(f, "r") for f in flags):
        return 1, MODIFY_FILES, "xxd -r writes binary output"
    if exe == "date" and any(f in ("-s", "--set") or f.startswith(("-s", "--set=")) for f in flags):
        return 3, PROCESS_CONTROL, "date -s sets the system clock"
    if exe == "hostname" and positional:
        return 3, PROCESS_CONTROL, "hostname NAME changes the machine name"
    if exe in ("rg", "ag", "ack") and any(f.startswith(("--pre", "--hostname-bin")) for f in flags):
        return 3, MODIFY_FILES, f"{exe} --pre runs a command on every file"
    if exe in ("diff", "less", "more") and any(f.startswith("--output") for f in flags):
        return 1, MODIFY_FILES, f"{exe} writes a file"
    return None


def _classify_exe(exe: str, rest: list[str], depth: int) -> tuple[int, str, str]:
    if exe == "busybox" and rest:
        return _classify_exe(os.path.basename(rest[0]), rest[1:], depth)
    if exe == "eval":
        dynamic = any("$" in a or "`" in a for a in rest)
        found = _inner(" ".join(rest), depth)
        level = max(found[0] if found else 1, 3 if dynamic else 1)
        return level, (found[1] if found else MODIFY_FILES), "eval runs dynamic content"
    if exe in _SHELLS:  # sh -c, bash -lc, zsh -ec ...
        for i, a in enumerate(rest):
            if (a == "-c" or _flag_cluster(a, "c")) and i + 1 < len(rest):
                found = _inner(rest[i + 1], depth)
                if found:
                    return max(found[0], 1), found[1], found[2]
        return 2, MODIFY_FILES, f"{exe} runs a script of unknown effect"
    if exe in _INTERPRETER_EVAL and _INTERPRETER_EVAL[exe] in rest:
        i = rest.index(_INTERPRETER_EVAL[exe])
        code = rest[i + 1] if i + 1 < len(rest) else ""
        if not _DYNAMIC_CODE.search(code):  # trivially read-only: print(1 + 1)
            return 1, MODIFY_FILES, f"{exe} runs inline code"
        literals = re.findall(r"""['"]([^'"]{2,})['"]""", code) or [code]
        found = max((f for f in (_inner(lit, depth) for lit in literals) if f), default=None)
        floor = (3, MODIFY_FILES, f"{exe} inline code can read, write or run anything")
        return max(floor, found) if found else floor
    if exe in ("python", "python3") and rest[:1] == ["-m"] and len(rest) > 1:
        module = rest[1]
        if module in ("pip", "pipx", "ensurepip"):
            return _classify_exe("pip", rest[2:], depth)
        if module == "json.tool":
            return 0, READ_ONLY, "python -m json.tool only reformats JSON"
        if module in _PY_TOOLS or module.startswith(("pytest", "unittest")):
            return 1, MODIFY_FILES, f"python -m {module} runs project code"
        return 2, MODIFY_FILES, f"python -m {module} runs a module of unknown effect"
    if exe in ("awk", "gawk", "mawk", "nawk"):
        program = " ".join(rest)
        if re.search(r"(^|\s)-i\s*inplace\b|--inplace\b|-i\s+inplace", program):
            return 1, MODIFY_FILES, f"{exe} -i inplace edits files in place"
        if "system(" in program or re.search(r"print[^;}]*[>|]|getline", program):
            found = _inner(" ".join(re.findall(r'"([^"]*)"', program)), depth)
            floor = (1, MODIFY_FILES, "awk runs commands or writes files")
            return max(floor, found) if found else floor
        return 0, READ_ONLY, f"{exe} only reads"
    if exe == "sed":
        return _sed_level(rest) or (0, READ_ONLY, "sed only reads")
    if exe == "git":
        return _git_level(rest)
    if exe in ("rm", "rmdir", "shred", "unlink"):
        level, why = _rm_level(rest)
        return level, DESTRUCTIVE, why
    if exe == "mv" and any(
        a in ("/", "/*", "~", "~/", "$HOME", "/etc", "/usr", "/bin") for a in rest[:-1]
    ):
        return 4, DESTRUCTIVE, "moves a system directory"
    if exe == "truncate":
        return 3, DESTRUCTIVE, "truncate destroys file contents"
    if exe in (
        "wipefs",
        "fdisk",
        "sfdisk",
        "gdisk",
        "sgdisk",
        "parted",
        "mkswap",
        "cryptsetup",
        "blkdiscard",
    ):
        return 4, DESTRUCTIVE, f"{exe} changes disk layout"
    if (
        exe == "diskutil"
        and rest[:1]
        and re.match(r"(?i)(erase|partition|repair|secure|zero|random)", rest[0])
    ):
        return 4, DESTRUCTIVE, "diskutil erases or repartitions a disk"
    if exe in ("chmod", "chown", "chgrp"):
        recursive = any(a in ("-R", "-r", "--recursive") or _flag_cluster(a, "R") for a in rest)
        return (
            (3, DESTRUCTIVE, "recursive permission change") if recursive else (1, MODIFY_FILES, exe)
        )
    if exe in ("kill", "pkill", "killall"):
        return 2, PROCESS_CONTROL, "signals processes"
    if exe in (
        "systemctl",
        "service",
        "launchctl",
        "mount",
        "umount",
        "iptables",
        "crontab",
        "sysctl",
        "ufw",
    ):
        return 3, PROCESS_CONTROL, f"{exe} changes system state"
    if exe in _CLOUD_DESTROY and any(a in _CLOUD_DESTROY[exe][0] or a == "-destroy" for a in rest):
        return _CLOUD_DESTROY[exe][1], DESTRUCTIVE, _CLOUD_DESTROY[exe][2]
    if exe == "terraform" and rest[:1] in (["apply"], ["import"], ["state"], ["taint"]):
        return 3, DESTRUCTIVE, "terraform changes real infrastructure"
    if exe == "aws":
        text = " ".join(rest)
        if re.search(
            r"\b(rm|rb|delete[\w-]*|terminate[\w-]*|remove[\w-]*|purge|detach[\w-]*)\b", text
        ):
            return 3, DESTRUCTIVE, "aws deletes or terminates resources"
        return 2, NETWORK, "aws talks to a cloud account"
    if exe in ("gcloud", "az", "doctl", "heroku", "flyctl", "fly", "vercel", "netlify", "wrangler"):
        text = " ".join(rest)
        if re.search(r"\b(delete|destroy|remove|rm|purge|terminate)\b", text):
            return 3, DESTRUCTIVE, f"{exe} deletes resources"
        return 2, NETWORK, f"{exe} talks to a cloud account"
    if exe == "kubectl" and any(
        a in ("delete", "drain", "replace", "apply", "patch", "scale", "rollout", "cordon", "taint")
        for a in rest
    ):
        return 3, DESTRUCTIVE, "kubectl changes the cluster"
    if exe in ("npm", "pnpm", "yarn") and any(
        a
        in ("publish", "unpublish", "deprecate", "owner", "access", "dist-tag", "login", "adduser")
        for a in rest
    ):
        return 3, NETWORK, f"{exe} changes a public registry"
    if exe in ("cargo", "gem", "twine", "poetry") and any(
        a in ("publish", "push", "yank", "upload") for a in rest
    ):
        return 3, NETWORK, f"{exe} publishes a package"
    if exe in ("redis-cli", "valkey-cli") and any(
        a.lower() in ("flushall", "flushdb", "shutdown", "debug", "config") for a in rest
    ):
        return 4, DESTRUCTIVE, "redis-cli removes data or reconfigures the server"
    if exe in ("mongo", "mongosh") and re.search(r"(?i)drop|deleteMany|remove\(", " ".join(rest)):
        return 4, DESTRUCTIVE, "mongo drops or deletes data"
    if exe in ("psql", "sqlite3", "mysql", "mariadb", "clickhouse-client", "cockroach"):
        return (
            2,
            NETWORK if exe != "sqlite3" else MODIFY_FILES,
            f"{exe} runs SQL against a database",
        )
    if exe == "uv" and rest[:1] == ["run"]:
        inner = [a for a in rest[1:] if not a.startswith("--")]  # uv run [--project x] cmd ...
        if inner:
            found = _classify_argv(inner, depth + 1)
            return max(found[0], 1), found[1], f"uv run: {found[2]}"
        return 2, MODIFY_FILES, "uv run without a command"
    if exe in _INSTALL and rest and rest[0] in _INSTALL[exe]:
        return 2, PACKAGE_INSTALL, f"{exe} {rest[0]} fetches and installs packages"
    if exe == "uv" and (
        rest[:2] == ["pip", "install"] or rest[:1] in (["add"], ["sync"], ["tool"], ["pip"])
    ):
        return 2, PACKAGE_INSTALL, "uv installs packages"
    if exe in ("curl", "wget", "http", "https", "xh"):
        method = _method_of(rest)
        sends = method not in (None, "GET", "HEAD", "OPTIONS") or any(
            a
            in (
                "-d",
                "--data",
                "--data-raw",
                "--data-binary",
                "--data-urlencode",
                "-F",
                "--form",
                "-T",
                "--upload-file",
                "--json",
            )
            or a.startswith(("-d", "--data", "--form", "--json"))
            for a in rest
        )
        if method == "DELETE":
            return 3, NETWORK, "sends a DELETE request to a remote host"
        if sends or (
            exe == "wget" and any(a.startswith("--post") or a == "--method=POST" for a in rest)
        ):
            return 2, NETWORK, "sends data to a remote host"
        return 1, NETWORK, "downloads from a remote host"
    if exe in (
        "ssh",
        "scp",
        "rsync",
        "sftp",
        "nc",
        "ncat",
        "socat",
        "telnet",
        "ftp",
        "gh",
        "glab",
        "docker",
        "podman",
        "helm",
        "ansible",
        "ansible-playbook",
    ):
        return 2, NETWORK, f"{exe} talks to an external system"
    if exe == "find":
        if any(
            a
            in (
                "-delete",
                "-exec",
                "-execdir",
                "-ok",
                "-okdir",
                "-fprint",
                "-fprint0",
                "-fprintf",
                "-fls",
            )
            for a in rest
        ):
            return 3, DESTRUCTIVE, "find with -delete/-exec"
        return 0, READ_ONLY, "find"
    if exe == "fd" or exe == "fdfind":
        for i, a in enumerate(rest):
            if a in ("-x", "-X", "--exec", "--exec-batch"):
                found = (
                    _classify_argv(rest[i + 1 :], depth + 1)
                    if rest[i + 1 :]
                    else (2, MODIFY_FILES, "fd -x")
                )
                return max(found[0], 2), found[1], f"fd runs a command per match: {found[2]}"
        return 0, READ_ONLY, "fd only lists"
    if exe in ("perl", "ruby", "python", "python3", "node") and any(
        a.startswith("-i") for a in rest
    ):
        return 1, MODIFY_FILES, "edits files in place"
    if exe in ("tee", "touch", "mkdir", "cp", "mv", "ln", "install", "patch", "dd"):
        if exe == "dd" and any(a.startswith("of=") for a in rest):
            return (
                3 if not any(a.startswith("of=/dev/") for a in rest) else 4,
                DESTRUCTIVE,
                "dd writes its output file",
            )
        return 1, MODIFY_FILES, f"{exe} writes files"
    if exe == ":":
        return 0, READ_ONLY, "no-op"
    if exe in _R0_COMMANDS:
        writer = _read_only_with_flags(exe, rest)
        return writer or (0, READ_ONLY, f"{exe} only reads")
    if exe in ("npm", "pnpm", "yarn") and rest[:1] in (["run"], ["run-script"]):
        script = next((a for a in rest[1:] if not a.startswith("-")), "")
        if script.split(":")[0] in _SAFE_SCRIPTS:
            return 1, MODIFY_FILES, f"{exe} runs the project's {script} script"
        return 2, MODIFY_FILES, f"{exe} run {script or '?'} runs a project script of unknown effect"
    if exe in ("npm", "pnpm", "yarn") and rest[:1] in (["exec"], ["dlx"], ["x"]):
        return 2, MODIFY_FILES, f"{exe} {rest[0]} downloads and runs a package"
    if exe in _TEST_BUILD:
        subs = _TEST_BUILD[exe]
        if subs is None or any(a in subs for a in rest[:3]):
            return 1, MODIFY_FILES, f"{exe} runs project tests or builds"
        return 2, MODIFY_FILES, f"{exe} runs project code of unknown effect"
    if exe in (
        "python",
        "python3",
        "node",
        "ruby",
        "perl",
        "bash",
        "sh",
        "uv",
        "npx",
        "deno",
        "bun",
        "php",
    ):
        return 2, MODIFY_FILES, f"{exe} runs code of unknown effect"
    return 2, MODIFY_FILES, "unrecognised command (unknown, may modify anything)"


def _mark_newlines(command: str) -> str:
    """Turn unquoted newlines into `;` so each line is classified; quoted ones stay as they are."""
    out, quote, escaped = [], "", False
    for ch in command:
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
    return "".join(out)


def _tokens(command: str) -> list[str] | None:
    try:
        lexer = shlex.shlex(_mark_newlines(command), posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _segments(tokens: list[str]) -> tuple[list[list[str]], bool]:
    """Split on operators and group punctuation; report whether output goes to a file."""
    segments: list[list[str]] = [[]]
    redirects = False
    skip_target = False
    for token in tokens:
        if skip_target:  # the file after a redirection is not a command word
            skip_target = False
            continue
        if token in _OPERATORS or (token and set(token) <= set("&|;")) or set(token) <= set("()"):
            segments.append([])
        elif _REDIRECT.match(token):
            redirects = True
            skip_target = True
        elif token in ("<", "<<", "<<<") or re.fullmatch(r"[0-9]*>&[0-9-]*|[0-9]*<&[0-9-]*", token):
            skip_target = token in ("<", "<<", "<<<")
        else:
            segments[-1].append(token)
    return [seg for seg in segments if seg], redirects


def _redirect_targets_are_harmless(tokens: list[str]) -> bool:
    """`> /dev/null` and `2>&1` do not modify files."""
    for i, token in enumerate(tokens):
        if _REDIRECT.match(token):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            if (
                target not in ("/dev/null", "/dev/stderr", "/dev/stdout")
                and not target.startswith("&")
                and not target.isdigit()  # 2>&1: duplicating a descriptor
                and target != "-"
            ):
                return False
    return True


def classify_command(command: str, *, _depth: int = 0) -> Classification:
    """Deterministic command risk class R0-R4 and spec §27 category. Compound commands take the
    highest segment; unknown or unparseable input is classified higher, never lower (ADR-031).
    Heuristic by nature: a shell string is not fully parseable."""
    if not isinstance(command, str) or not command.strip():
        return Classification("R0", READ_ONLY, ("empty command",))
    best: Classification | None = None
    for pattern, level, cat, why in _RAW_RULES:
        if pattern.search(command):
            best = _bump(best, level, cat, why)
    tokens = _tokens(re.sub(r"[<>]+\([^()]*\)", " __procsub__ ", command))
    if tokens is None:  # unbalanced quotes: classify the pieces crudely, and never below R2
        best = _bump(best, 2, MODIFY_FILES, "unparseable command (assumed risky)")
        pieces = re.split(r"[;&|\n(){}]+", command.replace("'", " ").replace('"', " "))
        segments = [piece.split() for piece in pieces if piece.strip()]
        redirects = False
    else:
        segments, redirects = _segments(tokens)
        if redirects and not _redirect_targets_are_harmless(tokens):
            best = _bump(best, 1, MODIFY_FILES, "redirects output to a file")
            if all(seg[0] in (":", "true", "cat") and len(seg) == 1 for seg in segments):
                best = _bump(best, 2, MODIFY_FILES, "truncates or overwrites a file")
    for direction, inner in re.findall(
        r"([<>]+)\(([^()]*)\)", command
    ):  # <(cmd) and >(cmd) run `cmd`
        found = _inner(inner, _depth)
        floor = 2 if ">" in direction else 0
        level = max(found[0] if found else 0, floor)
        if level:
            best = _bump(
                best,
                level,
                found[1] if found and found[0] > 1 else MODIFY_FILES,
                "process substitution: " + (found[2] if found else "writes to a command"),
            )
    for sub in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        found = _inner(sub[0] or sub[1], _depth)
        if found:
            category = found[1] if found[0] > 1 else MODIFY_FILES
            best = _bump(best, max(found[0], 1), category, "command substitution: " + found[2])
    for argv in segments:
        while argv and (argv[0] in _LEAD_WORDS or argv[0] == "{"):
            argv = argv[1:]
        if not argv or argv[0] in _SKIP_WORDS:
            continue
        if argv[0] in ("for", "select", "case", "function", "in"):
            continue  # a loop header or a case pattern runs nothing by itself
        argv = [t for t in argv if t not in ("}",)]
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

_SECRET_NAME = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIAL|PRIVATE|AUTH|DSN|COOKIE|SESSION"
    r"|DATABASE_URL|CONNECTION_STRING|SIGNATURE|(?:^|_)(?:PAT|PASS|PWD)(?:_|$)",
    re.I,
)
_CLEARLY_SECRET = re.compile(
    r"PASSWORD|PASSWD|SECRET|TOKEN|PASSPHRASE|PRIVATE|(?:^|_)(?:PAT|PASS|PWD)(?:_|$)", re.I
)
# No HOME: a recorded command must not find the user's dotfiles, credentials or tool configs there.
SAFE_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR", "TZ", "USER", "LOGNAME")
_REF = re.compile(r"^[A-Za-z0-9._/][A-Za-z0-9._/@+-]{0,199}$")


# Host variables that carry no secret by their nature. Everything else in the host's environment is
# masked by VALUE, whatever its name: `ps eww -p $PPID` and `/proc/$PPID/environ` print the whole
# environment, and a name like SMTP_PW or HF_TOK is not something a pattern can be relied on to
# recognise (ADR-031).
_BENIGN_ENV = re.compile(
    r"^(?:PATH|HOME|SHELL|USER|USERNAME|LOGNAME|LANG|LANGUAGE|LC_\w+|TERM|TERM_\w+|PWD|"
    r"OLDPWD|TMPDIR|TMP|TEMP|"
    r"EDITOR|VISUAL|PAGER|DISPLAY|SHLVL|COLORTERM|COLUMNS|LINES|XDG_\w+|_|HOSTNAME|HOST|"
    r"MAIL|TZ|PS\d|IFS|"
    r"LS_COLORS|LSCOLORS|CLICOLOR\w*|NO_COLOR|FORCE_COLOR|CI|OSTYPE|MACHTYPE|HOSTTYPE|"
    r"NODE_ENV|PYTHON\w*|"
    r"VIRTUAL_ENV|CONDA_\w+|JAVA_HOME|GOPATH|GOROOT|GOFLAGS|CARGO_HOME|RUSTUP_HOME|NVM_\w+|"
    r"COMMAND_MODE|"
    r"DBUS_\w+|XPC_\w+|__CF_\w+|APPLE_\w+|ITERM_\w+|TERM_PROGRAM\w*|WINDOWID|LAUNCHINSTANCEID|"
    r"GITHUB_(?:WORKSPACE|ACTIONS|JOB|RUN_\w+|REPOSITORY\w*|REF\w*|SHA|EVENT_\w+|SERVER_URL|"
    r"API_URL|ACTOR|"
    r"WORKFLOW\w*)|RUNNER_\w+|ABB_ENVIRONMENT)$",
    re.I,
)
_PLAIN_VALUE = re.compile(
    r"^(?:[\d.:,/+-]+|true|false|yes|no|on|off|null|none|development|production|test)$", re.I
)


def _host_value_is_harmless(value: str) -> bool:
    """Paths, plain URLs, numbers and ordinary words: masking them would corrupt all output."""
    if value.startswith(("/", "~", "./", "../")):
        return True
    if re.match(r"^https?://[^/@\s]+(?:/[^\s@]*)?$", value):
        return True  # a URL without credentials
    return bool(_PLAIN_VALUE.match(value))


def secret_env_values(
    environ: Mapping[str, str] | None = None, extra: tuple[str, ...] = (), min_len: int = 8
) -> tuple[str, ...]:
    """Values of the host environment to mask exactly, longest first.

    Names that look secret are masked from 6 characters (8 otherwise). Any OTHER variable is masked
    too, unless it is on a short benign list or its value is plainly harmless (a path, a number, a
    plain URL).
    """
    source = os.environ if environ is None else environ
    values: set[str] = set()
    for key, value in source.items():
        if not isinstance(value, str) or not value:
            continue
        if _SECRET_NAME.search(key):
            if len(value) >= (6 if _CLEARLY_SECRET.search(key) else min_len):
                values.add(value)
        elif (
            len(value) >= min_len
            and not _BENIGN_ENV.match(key)
            and not _host_value_is_harmless(value)
        ):
            values.add(value)
    values.update(v for v in extra if isinstance(v, str) and len(v) >= min_len)
    return tuple(sorted(values, key=len, reverse=True))


def mask_values(text: str, values: tuple[str, ...], marker: str = "[REDACTED:env]") -> str:
    return mask_text(text, values, marker)


def safe_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The only environment a recorded command sees: no inherited credentials."""
    env = {k: os.environ[k] for k in SAFE_ENV if k in os.environ}
    env.setdefault("PATH", os.defpath)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra or {})
    return env


def _valid_ref(value: str, what: str) -> str:
    if not _REF.fullmatch(value) or ".." in value or value.endswith((".lock", "/")):
        raise ValueError(f"unsafe {what}: {value!r}")
    return value


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
CUT_SLACK = 16 * 1024  # captured past the limit so redaction sees a whole secret before the cut


def _cut(text: str, limit: int) -> str:
    """At most `limit` bytes of text, never splitting a character."""
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", errors="ignore")


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
        self._warned_incomplete = False
        self._files = SecretFiles(self.root)
        self._file_secrets: tuple[str, ...] = ()
        self.refresh_secrets()

    def refresh_secrets(self) -> None:
        """Learn secret values in the workspace's sensitive files (cached by size and mtime)."""
        try:
            self._file_secrets = self._files.refresh()
            if self._files.incomplete and not self._warned_incomplete:
                self._warned_incomplete = True  # once, value-free: some secret files may be unknown
                logging.getLogger("blackbox").warning(
                    "blackbox: secret scan hit its time budget; some secret files may not be masked"
                )
        except (
            Exception
        ):  # scanning must never break the agent (INV-4); masking just uses what it had
            self.bb.stats_.add("internal_errors")

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
        """Everything that leaves the recorder passes here: ANSI off, values masked, redacted."""
        text = mask_values(strip_ansi(text), self._secrets)
        text = mask_values(text, self._file_secrets, "[REDACTED:file]")
        return self.bb.redact_text(text)

    # -- files -------------------------------------------------------------------------------------

    def read_file(
        self, path: str, max_bytes: int | None = 256 * 1024, *, redact: bool = True
    ) -> str:
        """Read a file for the agent. The text is redacted by default (the model must not see
        secrets either); pass `redact=False` and `max_bytes=None` only to edit the real content."""
        full = self.resolve(path)
        data = full.read_bytes()
        self.run.event(
            "file.read",
            {
                "file.path": self.sanitize(self.rel(full)),
                "file.operation": "read",
                "file.size_after": len(data),
                "file.hash_after": _sha(data), **self._lang(path),
            },
        )  # fmt: skip
        # Redact the whole text, then cut: a secret straddling the cut must not leave a prefix.
        raw = data if max_bytes is None else data[: max_bytes + CUT_SLACK]
        text = raw.decode("utf-8", errors="replace")
        if redact:
            self.refresh_secrets()
            text = self.sanitize(text)
        return text if max_bytes is None else _cut(text, max_bytes)

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
        mode = stat.S_IMODE(full.stat().st_mode) if old is not None else 0o644
        fd, tmp = tempfile.mkstemp(dir=full.parent, prefix=".abb-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(new)
        os.chmod(tmp, mode)  # mkstemp creates 0600; keep the file's own mode (executables stay so)
        os.replace(tmp, full)
        attrs: dict[str, object] = {
            "file.path": self.sanitize(rel),
            "file.operation": "created" if old is None else "modified",
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
            "file.path": self.sanitize(rel),
            "file.operation": "deleted",
            "file.size_before": len(old),
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
        self.refresh_secrets()
        diff = self.sanitize(_diff_text(rel, before, after))
        added, removed = _count_changes(diff)
        attrs["file.lines_added"], attrs["file.lines_removed"] = added, removed
        artifact = self.bb.upload_artifact(
            diff, kind="diff", name=self.sanitize(rel)[:120], run=self.run
        )
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
        self.refresh_secrets()
        known_before = list(self._files.paths)
        label = self.root_label + ("" if work == self.root else "/" + self.rel(work))
        verdict = self.classify(command)
        # Sanitize BEFORE cutting to the attribute length: a cut must not leave a secret's prefix.
        shown = self.sanitize(command)
        label = self.sanitize(label)
        span = self.run.span(
            shown, kind="shell",
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
        self.refresh_secrets()  # the command may have written a secret file
        cwd_rel = self.rel(work)
        withheld = command_may_reach_secrets(
            command, sorted(set(known_before) | set(self._files.paths)), cwd_rel
        )
        if (
            withheld
        ):  # `cat .env`, `bash -c`, `grep -r`: neither the record nor the agent gets the output
            out = err = "[output withheld: the command may print a file that holds secrets]\n"
        stdout = _cut(self.sanitize(withhold_sensitive_hunks(out)), self._limit)
        stderr = _cut(self.sanitize(withhold_sensitive_hunks(err)), self._limit)
        summary = parse_test_output(stdout + "\n" + stderr)
        attrs: dict[str, object] = {
            "shell.cwd": label,
            "shell.risk_class": verdict.risk_class,
            "shell.category": verdict.category,
            "shell.exit_code": code, "shell.duration_ms": round(duration, 3),
            "shell.stdout_bytes": total_out, "shell.stderr_bytes": total_err,
            "shell.output_truncated": total_out > self._limit or total_err > self._limit,
        }  # fmt: skip
        if withheld:
            attrs["shell.output_withheld"] = "may_reach_secrets"
        for stream, text, size in (("stdout", stdout, total_out), ("stderr", stderr, total_err)):
            if size and not withheld:
                artifact = self.bb.upload_artifact(text, kind=stream, name=stream, run=self.run)
                if artifact:
                    attrs[f"shell.{stream}_artifact"] = f"artifact://{artifact}"
        if summary is not None:
            attrs.update(
                {
                    "test.framework": summary.framework, "test.suite": self.sanitize(cwd)[:200],
                    "test.total": summary.total, "test.passed": summary.passed,
                    "test.failed": summary.failed, "test.skipped": summary.skipped,
                    "test.failing": list(summary.failing),
                }
            )  # fmt: skip
        span.set_attributes(attrs)
        if timed_out:
            self.run.event(
                "timeout.occurred",
                {"timeout.operation": shown[:200], "timeout.limit_ms": limit * 1000},
            )
        span.end("success" if code == 0 else "error")
        return CommandResult(shown, code, stdout, stderr, duration, verdict, timed_out, summary)

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
                room = self._limit + CUT_SLACK - len(kept[key])
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
        _valid_ref(name, "branch name")  # a flag-like value must never reach git
        base = self._head()
        result = self._git("checkout", "-b", name)
        if result.ok:
            attrs: dict[str, object] = {"git.branch": self.sanitize(name)}
            if base:
                attrs["git.base_commit"] = base
            self.run.event("git.branch_created", attrs)
        return result

    def git_diff(self) -> str:
        """The working-tree diff, without files that hold secrets; recorded as a git.diff event."""
        result = self._git("diff", "--no-color", "--", ".", *git_excludes())
        names = self._quiet("git diff --name-only")
        files = [n for n in names.splitlines() if n and not is_sensitive_path(n)]
        attrs: dict[str, object] = {
            "git.changed_files": len(files),
            "git.diff_stat_files": len(files),
        }
        if result.stdout:  # already sanitized and hunk-filtered by run_command
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
            files = [
                n
                for n in self._quiet("git show --name-only --format=").splitlines()
                if n and not is_sensitive_path(n)
            ]
            attrs: dict[str, object] = {"git.changed_files": len(files)}
            if head:
                attrs["git.commit_hash"] = attrs["git.head_commit"] = head
            branch = self._quiet("git rev-parse --abbrev-ref HEAD")
            if branch:
                attrs["git.branch"] = self.sanitize(branch)
            self.run.event("git.commit", attrs)
        return result

    def git_push(self, remote: str, branch: str) -> CommandResult:
        _valid_ref(remote, "remote name")
        _valid_ref(branch, "branch name")
        result = self._git("push", "-u", remote, branch)
        if result.ok:
            self.run.event(
                "git.push",
                {
                    "git.push_target": self.sanitize(f"{remote} {branch}"),
                    "git.branch": self.sanitize(branch),
                },
            )
        return result
