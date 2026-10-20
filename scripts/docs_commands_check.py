#!/usr/bin/env python3
"""Keep the command blocks in the user-facing docs honest (Phase 20, D14).

usage: docs_commands_check.py [--no-run] [FILE ...]
       (default FILES: docs/README.md docs/quickstart.md docs/deployment.md docs/troubleshooting.md docs/hosting-guide.md)

Every fenced `bash`/`sh`/`console` block is checked by what precedes it:

  <!-- check:run -->          the block is EXECUTED (bash -e, repository root, no BLACKBOX_* variables).
                             Only safe commands are allowed: offline, idempotent, no stack, no secrets
                             printed. An allow-list of command words and a deny-list of substrings decide;
                             a block that needs anything else is refused, not run.
  <!-- check:skip: reason --> the block is not checked (the reason is mandatory and is printed).
  (nothing)                  the block is only REFERENCE-checked, never run: every `make` target exists in
                             the Makefile, every repository path exists, every `docker compose --profile`
                             exists in docker-compose.yml, every `abb_api.cli` subcommand exists, and every
                             ABB_* variable is known to .env.example, docker-compose.yml or the API source.

`make demo` is being built (plan step 10). The marker `[CONFIRM AFTER STEP 10]` in the block or in the three
lines above it tolerates ONLY a missing `demo`/`demo-reset` target; once the target exists the marker is
reported as stale and must be removed.

The four user-facing docs are also linted: no placeholders (TODO, FIXME, lorem, NEEDS INPUT) and none of the
claims the release plan forbids (production-ready, SOC 2, enterprise, SaaS, "open source", "self-hosted").
Stdlib only; works on Python 3.9+. Exit 0 when everything holds, 1 otherwise.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILES = ["docs/README.md", "docs/quickstart.md", "docs/deployment.md", "docs/troubleshooting.md", "docs/hosting-guide.md"]
LANGS = {"bash", "sh", "shell", "console"}
CONFIRM = "[CONFIRM AFTER STEP 10]"
CONFIRM_TARGETS = {"demo", "demo-reset"}
RUN_MARK = re.compile(r"^\s*<!--\s*check:run\s*-->\s*$")
SKIP_MARK = re.compile(r"^\s*<!--\s*check:skip:\s*(\S.*?)\s*-->\s*$")
FENCE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)")

ALLOWED_WORDS = {"openssl", "uv", "python3", "python", "echo", "test", "ls", "export", "true", "printf"}
DENIED = ("docker", "curl", "wget", "sudo", "rm ", "git ", "make", "kill", "ssh", "pip ", "npm", "pnpm",
          "brew", "apt", "chmod", "chown", "mv ", "dd ", ".env", "dev-api-key", "cat ")
RUN_TIMEOUT_SECONDS = 600

PATH_PREFIXES = ("scripts/", "examples/", "docs/", "apps/", "packages/", "integrations/", "deploy/",
                 "infrastructure/")
PATH_NAMES = {".env.example", "docker-compose.yml", "Makefile"}

LINT_FILES = {"docs/README.md", "docs/quickstart.md", "docs/deployment.md", "docs/troubleshooting.md", "docs/hosting-guide.md"}
LINT_PATTERNS = [
    (re.compile(r"TODO|FIXME|lorem|NEEDS INPUT", re.I), "placeholder"),
    (re.compile(r"production[- ]ready|SOC ?2|enterprise|\bSaaS\b", re.I), "claim the release plan forbids"),
    (re.compile(r"open[- ]source|self-hosted", re.I), "claim not yet allowed (claims table)"),
]


class Block:
    def __init__(self, path: Path, line: int, mode: str, reason: str, text: str, confirm: bool) -> None:
        self.path, self.line, self.mode, self.reason, self.text, self.confirm = path, line, mode, reason, text, confirm

    @property
    def where(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def parse_blocks(path: Path) -> list[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        match = FENCE.match(lines[i])
        if not match:
            i += 1
            continue
        fence, lang = match.group(2), match.group(3).lower()
        start = i
        body: list[str] = []
        i += 1
        while i < len(lines) and not lines[i].lstrip().startswith(fence[0] * len(fence)):
            body.append(lines[i])
            i += 1
        i += 1
        if lang not in LANGS:
            continue
        j = start - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        mode, reason = "reference", ""
        if j >= 0 and RUN_MARK.match(lines[j]):
            mode = "run"
        elif j >= 0 and SKIP_MARK.match(lines[j]):
            mode, reason = "skip", SKIP_MARK.match(lines[j]).group(1)  # type: ignore[union-attr]
        near = "\n".join(lines[max(0, start - 3) : start] + body)
        blocks.append(Block(path, start + 1, mode, reason, "\n".join(body), CONFIRM in near))
    return blocks


def logical_lines(text: str) -> list[str]:
    out: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        line = (pending + line).strip()
        pending = ""
        if line.startswith("$ "):
            line = line[2:]
        if line and not line.startswith("#"):
            out.append(re.sub(r"\s+#\s.*$", "", line))
    return out


class Repo:
    """What the docs may refer to, read once from the repository."""

    def __init__(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.targets = set(re.findall(r"^([A-Za-z0-9_.-]+):", makefile, re.M))
        for phony in re.findall(r"^\.PHONY:(.*)$", makefile, re.M):
            self.targets.update(phony.split())
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.profiles = set()
        for group in re.findall(r"profiles:\s*\[([^\]]*)\]", compose):
            self.profiles.update(re.findall(r"[\"']?([A-Za-z0-9_-]+)[\"']?", group))
        cli = (ROOT / "apps/api/src/abb_api/cli.py").read_text(encoding="utf-8")
        self.cli_commands = set(re.findall(r"add_parser\(\s*\"([a-z0-9-]+)\"", cli))
        known = (ROOT / ".env.example").read_text(encoding="utf-8") + compose
        # The hosting recipe (Phase 23) names variables the development files do not: its template and overlays.
        for extra in [ROOT / ".env.production.example", *(ROOT / "deploy/hosting").rglob("*.yml"), *(ROOT / "deploy/hosting").rglob("*.example"),
                      *(ROOT / "deploy/hosting/systemd").glob("*"), *(ROOT / "scripts").glob("hosting-*.sh")]:
            if extra.is_file():
                known += extra.read_text(encoding="utf-8", errors="replace")
        for source in (ROOT / "apps/api/src").rglob("*.py"):
            known += source.read_text(encoding="utf-8", errors="replace")
        self.env_names = set(re.findall(r"ABB_[A-Z0-9_]+", known))


def check_reference(block: Block, repo: Repo) -> tuple[list[str], int]:
    problems: list[str] = []
    tolerated = 0
    for name in sorted(set(re.findall(r"\bABB_[A-Z0-9_]+\b", block.text))):
        if name not in repo.env_names:
            problems.append(f"{block.where}: unknown environment variable {name}")
    for line in logical_lines(block.text):
        try:
            words = shlex.split(line, comments=False, posix=True)
        except ValueError:
            words = line.split()
        for index, word in enumerate(words):
            if word == "make":
                for target in words[index + 1 :]:
                    if target.startswith("-") or "=" in target or target in ("&&", "||", ";", "|"):
                        break
                    if target in repo.targets:
                        if block.confirm and target in CONFIRM_TARGETS:
                            problems.append(f"{block.where}: `make {target}` exists now; remove the {CONFIRM} marker")
                        continue
                    if block.confirm and target in CONFIRM_TARGETS:
                        tolerated += 1
                        continue
                    problems.append(f"{block.where}: make target '{target}' does not exist in the Makefile")
            if word == "--profile" and index + 1 < len(words) and words[index + 1] not in repo.profiles:
                problems.append(f"{block.where}: compose profile '{words[index + 1]}' is not in docker-compose.yml")
            if word == "abb_api.cli" and index + 1 < len(words) and words[index - 1] == "-m":
                sub = words[index + 1]
                if not sub.startswith("-") and sub not in repo.cli_commands:
                    problems.append(f"{block.where}: abb_api.cli has no subcommand '{sub}'")
            candidate = word.split("=", 1)[-1] if word.startswith("--") else word
            candidate = candidate.rstrip(",;:")
            if candidate.startswith(PATH_PREFIXES) or candidate in PATH_NAMES:
                if any(ch in candidate for ch in "<>$*{}[]|"):
                    continue
                if not (ROOT / candidate).exists():
                    problems.append(f"{block.where}: path does not exist: {candidate}")
    return problems, tolerated


def run_refusal(block: Block) -> str | None:
    """Why a check:run block may not run, or None when every command is on the allow-list."""
    for denied in DENIED:
        if denied in block.text:
            return f"contains '{denied.strip()}'"
    for line in logical_lines(block.text):
        for segment in re.split(r"\|\||&&|\||;|\$\(|\)|`", line):
            segment = segment.strip().strip("\"'").strip()
            if not segment:
                continue
            words = segment.split()
            while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words = words[1:]  # NAME=value prefixes
            if words and words[0] == "export":
                words = words[1:]
                while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                    words = words[1:]
            if words and words[0] not in ALLOWED_WORDS:
                return f"command '{words[0]}' is not on the allow-list"
    return None


def run_block(block: Block) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BLACKBOX_", "ABB_", "ANTHROPIC_", "OPENAI_"))}
    try:
        done = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", block.text],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {RUN_TIMEOUT_SECONDS} s"
    tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-6:])
    return done.returncode == 0, f"exit {done.returncode}" + (f"; last output:\n{tail}" if done.returncode else "")


def lint(path: Path) -> list[str]:
    rel = str(path.relative_to(ROOT))
    if rel not in LINT_FILES:
        return []
    problems = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if CONFIRM in line:
            continue
        for pattern, label in LINT_PATTERNS:
            if pattern.search(line):
                problems.append(f"{rel}:{number}: {label}: {pattern.search(line).group(0)!r}")  # type: ignore[union-attr]
    return problems


def main(argv: list[str]) -> int:
    no_run = "--no-run" in argv
    names = [a for a in argv if not a.startswith("--")] or DEFAULT_FILES
    files = [(ROOT / n if not Path(n).is_absolute() else Path(n)) for n in names]
    files = [f for f in files if f.is_file()]
    if not files:
        print("no documentation files found")
        return 1
    repo = Repo()
    problems: list[str] = []
    counts = {"run": 0, "reference": 0, "skip": 0, "tolerated": 0}
    for path in files:
        problems.extend(lint(path))
        for block in parse_blocks(path):
            counts[block.mode] += 1
            if block.mode == "skip":
                print(f"skip   {block.where}: {block.reason}")
            elif block.mode == "reference":
                found, tolerated = check_reference(block, repo)
                problems.extend(found)
                counts["tolerated"] += tolerated
            else:
                refusal = run_refusal(block)
                if refusal:
                    problems.append(f"{block.where}: check:run block refused: {refusal}")
                    continue
                found, _ = check_reference(block, repo)
                problems.extend(found)
                if no_run:
                    print(f"norun  {block.where}")
                    continue
                ok, detail = run_block(block)
                print(f"{'ok    ' if ok else 'FAILED'} {block.where}: {detail}")
                if not ok:
                    problems.append(f"{block.where}: check:run block failed")
    for problem in problems:
        print(problem)
    print(
        f"docs commands: {counts['run']} run, {counts['reference']} reference-checked, {counts['skip']} skipped, "
        f"{counts['tolerated']} step-10 reference(s) tolerated; {len(problems)} problem(s)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
