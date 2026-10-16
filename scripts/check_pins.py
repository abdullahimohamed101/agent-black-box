#!/usr/bin/env python3
"""Fail when a GitHub Action or a container image is not pinned immutably (KI-024, spec §127).

usage: check_pins.py [ROOT]            (default: the repository root)

Checks, over git-tracked files (or every file when ROOT is not a git checkout):
  * workflows and composite actions: every `uses:` is `owner/repo@<40 hex> # <tag>`; local `./`
    paths are exempt; `docker://` references need `@sha256:`; the trailing tag comment is required
    so Dependabot and humans can read the version;
  * Dockerfiles: every `FROM` and `COPY --from=` image carries `@sha256:<64 hex>`; earlier stage
    names and `scratch` are not images;
  * compose files and workflows: every `image:` and `container:` value carries a digest;
  * `docker run|pull|create <image>` in shell scripts, Makefiles and workflow steps;
  * a tag-pinned reference of an image that is pinned elsewhere, written without its digest in
    any script or config (the literal `postgres:16-alpine` in a helper script).
A digest that appears only in a comment does not count. Reviewed exceptions live in
scripts/pins-allowlist.txt as `reference | reason` lines. Stdlib only.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SHA40 = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
IMAGE_KEY = re.compile(r"^\s*(?:-\s*)?(?:image|container):\s*(?P<ref>[^\s#]+)")
FROM = re.compile(
    r"^\s*FROM\s+(?P<flags>(?:--\S+\s+)*)(?P<ref>\S+)(?:\s+AS\s+(?P<name>\S+))?", re.I
)
COPY_FROM = re.compile(r"^\s*COPY\s+(?:--\S+\s+)*?--from=(?P<ref>\S+)", re.I)
DOCKER_CMD = re.compile(r"\bdocker\s+(?:run|pull|create)\b(?P<args>[^\n]*)")
# Flags of `docker run` that take a separate value; everything else starting with '-' is a switch.
VALUE_FLAGS = {
    "-e", "--env", "-v", "--volume", "-p", "--publish", "-u", "--user", "-w", "--workdir", "--name",
    "--entrypoint", "--network", "--net", "--platform", "--env-file", "--mount", "--add-host", "-l",
    "--label", "--memory", "-m", "--cpus", "--restart", "--pull", "-h", "--hostname", "--tmpfs",
    "--cap-add", "--cap-drop", "--security-opt", "--user-ns", "--health-cmd",
}  # fmt: skip
SCANNED_SUFFIXES = (".py", ".sh", ".yml", ".yaml", ".toml", ".json")
SKIP_DIRS = {".git", "node_modules", ".venv", ".next", "__pycache__", ".mypy_cache", ".ruff_cache"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reference: str
    problem: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.problem}: {self.reference}"


def tracked_files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607
            capture_output=True, check=True, timeout=30,
        ).stdout.decode()  # fmt: skip
        return [root / name for name in out.split("\0") if name and (root / name).is_file()]
    found: list[Path] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found.extend(Path(base, name) for name in names)
    return found


def load_allowlist(path: Path) -> set[str]:
    allowed: set[str] = set()
    if not path.exists():
        return allowed
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        reference, _, reason = (part.strip() for part in line.partition("|"))
        if not reference or not reason:
            raise SystemExit(f"{path}:{number}: expected 'reference | reason'")
        allowed.add(reference)
    return allowed


def _is_workflow(rel: str) -> bool:
    return rel.startswith(".github/workflows/") or (
        rel.startswith(".github/actions/") and rel.endswith(("action.yml", "action.yaml"))
    )


def _is_dockerfile(name: str) -> bool:
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def _is_compose(name: str) -> bool:
    return bool(re.fullmatch(r"(docker-)?compose([.-].*)?\.ya?ml", name))


def check_uses(rel: str, lines: list[str]) -> list[Finding]:
    found = []
    for number, line in enumerate(lines, 1):
        match = USES.match(line)
        if not match:
            continue
        ref = match["ref"].strip("'\"")
        if ref.startswith("./"):
            continue
        if ref.startswith("docker://"):
            if not DIGEST.search(ref):
                found.append(Finding(rel, number, ref, "docker:// action without @sha256 digest"))
            continue
        _, _, version = ref.partition("@")
        if not SHA40.match(version):
            found.append(Finding(rel, number, ref, "action is not pinned to a 40-hex commit sha"))
        elif not re.match(r"\s*#\s*\S+", match["rest"]):
            found.append(
                Finding(rel, number, ref, "pinned action lacks the trailing '# <tag>' comment")
            )
    return found


def _strip_comment(line: str) -> str:
    return re.split(r"\s#", line, maxsplit=1)[0]


def check_dockerfile(rel: str, lines: list[str]) -> list[Finding]:
    found = []
    stages: set[str] = set()
    for number, line in enumerate(lines, 1):
        code = _strip_comment(line)
        match = FROM.match(code)
        if match:
            ref = match["ref"]
            if ref.lower() != "scratch" and ref not in stages and not DIGEST.search(ref):
                found.append(Finding(rel, number, ref, "FROM image without @sha256 digest"))
            if match["name"]:
                stages.add(match["name"])
            continue
        copy = COPY_FROM.match(code)
        if copy and copy["ref"] not in stages and not copy["ref"].isdigit():
            if not DIGEST.search(copy["ref"]):
                found.append(
                    Finding(rel, number, copy["ref"], "COPY --from image without @sha256 digest")
                )
    return found


def check_image_keys(rel: str, lines: list[str]) -> list[Finding]:
    found = []
    for number, line in enumerate(lines, 1):
        match = IMAGE_KEY.match(line)
        if match and not DIGEST.search(match["ref"].strip("'\"")):
            found.append(Finding(rel, number, match["ref"], "image without @sha256 digest"))
    return found


def check_docker_commands(rel: str, lines: list[str]) -> list[Finding]:
    found = []
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            continue
        match = DOCKER_CMD.search(line)
        if not match:
            continue
        tokens = match["args"].replace('"', " ").replace("'", " ").split()
        skip = False
        for token in tokens:
            if skip:
                skip = False
            elif token.startswith("-"):
                skip = token in VALUE_FLAGS
            elif token.startswith(("$", "\\", "&", "|", ";", ">")):
                break  # a variable image or the end of the command: not checkable statically
            else:
                if not DIGEST.search(token):
                    found.append(
                        Finding(rel, number, token, "docker run/pull image without @sha256 digest")
                    )
                break
    return found


def pinned_tag_references(files: list[tuple[str, list[str]]]) -> set[str]:
    """`repo:tag` of every digest-pinned image: the names to look for written without a digest."""
    names: set[str] = set()
    for _, lines in files:
        for line in lines:
            for match in re.finditer(
                r"([a-z0-9][a-z0-9./_-]*:[A-Za-z0-9._-]+)@sha256:[0-9a-f]{64}", line
            ):
                names.add(match.group(1))
    return names


def check_stray_literals(rel: str, lines: list[str], names: set[str]) -> list[Finding]:
    found = []
    for number, line in enumerate(lines, 1):
        for name in names:
            for match in re.finditer(
                r"(?<![A-Za-z0-9./_:-])" + re.escape(name) + r"(?![A-Za-z0-9._-])", line
            ):
                if not line[match.end() :].startswith("@sha256:"):
                    found.append(
                        Finding(rel, number, name, "pinned image named again without its digest")
                    )
    return found


def run_checks(root: Path, allowlist: set[str] | None = None) -> list[Finding]:
    root = root.resolve()
    if allowlist is None:
        allowlist = load_allowlist(root / "scripts" / "pins-allowlist.txt")
    texts: list[tuple[str, list[str]]] = []
    for path in tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("docs/") or rel.endswith("-lock.yaml") or path.name == "pnpm-lock.yaml":
            continue
        if path.name == "Makefile" or path.suffix in SCANNED_SUFFIXES or _is_dockerfile(path.name):
            try:
                texts.append((rel, path.read_text(encoding="utf-8").splitlines()))
            except UnicodeDecodeError:
                continue
    findings: list[Finding] = []
    for rel, lines in texts:
        name = rel.rsplit("/", 1)[-1]
        if _is_workflow(rel):
            findings += check_uses(rel, lines) + check_image_keys(rel, lines)
            findings += check_docker_commands(rel, lines)
        elif _is_dockerfile(name):
            findings += check_dockerfile(rel, lines)
        elif _is_compose(name):
            findings += check_image_keys(rel, lines)
        if name == "Makefile" or rel.endswith(".sh"):
            findings += check_docker_commands(rel, lines)
    names = pinned_tag_references(texts)
    for rel, lines in texts:
        if rel.startswith("scripts/check_pins") or rel == "scripts/pins-allowlist.txt":
            continue
        findings += check_stray_literals(rel, lines, names)
    unique = sorted(set(findings), key=lambda f: (f.path, f.line, f.reference))
    return [f for f in unique if f.reference not in allowlist]


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent
    findings = run_checks(root)
    for finding in findings:
        print(f"FAIL {finding}")
    if findings:
        print(
            f"check-pins: {len(findings)} unpinned reference(s). See docs/runbooks/bumping-pins.md."
        )
        return 1
    print("check-pins: OK (every action pinned by sha, every image by digest)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
